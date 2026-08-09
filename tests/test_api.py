import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "coordinator"))

import app


def test_end_to_end_enroll_schedule_and_result(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(app, "ADMIN_TOKEN", "admin")
    monkeypatch.setattr(app, "ENROLLMENT_TOKEN", "enroll")
    app.init_db()

    with TestClient(app.app) as client:
        enrolled = client.post(
            "/workers/enroll",
            headers={"Authorization": "Bearer enroll"},
            json={"name": "test-node"},
        )
        assert enrolled.status_code == 200
        identity = enrolled.json()
        wid = identity["worker_id"]
        worker_headers = {"Authorization": f"Bearer {identity['device_token']}"}

        registered = client.post(
            f"/workers/{wid}/register",
            headers=worker_headers,
            json={
                "name": "test-node",
                "os_name": "Linux",
                "platform": "Linux-test",
                "arch": "x86_64",
                "cores": 8,
                "memory_mb": 16000,
                "benchmark": 123.0,
                "capabilities": ["cpu", "task:prime_count"],
                "labels": {"site": "test"},
            },
        )
        assert registered.status_code == 200

        job = client.post(
            "/jobs",
            headers={"Authorization": "Bearer admin"},
            json={
                "kind": "prime_count",
                "units": [{"start": 2, "end": 20}],
                "requirements": {"capabilities": ["cpu"], "labels": {"site": "test"}},
            },
        )
        assert job.status_code == 200
        job_id = job.json()["job_id"]

        lease = client.post(f"/workers/{wid}/lease", headers=worker_headers)
        assert lease.status_code == 200
        work = lease.json()["work"]
        assert work["kind"] == "prime_count"

        result = client.post(
            f"/workers/{wid}/units/{work['unit_id']}/result",
            headers=worker_headers,
            json={"lease_id": work["lease_id"], "result": {"count": 8}, "elapsed_ms": 1.0},
        )
        assert result.status_code == 200

        detail = client.get(f"/jobs/{job_id}", headers={"Authorization": "Bearer admin"})
        assert detail.status_code == 200
        assert detail.json()["units"][0]["status"] == "done"
        assert detail.json()["units"][0]["result"] == {"count": 8}
