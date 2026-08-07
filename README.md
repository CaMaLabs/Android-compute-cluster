# Universal Compute Swarm

A device-agnostic distributed-computing controller and worker protocol derived from the useful ideas in smartphone-cluster projects such as LiveDewStream, but redesigned for heterogeneous devices and remote/WAN operation.

## What it does

- Android/Termux, Linux, Windows, macOS and SBCs can use the included Python agent.
- Any other device can join by implementing the small HTTP/JSON protocol in `docs/PROTOCOL.md`.
- Workers advertise CPU, OS, architecture, memory, labels and locally installed task capabilities.
- Jobs are scheduled only onto compatible devices.
- Workers connect **outbound** to the controller, so phones behind hotspots, NAT or home routers need no incoming ports.
- Expiring leases recover work automatically when a device disconnects.
- Long-running work renews its lease while computing.
- Per-device credentials are issued at enrollment.
- Android thermal/battery throttling remains supported.
- No arbitrary remote shell or server-pushed executable code.

## Architecture

```text
                         Internet / private LAN
                                  |
                         HTTPS controller API
                       FastAPI + SQLite (MVP)
                                  |
          +-----------------------+-----------------------+
          |                       |                       |
   Android / Termux         Linux / Windows         SBC / custom agent
      ARM worker              x86/ARM worker          any implementation
          |                       |                       |
     local plugins             local plugins            local plugins
```

The same controller can therefore manage a few phones on a hotspot or a geographically distributed collection of machines.

## 1. Run the controller

```bash
cd coordinator
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export SWARM_ADMIN_TOKEN='replace-with-a-long-random-admin-token'
export SWARM_ENROLLMENT_TOKEN='replace-with-a-long-random-enrollment-token'
uvicorn app:app --host 0.0.0.0 --port 8765
```

For a LAN/hotspot test, the controller can be reached at `http://<controller-lan-ip>:8765`.

For remote devices, put the API behind TLS and use a public DNS name such as:

```text
https://swarm.example.com
```

Caddy/nginx, a cloud load balancer, or a private overlay network such as Tailscale can terminate TLS. The worker refuses plaintext non-local controller URLs by default.

## 2. Join a Python-capable device

The included worker works on conventional Python 3 platforms.

### Linux / macOS / Windows

```bash
cd worker
python -m pip install -r requirements.txt
export SWARM_CONTROLLER_URL='https://swarm.example.com'
export SWARM_ENROLLMENT_TOKEN='replace-with-the-enrollment-token'
python worker.py
```

### Android / Termux

```bash
pkg update
pkg install python termux-api
cd worker
pip install -r requirements.txt
export SWARM_CONTROLLER_URL='https://swarm.example.com'
export SWARM_ENROLLMENT_TOKEN='replace-with-the-enrollment-token'
python worker.py
```

For a trusted LAN test using plaintext HTTP, explicitly allow it:

```bash
export SWARM_ALLOW_INSECURE_REMOTE=1
export SWARM_CONTROLLER_URL='http://192.168.1.10:8765'
```

After the first successful enrollment, each device stores its own identity/token in:

```text
~/.compute-swarm-identity.json
```

The shared enrollment token is not needed for normal reconnects afterward.

## 3. Submit work

The controller accepts arbitrary JSON work units, but a matching task plugin must already be installed on candidate devices.

Prime-counting example:

```bash
curl -X POST http://127.0.0.1:8765/jobs/range \
  -H 'Authorization: Bearer replace-with-a-long-random-admin-token' \
  -H 'Content-Type: application/json' \
  -d '{
    "kind":"prime_count",
    "start":2,
    "end":2000000,
    "chunk_size":100000,
    "requirements":{"capabilities":["cpu"]}
  }'
```

Generic units example:

```bash
curl -X POST http://127.0.0.1:8765/jobs \
  -H 'Authorization: Bearer replace-with-a-long-random-admin-token' \
  -H 'Content-Type: application/json' \
  -d '{
    "kind":"vector_sum",
    "units":[
      {"values":[1,2,3]},
      {"values":[4,5,6]}
    ]
  }'
```

Inspect the swarm:

```bash
curl -H 'Authorization: Bearer replace-with-a-long-random-admin-token' \
  http://127.0.0.1:8765/status
```

Inspect one job and its results:

```bash
curl -H 'Authorization: Bearer replace-with-a-long-random-admin-token' \
  http://127.0.0.1:8765/jobs/JOB_ID
```

## Adding a workload

The worker has a tiny plugin API. A local module can register a task:

```python
from swarm_plugin import task

@task("vector_sum")
def vector_sum(payload):
    return {"sum": sum(payload["values"])}
```

Then start that device with:

```bash
export SWARM_PLUGIN_MODULES=plugin_example
python worker.py
```

The worker advertises `task:vector_sum`, so only devices with that plugin installed receive those units.

For native performance, the same plugin boundary can call C/C++, Rust, CUDA, Vulkan compute, TensorFlow Lite, ONNX Runtime, or hardware-specific libraries without changing the controller.

## Heterogeneous scheduling

A job may target device properties:

```json
{
  "kind": "model_infer",
  "units": [{"blob_id":"..."}],
  "requirements": {
    "capabilities": ["cuda"],
    "os": ["Linux", "Windows"],
    "arch": ["x86_64", "AMD64"],
    "min_memory_mb": 8192,
    "labels": {"site":"garage"}
  }
}
```

Every job also implicitly requires `task:<kind>`.

## Direction from LiveDewStream

LiveDewStream demonstrated useful real-phone cluster concepts: device registration, job scheduling, Android metrics, battery-aware experiments and mobile inference. This project keeps those ideas but removes the assumptions that every node is Android, physically attached through ADB, or running TensorFlow Lite.

## Planned next layers

- Native Kotlin Android foreground-service agent
- NDK/Vulkan task plugin SDK
- Native Rust agent for Linux/Windows/macOS/ARM SBCs
- PostgreSQL/Redis backend for multi-controller scale
- Web dashboard and job submission UI
- mTLS or public-key device identities
- Optional peer data transfer while the controller remains the authority
- Artifact/blob store for large input and result payloads
