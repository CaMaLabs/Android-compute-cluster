# Universal Compute Swarm

A heterogeneous distributed-computing platform for Android phones, Linux/Windows/macOS machines, Raspberry Pi/SBCs, servers, and GPU/accelerator nodes.

Workers connect **outbound** to a controller, advertise locally installed capabilities, lease compatible work, and return results. Devices can share a hotspot/LAN or be distributed across the Internet behind NAT.

The high-level interface is the **distributed experiment engine**: submit a parameter grid, let the swarm distribute independent parameter points across whatever devices are available, rank the results, and optionally launch a finer refinement sweep around promising regions.

## Included components

- `coordinator/` — FastAPI controller, SQLite state, scheduler, experiment engine, artifact store, and web dashboard.
- `worker/` — portable Python/Termux worker with optional CUDA/CuPy and ONNX Runtime backends.
- `rust-worker/` — native cross-platform worker for desktops, servers, Raspberry Pis, and SBCs.
- `android-worker/` — native Android foreground-service worker with LiteRT and optional Vulkan Compute.
- `scripts/` — Ubuntu controller/worker and Windows worker installers.

All worker implementations speak the same controller protocol and task/capability vocabulary.

## Security model

The controller does **not** send shell commands, executables, source code, CUDA kernels, Vulkan shaders, or arbitrary command lines. It sends a registered task name plus JSON/artifact input. A device receives a task only if it already advertises the corresponding local `task:<kind>` capability.

The Rust worker supports optional executable plugins, but executable paths and arguments are configured locally by the device owner. The remote controller cannot install or select arbitrary executables.

Remote/public controllers must use HTTPS. Plain HTTP is intended only for loopback or trusted LAN/hotspot use.

## Architecture

```text
                         HTTPS / trusted-LAN HTTP

 Android APK  ───────────────┐
 Python/Termux ──────────────┤
 Linux/RPi Rust worker ──────┤
 Windows/macOS worker ───────┼──> FastAPI controller
 CUDA/ONNX PC ───────────────┤       ├── experiment engine
 Remote server ──────────────┘       ├── adaptive pull scheduler
                                     ├── lease/recovery queue
                                     ├── SQLite state
                                     ├── SHA-256 artifact store
                                     └── web dashboard
```

Workers pull work when they are ready. Faster devices therefore consume more parameter points automatically; slower or thermally throttled devices simply consume fewer. If a worker disappears, its expired lease returns to the queue.

## Ubuntu controller install

For the private repo, clone with your authenticated GitHub account first:

```bash
gh repo clone CaMaLabs/Android-compute-cluster ~/compute-swarm -- \
  --branch agent/universal-compute-swarm

sudo SWARM_REPO_URL="$HOME/compute-swarm" \
  bash ~/compute-swarm/scripts/install-controller-ubuntu.sh
```

The installer creates persistent controller/enrollment tokens, persistent SQLite/artifact storage, and a systemd service named `compute-swarm-controller`.

Manual development start:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r coordinator/requirements.txt

export SWARM_ADMIN_TOKEN='replace-with-a-long-secret'
export SWARM_ENROLLMENT_TOKEN='replace-with-another-long-secret'
uvicorn --app-dir coordinator server:app --host 0.0.0.0 --port 8765
```

The unified `server:app` launcher registers the API, experiment engine, and dashboard.

Useful URLs:

```text
http://CONTROLLER:8765/          dashboard
http://CONTROLLER:8765/docs      API documentation
http://CONTROLLER:8765/health    health check
```

## Distributed experiment engine

See [`docs/EXPERIMENTS.md`](docs/EXPERIMENTS.md) for the complete contract.

An experiment specifies a locally installed task, a parameter grid, scheduling requirements, and optionally an objective to maximize or minimize.

```bash
curl -X POST http://127.0.0.1:8765/experiments \
  -H "Authorization: Bearer $SWARM_ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "name":"example sweep",
    "task":"my_simulation",
    "parameters":{
      "voltage":{"values":[100,200,300,400]},
      "field_strength":{"start":0.1,"stop":1.0,"step":0.1},
      "geometry_scale":{"start":0.5,"stop":1.0,"step":0.05}
    },
    "objective":{"path":"metrics.efficiency","direction":"maximize"},
    "requirements":{"capabilities":["cpu"]}
  }'
```

The controller expands the Cartesian parameter grid into independently leased units. Up to 10,000 work units are accepted per experiment. Use staged/refined searches for larger spaces.

Experiment status and ranked results:

```bash
curl http://127.0.0.1:8765/experiments/EXPERIMENT_ID \
  -H "Authorization: Bearer $SWARM_ADMIN_TOKEN"
