#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  sudo bash scripts/install-ubuntu.sh --controller https://swarm.example.com --token YOUR_ENROLLMENT_TOKEN [options]

Options:
  --controller URL     Controller URL (required)
  --token TOKEN        Enrollment token (required)
  --user USER          Service user (defaults to invoking sudo user/current user)
  --onnx cpu|gpu|none  Optional ONNX Runtime backend (default: none)
  --cuda 12|13|none    Optional CuPy CUDA backend (default: none)
  --branch NAME        Git branch to install (default: agent/universal-compute-swarm)
EOF
}

CONTROLLER=""
TOKEN=""
TARGET_USER="${SUDO_USER:-$USER}"
ONNX="none"
CUDA="none"
BRANCH="agent/universal-compute-swarm"
REPO_URL="https://github.com/CaMaLabs/Android-compute-cluster.git"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --controller) CONTROLLER="$2"; shift 2 ;;
    --token) TOKEN="$2"; shift 2 ;;
    --user) TARGET_USER="$2"; shift 2 ;;
    --onnx) ONNX="$2"; shift 2 ;;
    --cuda) CUDA="$2"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

[[ -n "$CONTROLLER" && -n "$TOKEN" ]] || { usage; exit 2; }
id "$TARGET_USER" >/dev/null 2>&1 || { echo "User not found: $TARGET_USER" >&2; exit 1; }

if [[ $EUID -ne 0 ]]; then
  echo "Run this installer with sudo." >&2
  exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y git python3 python3-venv python3-pip ca-certificates curl

TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
INSTALL_DIR="$TARGET_HOME/.local/share/compute-swarm"
REPO_DIR="$INSTALL_DIR/repo"
VENV_DIR="$INSTALL_DIR/venv"
CONFIG_DIR="$TARGET_HOME/.config/compute-swarm"
ENV_FILE="$CONFIG_DIR/worker.env"

install -d -o "$TARGET_USER" -g "$TARGET_USER" "$INSTALL_DIR" "$CONFIG_DIR"

if [[ -d "$REPO_DIR/.git" ]]; then
  sudo -u "$TARGET_USER" git -C "$REPO_DIR" fetch origin "$BRANCH"
  sudo -u "$TARGET_USER" git -C "$REPO_DIR" checkout "$BRANCH"
  sudo -u "$TARGET_USER" git -C "$REPO_DIR" reset --hard "origin/$BRANCH"
else
  sudo -u "$TARGET_USER" git clone --branch "$BRANCH" --single-branch "$REPO_URL" "$REPO_DIR"
fi

sudo -u "$TARGET_USER" python3 -m venv "$VENV_DIR"
sudo -u "$TARGET_USER" "$VENV_DIR/bin/python" -m pip install --upgrade pip
sudo -u "$TARGET_USER" "$VENV_DIR/bin/python" -m pip install -r "$REPO_DIR/worker/requirements.txt"

case "$ONNX" in
  cpu) sudo -u "$TARGET_USER" "$VENV_DIR/bin/python" -m pip install -r "$REPO_DIR/worker/requirements-onnx.txt" ;;
  gpu) sudo -u "$TARGET_USER" "$VENV_DIR/bin/python" -m pip install -r "$REPO_DIR/worker/requirements-onnx-gpu.txt" ;;
  none) ;;
  *) echo "Invalid --onnx value: $ONNX" >&2; exit 2 ;;
esac

case "$CUDA" in
  12) sudo -u "$TARGET_USER" "$VENV_DIR/bin/python" -m pip install -r "$REPO_DIR/worker/requirements-cuda12.txt" ;;
  13) sudo -u "$TARGET_USER" "$VENV_DIR/bin/python" -m pip install -r "$REPO_DIR/worker/requirements-cuda13.txt" ;;
  none) ;;
  *) echo "Invalid --cuda value: $CUDA" >&2; exit 2 ;;
esac

ALLOW_INSECURE=0
if [[ "$CONTROLLER" =~ ^http://(localhost|127\.0\.0\.1|10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[0-1])\.) ]]; then
  ALLOW_INSECURE=1
fi

cat > "$ENV_FILE" <<EOF
SWARM_CONTROLLER_URL=$CONTROLLER
SWARM_ENROLLMENT_TOKEN=$TOKEN
SWARM_ALLOW_INSECURE_REMOTE=$ALLOW_INSECURE
EOF
chown "$TARGET_USER:$TARGET_USER" "$ENV_FILE"
chmod 600 "$ENV_FILE"

cat > /etc/systemd/system/compute-swarm-worker.service <<EOF
[Unit]
Description=Compute Swarm Worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$TARGET_USER
EnvironmentFile=$ENV_FILE
WorkingDirectory=$REPO_DIR
ExecStart=$VENV_DIR/bin/python $REPO_DIR/worker/worker.py
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now compute-swarm-worker.service

echo
echo "Compute Swarm worker installed and started."
echo "Controller: $CONTROLLER"
echo "Status: sudo systemctl status compute-swarm-worker"
echo "Logs:   sudo journalctl -u compute-swarm-worker -f"
