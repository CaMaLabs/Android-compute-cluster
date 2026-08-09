#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${SWARM_APP_DIR:-/opt/compute-swarm}"
SERVICE_USER="${SWARM_SERVICE_USER:-compute-swarm}"
SERVICE_NAME="${SWARM_SERVICE_NAME:-compute-swarm-worker}"
ENV_DIR="/etc/compute-swarm"
ENV_FILE="$ENV_DIR/worker.env"
DATA_DIR="${SWARM_DATA_DIR:-/var/lib/compute-swarm}"
REPO_URL="${SWARM_REPO_URL:-https://github.com/CaMaLabs/Android-compute-cluster.git}"
REPO_BRANCH="${SWARM_REPO_BRANCH:-agent/universal-compute-swarm}"
CONTROLLER_URL="${SWARM_CONTROLLER_URL:-}"
ENROLLMENT_TOKEN="${SWARM_ENROLLMENT_TOKEN:-}"
MAX_TEMP_C="${SWARM_MAX_TEMP_C:-80}"
RESUME_TEMP_C="${SWARM_RESUME_TEMP_C:-75}"

if [[ $EUID -ne 0 ]]; then
  echo "Run this installer with sudo/root." >&2
  exit 1
fi
if [[ -z "$CONTROLLER_URL" ]]; then
  echo "Set SWARM_CONTROLLER_URL, for example http://192.168.1.27:8765" >&2
  exit 1
fi
if [[ -z "$ENROLLMENT_TOKEN" ]]; then
  echo "Set SWARM_ENROLLMENT_TOKEN from the controller env file." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y git curl ca-certificates python3 python3-venv python3-pip

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

systemctl stop "$SERVICE_NAME" >/dev/null 2>&1 || true

if [[ -d "$APP_DIR/.git" ]]; then
  git config --global --add safe.directory "$APP_DIR" || true
  git -C "$APP_DIR" fetch origin "$REPO_BRANCH"
  git -C "$APP_DIR" checkout "$REPO_BRANCH"
  git -C "$APP_DIR" reset --hard "origin/$REPO_BRANCH"
else
  rm -rf "$APP_DIR"
  git clone --branch "$REPO_BRANCH" --single-branch "$REPO_URL" "$APP_DIR"
fi

python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip wheel
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/worker/requirements.txt"

mkdir -p "$ENV_DIR" "$DATA_DIR/work"
chown root:"$SERVICE_USER" "$ENV_DIR"
chmod 750 "$ENV_DIR"
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR" "$DATA_DIR"

cat > "$ENV_FILE" <<EOF
SWARM_CONTROLLER_URL=$CONTROLLER_URL
SWARM_ALLOW_INSECURE_REMOTE=${SWARM_ALLOW_INSECURE_REMOTE:-1}
SWARM_ENROLLMENT_TOKEN=$ENROLLMENT_TOKEN
SWARM_IDENTITY_FILE=$DATA_DIR/worker-identity.json
SWARM_WORK_ROOT=$DATA_DIR/work
SWARM_POLL_SECONDS=${SWARM_POLL_SECONDS:-1.5}
SWARM_MAX_TEMP_C=$MAX_TEMP_C
SWARM_RESUME_TEMP_C=$RESUME_TEMP_C
SWARM_LABELS=${SWARM_LABELS:-device=raspberry-pi,role=worker}
EOF
chmod 640 "$ENV_FILE"
chown root:"$SERVICE_USER" "$ENV_FILE"

cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Compute Swarm Worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$APP_DIR/worker
EnvironmentFile=$ENV_FILE
ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/worker/worker.py
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"

for _ in {1..12}; do
  if systemctl is-active --quiet "$SERVICE_NAME"; then
    break
  fi
  sleep 1
done

systemctl --no-pager --full status "$SERVICE_NAME" || true
cat <<EOF

Raspberry Pi Compute Swarm worker installed.

Service: $SERVICE_NAME
Config:  $ENV_FILE
Work dir: $DATA_DIR/work
Thermal pause/resume: ${MAX_TEMP_C}C / ${RESUME_TEMP_C}C

Useful commands:
  sudo systemctl status $SERVICE_NAME
  sudo journalctl -u $SERVICE_NAME -f
  sudo systemctl restart $SERVICE_NAME
  sudo systemctl stop $SERVICE_NAME
  sudo systemctl enable $SERVICE_NAME
  sudo systemctl disable $SERVICE_NAME
EOF