```

The response includes progress, `best`, ranked results, and per-worker throughput.

Coarse-to-fine refinement:

```bash
curl -X POST http://127.0.0.1:8765/experiments/EXPERIMENT_ID/refine \
  -H "Authorization: Bearer $SWARM_ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"top_k":3,"shrink":0.25,"points_per_axis":5}'
```

For numeric range parameters, the child experiment searches smaller neighborhoods around the best completed points while remaining inside the original bounds.

The web dashboard also provides **New experiment**, live experiment progress, result inspection, and one-click refinement.

## Python / Termux worker

```bash
pip install -r worker/requirements.txt
export SWARM_CONTROLLER_URL=http://127.0.0.1:8765
export SWARM_ENROLLMENT_TOKEN='replace-with-enrollment-token'
python worker/worker.py
```

On a trusted LAN/hotspot where the controller is another machine:

```bash
export SWARM_ALLOW_INSECURE_REMOTE=1
export SWARM_CONTROLLER_URL=http://192.168.1.10:8765
python worker/worker.py
```

For Internet use, use `https://...` instead.

## Ubuntu / Raspberry Pi worker install

The portable Python worker can be installed as a persistent systemd service:

```bash
sudo bash scripts/install-ubuntu.sh \
  --controller http://192.168.1.10:8765 \
  --token YOUR_ENROLLMENT_TOKEN
```

The same script works on Ubuntu and Debian/Raspberry Pi OS environments with the required packages available.

## Rust worker

```bash
cd rust-worker
cargo build --release
export SWARM_CONTROLLER_URL=https://swarm.example.com
export SWARM_ENROLLMENT_TOKEN='replace-with-enrollment-token'
./target/release/compute-swarm-worker
```

The Rust worker is useful for PCs, servers, Raspberry Pis, and other devices where a small native daemon is preferable to Python.

## Native Android worker

Install the APK from `releases/compute-swarm-worker.apk`, enter the controller URL and enrollment token, then tap **Join swarm**. It runs as a foreground service with battery/temperature-aware pausing.

The Android worker includes:

- `prime_count`
- `monte_carlo_pi`
- `sha256_artifact`
- `text_artifact`
- `litert_infer`
- `vulkan_vector_add` on devices with a usable Vulkan compute queue

## GPU and inference backends

See [`docs/ACCELERATORS.md`](docs/ACCELERATORS.md).

Python workers automatically discover optional locally installed accelerator libraries.

```text
CUDA/CuPy        cuda, cuda:cupy
ONNX Runtime     onnx, onnxruntime, optionally onnx:cuda
Android          litert/tflite, optionally vulkan
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

Optional accelerator tasks include:

- `cuda_vector_add`
- `cuda_matmul_npy`
- `onnx_infer`
- `litert_infer`
- `vulkan_vector_add`

## Low-level jobs

The experiment engine builds on the same generic job protocol. You can still submit explicit units directly:

```bash
curl -X POST http://127.0.0.1:8765/jobs \
  -H "Authorization: Bearer $SWARM_ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "kind":"prime_count",
    "units":[{"start":2,"end":500000},{"start":500000,"end":1000000}],
    "requirements":{"capabilities":["cpu"]}
  }'
```

## Large files / artifacts

Large inputs are uploaded once and referenced by SHA-256 artifact ID instead of being embedded in JSON:

```bash
curl -X POST 'http://127.0.0.1:8765/artifacts?name=input.bin' \
  -H "Authorization: Bearer $SWARM_ADMIN_TOKEN" \
  -H 'Content-Type: application/octet-stream' \
  --data-binary @input.bin
```

Workers download referenced inputs into isolated per-unit sandboxes and verify SHA-256. Worker artifact reads are lease-scoped, so possession of a worker token alone does not grant access to unrelated artifacts.

See [`docs/PROTOCOL.md`](docs/PROTOCOL.md).

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
task:my_simulation
task:onnx_infer
task:litert_infer
task:vulkan_vector_add
```

Jobs and experiments can additionally constrain OS, architecture, minimum CPU cores/RAM, and exact-match labels such as site or rack.

## Tests and builds

```bash
pip install -r coordinator/requirements.txt -r worker/requirements.txt pytest httpx
pytest -q
```

GitHub Actions runs the controller/experiment tests, ONNX execution test, Rust `cargo check`, and a full Android debug APK build including NDK/Vulkan/LiteRT. Successful Android builds are signed with the project signing identity and published to `releases/compute-swarm-worker.apk`.
