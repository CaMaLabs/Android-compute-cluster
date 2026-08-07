# Swarm agent protocol

The protocol is intentionally HTTP/JSON and implementation-neutral. A device does not need Python or Android; it only needs to implement these operations.

## Trust model

There are two credentials:

- `SWARM_ADMIN_TOKEN`: controller/operator credential for job submission and status.
- `SWARM_ENROLLMENT_TOKEN`: one-time-ish shared enrollment credential used to join a new device.

Enrollment returns a unique per-device bearer token. The device stores that token locally and uses it for all later worker calls.

For remote/WAN deployments the controller URL should be HTTPS. Workers initiate every connection outbound, so no worker needs an inbound firewall/NAT port.

## Worker lifecycle

1. `POST /workers/enroll` using the enrollment token.
2. Store `worker_id` and returned `device_token`.
3. `POST /workers/{worker_id}/register` using the device token.
4. Send periodic heartbeat telemetry.
5. Long-poll `POST /workers/{worker_id}/lease?wait_seconds=15`.
6. Execute only a locally installed task handler matching `kind`.
7. Renew the lease while a long task is running.
8. Submit either a result or a failure.

## Capability scheduling

Workers advertise strings such as:

```json
[
  "cpu",
  "python",
  "os:linux",
  "arch:x86_64",
  "task:prime_count",
  "task:vector_sum"
]
```

Jobs may additionally require OS, architecture, memory, core count, capabilities, or exact labels. The server never sends a unit to a worker that lacks `task:<kind>`.

This lets specialized agents coexist in one swarm. Examples:

- Android/ARM phone: `cpu`, `task:foo`, later `vulkan`
- Linux workstation: `cpu`, `cuda`, `task:foo`
- Raspberry Pi: `cpu`, `gpio`, `task:sensor_reduce`
- Windows PC: `cpu`, `directml`, `task:model_infer`
- Browser/WebAssembly agent: a future implementation of the same lease protocol

## No remote shell

The controller does not send commands, scripts, executables, or Python source. Work units contain a typed `kind` plus JSON payload. Code must already be installed on the device as a task plugin. This is deliberate: it keeps enrollment from becoming arbitrary remote code execution.
