import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "coordinator"))

import app
import pairing


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "pairing.db")
    monkeypatch.setattr(app, "ADMIN_TOKEN", "admin")
    monkeypatch.setattr(pairing, "PAIRING_SECRET", "test-pairing-secret")
    app.init_db()
    pairing._init_pairing_db()


def test_pairing_requires_controller_approval_and_returns_stable_identity(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    with TestClient(app.app) as client:
        created = client.post(
            "/pairing/request",
            json={
                "name": "windows-gpu-node",
                "os_name": "Windows",
                "platform": "Windows 11 Pro",
                "arch": "AMD64",
                "gpu_name": "NVIDIA GeForce RTX 4080",
                "labels": {"gpu_vendor": "nvidia"},
            },
        )
        assert created.status_code == 200
        request_id = created.json()["request_id"]
        claim_secret = created.json()["claim_secret"]

        pending_poll = client.get(
            f"/pairing/request/{request_id}", params={"secret": claim_secret}
        )
        assert pending_poll.status_code == 200
        assert pending_poll.json()["status"] == "pending"

        unauthorized = client.get("/pairing/pending")
        assert unauthorized.status_code == 401

        pending = client.get(
            "/pairing/pending", headers={"Authorization": "Bearer admin"}
        )
        assert pending.status_code == 200
        assert len(pending.json()["requests"]) == 1
        assert pending.json()["requests"][0]["gpu_name"] == "NVIDIA GeForce RTX 4080"

        approved = client.post(
            f"/pairing/request/{request_id}/approve",
            headers={"Authorization": "Bearer admin"},
        )
        assert approved.status_code == 200

        claimed = client.get(
            f"/pairing/request/{request_id}", params={"secret": claim_secret}
        )
        assert claimed.status_code == 200
        identity = claimed.json()
        assert identity["status"] == "approved"
        assert identity["worker_id"]
        assert identity["device_token"]

        repeated = client.get(
            f"/pairing/request/{request_id}", params={"secret": claim_secret}
        )
        assert repeated.status_code == 200
        assert repeated.json()["worker_id"] == identity["worker_id"]
        assert repeated.json()["device_token"] == identity["device_token"]

        registered = client.post(
            f"/workers/{identity['worker_id']}/register",
            headers={"Authorization": f"Bearer {identity['device_token']}"},
            json={
                "name": "windows-gpu-node",
                "os_name": "Windows",
                "platform": "Windows 11 Pro",
                "arch": "AMD64",
                "cores": 16,
                "memory_mb": 32768,
                "benchmark": 10.0,
                "capabilities": ["cpu", "cuda", "task:prime_count"],
                "labels": {"gpu_vendor": "nvidia"},
            },
        )
        assert registered.status_code == 200


def test_pairing_can_be_denied(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    with TestClient(app.app) as client:
        created = client.post("/pairing/request", json={"name": "unknown-node"}).json()
        denied = client.post(
            f"/pairing/request/{created['request_id']}/deny",
            headers={"Authorization": "Bearer admin"},
        )
        assert denied.status_code == 200

        poll = client.get(
            f"/pairing/request/{created['request_id']}",
            params={"secret": created["claim_secret"]},
        )
        assert poll.status_code == 200
        assert poll.json()["status"] == "denied"
