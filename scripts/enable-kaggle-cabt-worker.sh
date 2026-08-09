#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${SWARM_APP_DIR:-/opt/compute-swarm}"
SERVICE_NAME="${SWARM_SERVICE_NAME:-compute-swarm-worker}"
ENV_FILE="${SWARM_ENV_FILE:-/etc/compute-swarm/worker.env}"
VENV="$APP_DIR/.venv"

if [[ $EUID -ne 0 ]]; then
  echo "Run this with sudo/root." >&2
  exit 1
fi
if [[ ! -x "$VENV/bin/python" ]]; then
  echo "Worker venv not found at $VENV. Install/update the Compute Swarm Python worker first." >&2
  exit 1
fi
if [[ ! -f "$ENV_FILE" ]]; then
  echo "Worker environment file not found: $ENV_FILE" >&2
  exit 1
fi

"$VENV/bin/python" - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("kaggle-environments requires Python 3.11 or newer")
PY

"$VENV/bin/pip" install -r "$APP_DIR/worker/requirements-kaggle-cabt.txt"

"$VENV/bin/python" - <<'PY'
from kaggle_environments import make
env = make("cabt")
assert env is not None
print("CABT environment import smoke test passed")
PY

python3 - "$ENV_FILE" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
lines = path.read_text().splitlines()
values = {}
order = []
for line in lines:
    if "=" in line and not line.lstrip().startswith("#"):
        key, value = line.split("=", 1)
        if key not in values:
            order.append(key)
        values[key] = value
    else:
        order.append(line)

values["SWARM_ENABLE_KAGGLE_CABT"] = "1"
mods = [x.strip() for x in values.get("SWARM_PLUGIN_MODULES", "").split(",") if x.strip()]
if "plugins.kaggle_cabt" not in mods:
    mods.append("plugins.kaggle_cabt")
values["SWARM_PLUGIN_MODULES"] = ",".join(mods)
for key in ("SWARM_ENABLE_KAGGLE_CABT", "SWARM_PLUGIN_MODULES"):
    if key not in order:
        order.append(key)

out = []
seen = set()
for item in order:
    if item in values and item not in seen:
        out.append(f"{item}={values[item]}")
        seen.add(item)
    elif item not in values:
        out.append(item)
path.write_text("\n".join(out).rstrip() + "\n")
PY

systemctl restart "$SERVICE_NAME"
sleep 2
systemctl --no-pager --full status "$SERVICE_NAME" || true

echo
echo "Kaggle CABT enabled."
echo "The worker should advertise:"
echo "  task:kaggle_cabt_episode"
echo "  kaggle:cabt"
echo
echo "Check with:"
echo "  sudo journalctl -u $SERVICE_NAME -n 80 --no-pager"
