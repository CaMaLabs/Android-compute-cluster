import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "coordinator"))

import app
import experiments
import server


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "experiment.db")
    monkeypatch.setattr(app, "ARTIFACT_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(app, "ADMIN_TOKEN", "admin")
    monkeypatch.setattr(app, "ENROLLMENT_TOKEN", "enroll")
    app.init_db()


def _worker(client):
    identity = client.post(
        "/workers/enroll",
        headers={"Authorization": "Bearer enroll"},
        json={"name": "sweep-node"},
    ).json()
    wid = identity["worker_id"]
    headers = {"Authorization": f"Bearer {identity['device_token']}"}
    response = client.post(
        f"/workers/{wid}/register",
        headers=headers,
        json={
            "name": "sweep-node",
            "os_name": "Linux",
            "platform": "Linux-test",
            "arch": "x86_64",
            "cores": 8,
            "memory_mb": 16000,
            "benchmark": 1000,
            "capabilities": ["cpu", "task:prime_count"],
        },
    )
    assert response.status_code == 200
    return wid, headers


def test_dashboard_launcher_registers_root_route(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    with TestClient(server.app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "Compute Swarm" in response.text
        assert "New experiment" in response.text
        assert "/experiments" in response.text


def test_parameter_sweep_ranking_and_refinement(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    admin = {"Authorization": "Bearer admin"}
    with TestClient(server.app) as client:
        wid, worker_headers = _worker(client)
        created = client.post(
            "/experiments",
            headers=admin,
            json={
                "name": "prime sweep",
                "task": "prime_count",
                "parameters": {
                    "start": {"start": 2, "stop": 6, "step": 2},
                    "end": {"values": [20]},
                },
                "objective": {"path": "count", "direction": "maximize"},
                "requirements": {"capabilities": ["cpu"]},
            },
        )
        assert created.status_code == 200, created.text
        experiment_id = created.json()["experiment_id"]
        assert created.json()["parameter_points"] == 3
        assert created.json()["units"] == 3
        assert created.json()["scheduler"] == "adaptive_pull"

        counts = {2: 8, 4: 6, 6: 5}
        for _ in range(3):
            lease = client.post(f"/workers/{wid}/lease", headers=worker_headers)
            assert lease.status_code == 200
            work = lease.json()["work"]
            assert work is not None
            start = work["payload"]["start"]
            result = client.post(
                f"/workers/{wid}/units/{work['unit_id']}/result",
                headers=worker_headers,
                json={
                    "lease_id": work["lease_id"],
                    "result": {"count": counts[start]},
                    "elapsed_ms": 10.0,
                },
            )
            assert result.status_code == 200

        detail = client.get(f"/experiments/{experiment_id}", headers=admin)
        assert detail.status_code == 200, detail.text
        data = detail.json()
        assert data["done"] == 3
        assert data["best"]["parameters"] == {"start": 2, "end": 20}
        assert data["best"]["score"] == 8.0
        assert data["worker_throughput"][0]["units_done"] == 3

        refined = client.post(
            f"/experiments/{experiment_id}/refine",
            headers=admin,
            json={"top_k": 1, "shrink": 0.5, "points_per_axis": 3},
        )
        assert refined.status_code == 200, refined.text
        child = refined.json()
        assert child["parent_experiment_id"] == experiment_id
        assert child["generation"] == 1
        assert 1 <= child["parameter_points"] <= 3

        listing = client.get("/experiments", headers=admin)
        assert listing.status_code == 200
        assert len(listing.json()["experiments"]) == 2


def test_nested_objective_path():
    assert experiments._numeric_score({"metrics": {"efficiency": 0.91}}, {"path": "metrics.efficiency"}) == 0.91
    assert experiments._numeric_score({"values": [3, 9]}, {"path": "values.1"}) == 9.0
