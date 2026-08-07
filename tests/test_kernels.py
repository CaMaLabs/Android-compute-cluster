import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker"))

import worker
from swarm_plugin import CAPABILITIES, advertise


def test_prime_count():
    assert worker.prime_count({"start": 2, "end": 20})["count"] == 8


def test_monte_carlo_is_deterministic():
    payload = {"start": 0, "end": 1000}
    assert worker.monte_carlo_pi(payload) == worker.monte_carlo_pi(payload)


def test_task_capabilities_are_advertised():
    caps = set(worker.capabilities())
    assert "cpu" in caps
    assert "python" in caps
    assert "task:prime_count" in caps
    assert "task:monte_carlo_pi" in caps


def test_plugin_capability_registry_is_advertised():
    try:
        advertise("test:accelerator")
        assert "test:accelerator" in set(worker.capabilities())
    finally:
        CAPABILITIES.discard("test:accelerator")


def test_bundled_accelerator_plugin_is_optional_dependency_safe():
    # CI intentionally does not install CUDA/ONNX packages. Discovery must remain
    # a safe no-op instead of preventing ordinary CPU workers from starting.
    worker.load_plugins()
