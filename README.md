# Universal Compute Swarm

A heterogeneous distributed-computing controller and worker protocol for Android phones, Linux/Windows/macOS machines, Raspberry Pi/SBCs, servers, and GPU/accelerator nodes.

The design is inspired by the useful real-device ideas in LiveDewStream, but removes its Android/ADB/TensorFlow-specific assumptions. Workers connect **outbound** to a controller, advertise what they can do, lease compatible work, and return results. Devices can be on the same hotspot or distributed across the Internet behind NAT.

## Included workers

- `worker/` — portable Python/Termux worker, with optional CUDA/CuPy and ONNX Runtime backends.
- `rust-worker/` — native cross-platform worker for desktops, servers, and SBCs.
- `android-worker/` — native Android foreground-service worker with LiteRT and optional Vulkan Compute.

All workers use the same controller API and task/capability vocabulary.

## Security model

The controller does not send shell commands, executables, source code, CUDA kernels, Vulkan shaders, or arbitrary command lines. Workers advertise locally installed capabilities and the scheduler only leases matching jobs.

The Rust worker can run optional local command plugins, but those commands must be configured locally by the device owner. The controller can only select the registered task name and provide JSON/artifact input.

Remote/public controllers must use HTTPS. Plain HTTP is only intended for loopback or trusted LAN/hotspot use.

## Architecture

```text
                       HTTPS / trusted-LAN HTTP

 Android APK  ───────────────┐
 Python/Termux ──────────────┤
 Linux/RPi Rust worker ──────┤
 Windows/macOS Rust worker ──┼──> FastAPI controller
 CUDA/ONNX PC ───────────────┤       ├── capability scheduler
 Remote server ──────────────┘       ├── lease/recovery queue
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

## GPU and inference backends

See [`docs/ACCELERATORS.md`](docs/ACCELERATORS.md) for installation and job examples.

Python workers automatically discover optional locally installed accelerator libraries:

```text
CUDA/CuPy        cuda, cuda:cupy
ONNX Runtime     onnx, onnxruntime, optionally onnx:cuda
```

Install examples:

```bash
# CUDA 12.x
pip install -r worker/requirements.txt -r worker/requirements-cuda12.txt

# CUDA 13.x
pip install -r worker/requirements.txt -r worker/requirements-cuda13.txt

# ONNX CPU
pip install -r worker/requirements.txt -r worker/requirements-onnx.txt

# ONNX GPU
pip install -r worker/requirements.txt -r worker/requirements-onnx-gpu.txt
```

The native Android APK bundles LiteRT. If a device successfully exposes a Vulkan compute queue, the APK additionally advertises `vulkan` and the fixed `vulkan_vector_add` GPU task.

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

The Android worker includes:

- `litert_infer` — local LiteRT FLOAT32 inference from a model artifact.
- `vulkan_vector_add` — a real NDK Vulkan compute dispatch using a locally bundled shader; advertised only on compatible devices.

The controller cannot replace the model runtime code or Vulkan shader.

## Creating jobs

A normal job is JSON. The worker must advertise `task:<kind>` plus every explicit requirement:

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

```bash
curl -X POST 'http://127.0.0.1:8765/artifacts?name=input.bin' \
  -H "Authorization: Bearer $SWARM_ADMIN_TOKEN" \
  -H 'Content-Type: application/octet-stream' \
  --data-binary @input.bin
```

The response's `artifact_id` can then be referenced in a work-unit payload:

```json
{
  "artifact_inputs":[
    {"artifact_id":"<sha256>", "alias":"input", "name":"input.bin"}
  ]
}
```

Workers download inputs into an isolated per-unit work directory and verify SHA-256 before execution. Trusted local tasks can declare sandbox files as outputs; the agent uploads them back to the controller.

Worker artifact reads are lease-scoped: possession of a device token alone does not grant access to arbitrary stored artifacts.

See [`docs/PROTOCOL.md`](docs/PROTOCOL.md) for the wire and artifact contract.

## Capability scheduling

Examples:

```text
cpu
python
rust
kotlin
cuda
cuda:cupy
onnx
onnx:cuda
litert
tflite
vulkan
os:linux
os:android
arch:x86_64
arch:aarch64
task:cuda_matmul_npy
task:onnx_infer
task:litert_infer
task:vulkan_vector_add
```

A job may additionally constrain OS, architecture, minimum CPU cores/RAM, and arbitrary exact-match labels such as site or rack.

## Included task implementations

Baseline tasks:

- `prime_count`
- `monte_carlo_pi`
- `sha256_artifact`
- `text_artifact`

Optional/native accelerator tasks:

- `cuda_vector_add`
- `cuda_matmul_npy`
- `onnx_infer`
- `litert_infer`
- `vulkan_vector_add`

## Tests and builds

```bash
pip install -r coordinator/requirements.txt -r worker/requirements.txt pytest httpx
pytest -q
```

GitHub Actions runs the Python tests, `cargo check` for the Rust worker, and a full Android debug APK build including NDK C++, shader compilation, and LiteRT dependency resolution. A successful Android job uploads `app-debug.apk` as a workflow artifact.
