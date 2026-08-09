import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "coordinator"))

import app


def fake_worker(capabilities, os_name="Linux", arch="x86_64", cores=8, memory_mb=16000, labels=None):
    return {
        "capabilities_json": app._json(capabilities),
        "os_name": os_name,
        "arch": arch,
        "cores": cores,
        "memory_mb": memory_mb,
        "labels_json": app._json(labels or {}),
    }


def test_capability_match_requires_task_kind():
    worker = fake_worker(["cpu", "task:prime_count"])
    req = app._json({"capabilities": ["cpu"]})
    assert app._worker_matches(worker, req, "prime_count")
    assert not app._worker_matches(worker, req, "monte_carlo_pi")


def test_resource_and_label_constraints():
    worker = fake_worker(["cpu", "task:x"], cores=4, memory_mb=4096, labels={"site": "lab"})
    good = app._json({"min_cores": 4, "min_memory_mb": 2048, "labels": {"site": "lab"}})
    bad = app._json({"min_cores": 8})
    assert app._worker_matches(worker, good, "x")
    assert not app._worker_matches(worker, bad, "x")
