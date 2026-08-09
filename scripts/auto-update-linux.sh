#!/usr/bin/env bash
set -Eeuo pipefail

# Generic unattended updater for Linux Compute Swarm controller/worker installs.
# The installer writes the SWARM_UPDATE_* values into a root-owned EnvironmentFile.

: "${SWARM_UPDATE_REPO_DIR:?SWARM_UPDATE_REPO_DIR is required}"
: "${SWARM_UPDATE_SERVICE:?SWARM_UPDATE_SERVICE is required}"
: "${SWARM_UPDATE_PYTHON:?SWARM_UPDATE_PYTHON is required}"

REPO_DIR="$SWARM_UPDATE_REPO_DIR"
SERVICE_NAME="$SWARM_UPDATE_SERVICE"
PYTHON="$SWARM_UPDATE_PYTHON"
BRANCH="${SWARM_UPDATE_BRANCH:-main}"
GIT_USER="${SWARM_UPDATE_GIT_USER:-root}"
REQUIREMENTS="${SWARM_UPDATE_REQUIREMENTS:-}"
HEALTH_URL="${SWARM_UPDATE_HEALTH_URL:-}"
STATE_FILE="${SWARM_UPDATE_STATE_FILE:-/var/lib/compute-swarm/update-state}"
LOCK_FILE="${SWARM_UPDATE_LOCK_FILE:-/run/lock/${SERVICE_NAME}-update.lock}"

mkdir -p "$(dirname "$STATE_FILE")" "$(dirname "$LOCK_FILE")"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  exit 0
fi

log() {
  printf '[compute-swarm-update] %s\n' "$*"
}

write_state() {
  local status="$1"
  local current="${2:-unknown}"
  local target="${3:-unknown}"
  local message="${4:-}"
  local now
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  {
    printf 'status=%s\n' "$status"
    printf 'checked_at=%s\n' "$now"
    printf 'current=%s\n' "$current"
    printf 'target=%s\n' "$target"
    printf 'message=%s\n' "${message//$'\n'/ }"
  } > "$STATE_FILE"
}

run_as_git_user() {
  if [[ "$GIT_USER" == "root" || -z "$GIT_USER" ]]; then
    "$@"
  else
    runuser -u "$GIT_USER" -- "$@"
  fi
}

git_cmd() {
  run_as_git_user git -c safe.directory="$REPO_DIR" -C "$REPO_DIR" "$@"
}

python_cmd() {
  if [[ "$GIT_USER" == "root" || -z "$GIT_USER" ]]; then
    "$PYTHON" "$@"
  else
    runuser -u "$GIT_USER" -- "$PYTHON" "$@"
  fi
}

install_requirements() {
  local req
  IFS=',' read -r -a reqs <<< "$REQUIREMENTS"
  for req in "${reqs[@]}"; do
    req="${req#${req%%[![:space:]]*}}"
    req="${req%${req##*[![:space:]]}}"
    [[ -n "$req" ]] || continue
    if [[ ! -f "$REPO_DIR/$req" ]]; then
      log "requirements file missing: $req"
      return 1
    fi
    log "installing dependencies from $req"
    python_cmd -m pip install -r "$REPO_DIR/$req"
  done
}

service_healthy() {
  local i
  for i in {1..30}; do
    if systemctl is-active --quiet "$SERVICE_NAME"; then
      if [[ -z "$HEALTH_URL" ]] || curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
        return 0
      fi
    fi
    sleep 1
  done
  return 1
}

if [[ ! -d "$REPO_DIR/.git" ]]; then
  write_state "error" "unknown" "unknown" "repository not found: $REPO_DIR"
  log "repository not found: $REPO_DIR"
  exit 1
fi
if [[ ! -x "$PYTHON" ]]; then
  write_state "error" "unknown" "unknown" "python environment not found: $PYTHON"
  log "python environment not found: $PYTHON"
  exit 1
fi

# Never overwrite tracked local edits. Untracked files are tolerated unless Git itself
# reports that they collide with an incoming path.
if ! git_cmd diff --quiet || ! git_cmd diff --cached --quiet; then
  current="$(git_cmd rev-parse HEAD 2>/dev/null || echo unknown)"
  write_state "blocked_dirty" "$current" "$current" "tracked local changes detected"
  log "tracked local changes detected; refusing automatic update"
  exit 2
fi

log "checking origin/$BRANCH"
git_cmd fetch --prune origin "$BRANCH"
CURRENT="$(git_cmd rev-parse HEAD)"
TARGET="$(git_cmd rev-parse "origin/$BRANCH")"

if [[ "$CURRENT" == "$TARGET" ]]; then
  write_state "up_to_date" "$CURRENT" "$TARGET" "no update available"
  exit 0
fi

if ! git_cmd merge-base --is-ancestor "$CURRENT" "$TARGET"; then
  write_state "blocked_diverged" "$CURRENT" "$TARGET" "local history is not a fast-forward of origin/$BRANCH"
  log "local history diverged from origin/$BRANCH; refusing automatic update"
  exit 3
fi

rollback() {
  local reason="$1"
  log "update failed; rolling back to $CURRENT: $reason"
  git_cmd reset --hard "$CURRENT" || true
  install_requirements || true
  systemctl restart "$SERVICE_NAME" || true
  service_healthy || true
  write_state "rolled_back" "$CURRENT" "$TARGET" "$reason"
}

log "updating $CURRENT -> $TARGET"
if ! git_cmd checkout "$BRANCH"; then
  write_state "error" "$CURRENT" "$TARGET" "could not checkout $BRANCH"
  exit 4
fi
if ! git_cmd merge --ff-only "origin/$BRANCH"; then
  write_state "error" "$CURRENT" "$TARGET" "fast-forward failed (possibly an untracked-file collision)"
  exit 5
fi

if ! install_requirements; then
  rollback "dependency installation failed"
  exit 6
fi

systemctl daemon-reload || true
if ! systemctl restart "$SERVICE_NAME"; then
  rollback "service restart failed"
  exit 7
fi
if ! service_healthy; then
  rollback "service failed health check after update"
  exit 8
fi

NEW="$(git_cmd rev-parse HEAD)"
write_state "updated" "$NEW" "$TARGET" "updated successfully from $CURRENT"
log "updated successfully to $NEW"
