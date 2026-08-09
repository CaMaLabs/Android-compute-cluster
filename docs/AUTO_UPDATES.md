# Automatic updates

Compute Swarm Linux controllers and Python workers can update themselves from the configured Git branch. New installs default to `main` and enable update checks every 15 minutes with a small randomized delay on systemd hosts.

## Safety behavior

The updater:

- fetches only the configured repository/branch;
- updates only when the installed commit can fast-forward to `origin/<branch>`;
- refuses to overwrite tracked local changes;
- reinstalls the configured Python requirement sets;
- restarts the controller/worker service;
- checks that the service came back (and checks `/health` for the controller);
- rolls the repository back to the previous commit if dependency installation or service health fails;
- records its latest state under `/var/lib/compute-swarm/` on Linux.

Untracked files are not deleted. If an untracked file collides with an incoming tracked path, Git rejects the update and the node remains on its current revision.

## Linux controller

Existing controllers need one final manual installer run to create the update timer:

```bash
cd ~/compute-swarm
git fetch origin
git checkout main
git pull --ff-only origin main
sudo SWARM_REPO_URL="$HOME/compute-swarm" bash scripts/install-controller-ubuntu.sh
```

After that:

```bash
sudo systemctl list-timers compute-swarm-controller-update.timer
sudo systemctl start compute-swarm-controller-update.service
sudo cat /var/lib/compute-swarm/controller-update-state
```

## Ubuntu Python worker

Rerun the worker installer once using the same controller URL/token and accelerator flags. The installer creates `compute-swarm-worker-update.timer` and remembers which ONNX/CUDA requirement files should be refreshed after future pulls.

```bash
sudo systemctl list-timers compute-swarm-worker-update.timer
sudo systemctl start compute-swarm-worker-update.service
sudo cat /var/lib/compute-swarm/worker-update-state
```

## Raspberry Pi worker

Rerun `scripts/install-worker-raspberry-pi.sh` once with the same `SWARM_CONTROLLER_URL` and `SWARM_ENROLLMENT_TOKEN`. Future clean fast-forward updates then happen automatically.

## Windows worker

Rerun `scripts/install-windows.ps1` once. It creates a `ComputeSwarmAutoUpdate` Scheduled Task that checks `main` every 15 minutes and restarts `ComputeSwarmWorker` only when a new commit is successfully applied.

## Disable or change cadence

Linux installers accept environment variables:

```bash
SWARM_AUTO_UPDATE=0
SWARM_UPDATE_INTERVAL_MINUTES=60
```

The Windows installer accepts `-UpdateIntervalMinutes`.

## Android

Android APK replacement is different from Git-based workers. Normal Android does not allow an ordinary app to silently replace itself without user confirmation (unless the device is managed/device-owner or rooted). Android workers therefore need a separate in-app update-check/download flow; they cannot use these Git timers.
