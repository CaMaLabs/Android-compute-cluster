# Rust worker

Cross-platform worker for Linux, Windows, macOS, Raspberry Pi/SBCs, and other Rust-supported targets.

The controller never supplies an executable or command. Built-in task handlers are compiled into the worker. Optional local plugins are explicitly configured by the device owner in `SWARM_RUST_PLUGIN_CONFIG`; the controller can only select the registered task name and send JSON input.

```bash
cargo build --release
export SWARM_CONTROLLER_URL=https://swarm.example.com
export SWARM_ENROLLMENT_TOKEN='change-me'
./target/release/compute-swarm-worker
```

For a trusted LAN only:

```bash
export SWARM_ALLOW_INSECURE_REMOTE=1
export SWARM_CONTROLLER_URL=http://192.168.1.10:8765
```

Optional local task plugin config:

```bash
export SWARM_RUST_PLUGIN_CONFIG=$PWD/plugins.example.json
```

A local plugin receives the work-unit JSON on stdin, runs with its current directory set to the unit sandbox, and must write one JSON value to stdout. No shell is used.
