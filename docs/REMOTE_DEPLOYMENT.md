# Remote controller deployment

Workers always initiate connections to the controller. No inbound ports are required on phones, PCs, SBCs, or other worker devices, which means normal NAT, carrier NAT, hotspots, and home routers are compatible.

## Controller

A minimal Docker deployment:

```bash
export SWARM_ADMIN_TOKEN="$(openssl rand -hex 32)"
export SWARM_ENROLLMENT_TOKEN="$(openssl rand -hex 32)"
docker compose up -d --build
```

The controller listens on port `8765`. For Internet-facing use, put it behind a TLS reverse proxy and do not expose plaintext HTTP publicly.

Example Caddy configuration:

```caddyfile
swarm.example.com {
    reverse_proxy 127.0.0.1:8765
}
```

Then point every remote worker at:

```bash
export SWARM_CONTROLLER_URL=https://swarm.example.com
```

## Network model

```text
Android phone ─┐
Linux PC ──────┤
Windows PC ────┼── outbound HTTPS ──> controller.example.com
Raspberry Pi ──┤
Remote server ─┘
```

The same swarm may mix local hotspot devices and remote devices. Scheduling is based on advertised capabilities and labels, not network location.

## Suggested labels

Use labels to keep control over placement without creating separate clusters:

```bash
SWARM_LABELS=site=shop,owner=lab,rack=phone-cart
```

Jobs can require any subset of those labels.

## Artifact storage

`SWARM_ARTIFACT_DIR` should live on persistent storage. `SWARM_MAX_ARTIFACT_BYTES` defaults to 2 GiB per artifact and may be changed by the controller operator.

For a larger deployment the same API can later be backed by S3-compatible object storage without changing worker task semantics.
