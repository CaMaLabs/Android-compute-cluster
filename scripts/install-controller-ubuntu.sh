#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${SWARM_APP_DIR:-/opt/compute-swarm}"
SERVICE_USER="${SWARM_SERVICE_USER:-compute-swarm}"
SERVICE_NAME="${SWARM_SERVICE_NAME:-compute-swarm-controller}"
ENV_DIR="/etc/compute-swarm"
ENV_FILE="$ENV_DIR/controller.env"
PORT="${SWARM_PORT:-8765}"
REPO_URL="${SWARM_REPO_URL:-https://github.com/CaMaLabs/Android-compute-cluster.git}"
REPO_BRANCH="${SWARM_REPO_BRANCH:-agent/universal-compute-swarm}"

if [[ $EUID -ne 0 ]]; then
  echo "Run this installer with sudo/root." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y git python3 python3-venv python3-pip curl ca-certificates openssl

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

if [[ -d "$APP_DIR/.git" ]]; then
  git -C "$APP_DIR" fetch origin "$REPO_BRANCH"
  git -C "$APP_DIR" checkout "$REPO_BRANCH"
  git -C "$APP_DIR" reset --hard "origin/$REPO_BRANCH"
else
  rm -rf "$APP_DIR"
  git clone --branch "$REPO_BRANCH" --single-branch "$REPO_URL" "$APP_DIR"
fi

python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip wheel
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/coordinator/requirements.txt"

mkdir -p "$ENV_DIR"
chmod 700 "$ENV_DIR"

if [[ ! -f "$ENV_FILE" ]]; then
  ADMIN_TOKEN="$(openssl rand -hex 32)"
  ENROLLMENT_TOKEN="$(openssl rand -hex 32)"
  cat > "$ENV_FILE" <<EOF
SWARM_ADMIN_TOKEN=$ADMIN_TOKEN
SWARM_ENROLLMENT_TOKEN=$ENROLLMENT_TOKEN
SWARM_DATA_DIR=/var/lib/compute-swarm
EOF
  chmod 600 "$ENV_FILE"
fi

mkdir -p /var/lib/compute-swarm
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR" /var/lib/compute-swarm

cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Compute Swarm Controller
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$APP_DIR/.venv/bin/uvicorn --app-dir $APP_DIR/coordinator app:app --host 0.0.0.0 --port $PORT
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/compute-swarm

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"

sleep 1
if ! systemctl is-active --quiet "$SERVICE_NAME"; then
  echo "Controller failed to start. Recent logs:" >&2
  journalctl -u "$SERVICE_NAME" -n 50 --no-pager >&2
  exit 1
fi

IP_ADDR="$(hostname -I 2>/dev/null | awk '{print $1}')"
ENROLLMENT_TOKEN="$(grep '^SWARM_ENROLLMENT_TOKEN=' "$ENV_FILE" | cut -d= -f2-)"
ADMIN_TOKEN="$(grep '^SWARM_ADMIN_TOKEN=' "$ENV_FILE" | cut -d= -f2-)"

cat <<EOF

Compute Swarm controller installed successfully.

Service:        $SERVICE_NAME
Controller URL: http://${IP_ADDR:-127.0.0.1}:$PORT
Local URL:      http://127.0.0.1:$PORT

Android enrollment token:
$ENROLLMENT_TOKEN

Admin token:
$ADMIN_TOKEN

Useful commands:
  sudo systemctl status $SERVICE_NAME
  sudo journalctl -u $SERVICE_NAME -f
  sudo systemctl restart $SERVICE_NAME
  sudo cat $ENV_FILE

For access across the public Internet, put the controller behind HTTPS rather than exposing plain HTTP directly.
EOF
