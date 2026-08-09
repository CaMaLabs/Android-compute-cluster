import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "coordinator"))

import app
import llm_experiments
import server


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "llm.db")
    monkeypatch.setattr(app, "ARTIFACT_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(app, "ADMIN_TOKEN", "admin")
    monkeypatch.setattr(app, "ENROLLMENT_TOKEN", "enroll")
    app.init_db()


def _worker(client):
    identity = client.post(
        "/workers/enroll",
        headers={"Authorization": "Bearer enroll"},
        json={"name": "llm-node"},
    ).json()
    headers = {"Authorization": f"Bearer {identity['device_token']}"}
    response = client.post(
        f"/workers/{identity['worker_id']}/register",
        headers=headers,
        json={
            "name": "llm-node",
            "os_name": "Linux",
            "platform": "Linux-test",
            "arch": "x86_64",
            "cores": 4,
            "memory_mb": 2048,
            "benchmark": 1000,
            "capabilities": ["cpu", "python", "os:linux", "task:prime_count"],
        },
    )
    assert response.status_code == 200


def test_llm_experiment_rejects_missing_prime_count_fields(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        llm_experiments,
        "_ollama_generate",
        lambda *args, **kwargs: """
        {"name":"bad","task":"prime_count","parameters":{"limit":{"values":["10000"]}},
        "objective":{"path":"count","direction":"maximize"},"requirements":{"capabilities":["cpu"]}}
        """,
    )

    with TestClient(server.app) as client:
        _worker(client)
        response = client.post(
            "/llm/experiments",
            headers={"Authorization": "Bearer admin"},
            json={"request": "bad prime count", "mode": "launch"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["validation"]["ok"] is False
    assert "start" in body["validation"]["error"]
    assert body["launched"] is None


def test_llm_experiment_coerces_numeric_strings_before_launch(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        llm_experiments,
        "_ollama_generate",
        lambda *args, **kwargs: """
        {"name":"good","task":"prime_count",
        "parameters":{"start":{"values":["2"]},"end":{"values":["20","30"]}},
        "objective":{"path":"count","direction":"maximize"},"requirements":{"capabilities":["cpu"]}}
        """,
    )

    with TestClient(server.app) as client:
        _worker(client)
        response = client.post(
            "/llm/experiments",
            headers={"Authorization": "Bearer admin"},
            json={"request": "good prime count", "mode": "launch"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["validation"]["ok"] is True
        first_lease = client.post(
            "/workers/enroll",
            headers={"Authorization": "Bearer enroll"},
            json={"name": "lease-node"},
        )

        assert first_lease.status_code == 200
        identity = first_lease.json()
        headers = {"Authorization": f"Bearer {identity['device_token']}"}
        registered = client.post(
            f"/workers/{identity['worker_id']}/register",
            headers=headers,
            json={
                "name": "lease-node",
                "os_name": "Linux",
                "platform": "Linux-test",
                "arch": "x86_64",
                "cores": 4,
                "memory_mb": 2048,
                "benchmark": 1000,
                "capabilities": ["cpu", "python", "os:linux", "task:prime_count"],
            },
        )
        assert registered.status_code == 200
        lease = client.post(f"/workers/{identity['worker_id']}/lease", headers=headers)

    assert lease.status_code == 200
    payload = lease.json()["work"]["payload"]
    assert payload["start"] == 2
    assert payload["end"] in {20, 30}
