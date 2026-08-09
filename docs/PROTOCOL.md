# Swarm wire protocol v0.3

The protocol is intentionally small and transport-neutral. Current agents use JSON over HTTP(S) with worker-initiated long polling. Public controllers must be exposed through HTTPS.

## Authentication

There are three credential classes:

- **Admin token** — submits jobs, uploads input artifacts, reads job/status data.
- **Enrollment token** — permits a new device to obtain a unique worker ID/token.
- **Device token** — unique to one enrolled worker and used for registration, heartbeats, leasing, result submission, and artifact transfer.

The controller never transmits shell commands, executables, source code, or plugin installation instructions. A worker advertises `task:<kind>` capabilities for task handlers already installed locally.

## Enrollment

`POST /workers/enroll`

Authorization: enrollment token.

```json
{"name":"pixel-worker"}
```

Response:

```json
{
  "worker_id":"uuid",
  "device_token":"unique-secret",
  "lease_seconds":120
}
```

## Registration

`POST /workers/{worker_id}/register`

Authorization: device token.

```json
{
  "name":"pixel-worker",
  "os_name":"Android",
  "platform":"Android 16",
  "arch":"arm64-v8a",
  "cores":8,
  "memory_mb":8192,
  "benchmark":250000,
  "capabilities":["cpu","task:prime_count","task:sha256_artifact"],
  "labels":{"site":"shop"},
  "agent_version":"0.3.0-android"
}
```

## Work units

Admins create jobs with `/jobs` or `/jobs/range`. Workers long-poll:

`POST /workers/{worker_id}/lease?wait_seconds=15`

A lease contains a task kind and JSON payload:

```json
{
  "work": {
    "lease_id":"uuid",
    "job_id":"uuid",
    "unit_id":"uuid",
    "sequence":0,
    "kind":"sha256_artifact",
    "payload": {
      "alias":"input",
      "artifact_inputs":[
        {"artifact_id":"<sha256>","alias":"input","name":"input.bin"}
      ]
    }
  }
}
```

The scheduler leases the unit only to a device which advertises `task:sha256_artifact` plus every capability/resource/label requirement attached to the job.

## Lease renewal

Long tasks and artifact transfers renew through:

`POST /workers/{worker_id}/leases/{lease_id}/renew`

If a worker disappears, an expired lease is automatically returned to the queue.

## Artifact store

Artifacts are SHA-256 content-addressed and deduplicated.

Admin input upload:

`POST /artifacts?name=input.bin`

Worker output upload:

`POST /workers/{worker_id}/artifacts?name=result.bin`

The request body is raw bytes. Optional `X-Artifact-Sha256` lets the controller reject a corrupted upload. The response contains:

```json
{
  "artifact_id":"<sha256>",
  "sha256":"<sha256>",
  "name":"input.bin",
  "content_type":"application/octet-stream",
  "size_bytes":1048576
}
```

Authenticated download:

`GET /artifacts/{artifact_id}`

Workers additionally send `X-Worker-ID: <worker_id>` because the bearer token is device-scoped. A worker is allowed to download an artifact only while it holds an active lease whose payload references that artifact ID.

## Local agent artifact contract

The wire protocol only knows artifact IDs. Agent implementations translate them into local sandbox files before invoking a task.

A payload may include:

```json
{
  "artifact_inputs":[
    {"artifact_id":"<sha256>","alias":"weights","name":"model.bin"}
  ]
}
```

The Python, Rust, and Android agents expose local paths to their task handler. A trusted local task may declare files created inside its unit sandbox as outputs. The agent uploads those files and replaces the private output declaration with public artifact metadata in the submitted result.

## Result

`POST /workers/{worker_id}/units/{unit_id}/result`

```json
{
  "lease_id":"uuid",
  "result": {
    "score":0.98,
    "artifacts":[{"artifact_id":"<sha256>","name":"output.bin"}]
  },
  "elapsed_ms":4123.5
}
```

Failures use `/failure` with a short error string and explicit retry flag.
