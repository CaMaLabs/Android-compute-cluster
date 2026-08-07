# Universal Compute Swarm

A heterogeneous distributed-computing controller and worker protocol for Android phones, Linux/Windows/macOS machines, Raspberry Pi/SBCs, servers, and future GPU/accelerator agents.

The design is inspired by the useful real-device ideas in LiveDewStream, but removes its Android/ADB/TensorFlow-specific assumptions. Workers connect **outbound** to a controller, advertise what they can do, lease compatible work, and return results. Devices can be on the same hotspot or distributed across the Internet behind NAT.

## Included workers

- `worker/` — portable Python/Termux worker.
- `rust-worker/` — native cross-platform worker for desktops, servers, and SBCs.
- `android-worker/` — native Android foreground-service worker.

All three use the same controller API and task/capability vocabulary.

## Security model

The controller does not send shell commands, executables, source code, or arbitrary command lines. Workers advertise locally installed capabilities such as `task:prime_count`, `cuda`, `vulkan`, or `tflite`, and the scheduler only leases matching jobs.

The Rust worker can run optional local command plugins, but those commands must be configured locally by the device owner. The controller can only select the registered task name and provide JSON input.

Remote/public controllers must use HTTPS. Plain HTTP is only intended for loopback or trusted LAN/hotspot use.

## Architecture

```text
                       HTTPS / trusted-LAN HTTP

 Android APK  ───────────────┐
 Python/Termux ──────────────┤
 Linux/RPi Rust worker ──────┤
 Windows/macOS Rust worker ──┼──> FastAPI controller
 Remote server ──────────────┤       ├── capability scheduler
 Future GPU agent ───────────┘       ├── lease/recovery queue
                                     ├── SQLite state
                                     └── SHA-256 artifact store
```

A worker disappearing during a task does not lose the work unit. Its lease expires and the unit returns to the queue. Long-running agents renew leases in the background.

## Quick start: controller

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r coordinator/requirements.txt

export SWARM_ADMIN_TOKEN='replace-with-a-long-secret'
export SWARM_ENROLLMENT_TOKEN='replace-with-another-long-secret'
uvicorn --app-dir coordinator app:app --host 0.0.0.0 --port 8765
```

For a remote controller, see [`docs/REMOTE_DEPLOYMENT.md`](docs/REMOTE_DEPLOYMENT.md). A Docker Compose deployment is included.

## Quick start: Python/Termux worker

```bash
pip install -r worker/requirements.txt
export SWARM_CONTROLLER_URL=http://127.0.0.1:8765
export SWARM_ENROLLMENT_TOKEN='replace-with-another-long-secret'
python worker/worker.py
```

On a trusted LAN or hotspot where the controller is another machine:

```bash
export SWARM_ALLOW_INSECURE_REMOTE=1
export SWARM_CONTROLLER_URL=http://192.168.1.10:8765
python worker/worker.py
```

For Internet use, use `https://...` instead.

## Rust worker

```bash
cd rust-worker
cargo build --release
export SWARM_CONTROLLER_URL=https://swarm.example.com
export SWARM_ENROLLMENT_TOKEN='replace-with-another-long-secret'
./target/release/compute-swarm-worker
```

The Rust worker is intended for PCs, servers, Raspberry Pis, and other devices where a small native daemon is preferable to Python.

## Native Android worker

Open `android-worker/` in Android Studio and build the app. Enter the controller URL and enrollment token, then tap **Join swarm**. It runs as a foreground service with battery/temperature-aware pausing.

The Android task registry is in:

```text
android-worker/app/src/main/java/com/camalabs/computeswarm/TaskRegistry.kt
```

That is the extension point for Kotlin, NDK/C++, Vulkan Compute, TFLite, or other locally installed Android kernels.

## Creating jobs

A normal job is JSON. The worker must advertise `task:<kind>` plus every requirement:

```bash
curl -X POST http://127.0.0.1:8765/jobs \
  -H "Authorization: Bearer $SWARM_ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "kind":"prime_count",
    "units":[
      {"start":2,"end":500000},
      {"start":500000,"end":1000000}
    ],
    "requirements":{"capabilities":["cpu"]}
  }'
```

Range jobs let the controller create chunks:

```bash
curl -X POST http://127.0.0.1:8765/jobs/range \
  -H "Authorization: Bearer $SWARM_ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "kind":"prime_count",
    "start":2,
    "end":100000000,
    "chunk_size":1000000,
    "requirements":{"capabilities":["cpu"]}
  }'
```

## Large files / artifacts

Large inputs are uploaded once and referenced by SHA-256 artifact ID instead of being embedded in job JSON.

Upload an input:

```bash
curl -X POST 'http://127.0.0.1:8765/artifacts?name=input.bin' \
  -H "Authorization: Bearer $SWARM_ADMIN_TOKEN" \
  -H 'Content-Type: application/octet-stream' \
  --data-binary @input.bin
```

The response's `artifact_id` can be used in a work-unit payload:

```json
{
  "alias":"input",
  "artifact_inputs":[
    {
      "artifact_id":"<sha256>",
      "alias":"input",
      "name":"input.bin"
    }
  ]
}
```

The Python, Rust, and Android workers download the file into an isolated per-unit work directory and verify its SHA-256 checksum before the task runs.

Trusted local tasks can declare files created inside that sandbox as outputs; the agent uploads them to the controller and returns artifact metadata in the work-unit result.

See [`docs/PROTOCOL.md`](docs/PROTOCOL.md) for the complete wire and artifact contract.

## Capability scheduling

Examples of worker-advertised capabilities:

```text
cpu
rust
python
kotlin
cuda
vulkan
tflite
os:linux
os:android
arch:x86_64
arch:aarch64
task:prime_count
task:model_infer
```

A job may additionally constrain:

- operating system
- architecture
- minimum CPU cores
- minimum RAM
- arbitrary labels such as site/rack/owner

This lets one controller coordinate a mixed swarm without assuming all nodes have the same hardware.

## Built-in demonstration tasks

All current workers implement:

- `prime_count`
- `monte_carlo_pi`
- `sha256_artifact`
- `text_artifact`

The demos are deliberately simple. Real workloads should be added as locally installed task handlers/plugins.

## Tests

```bash
pip install -r coordinator/requirements.txt -r worker/requirements.txt pytest httpx
pytest -q
```

GitHub Actions runs the Python test suite, `cargo check` for the Rust worker, and a debug APK build for the native Android worker.

## Next extensions

The protocol is intentionally compatible with future workers written in other languages. Logical next backends include CUDA, Vulkan Compute, ONNX Runtime, TFLite, and object-storage-backed artifacts.
