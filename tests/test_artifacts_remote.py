import hashlib
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "coordinator"))
import app


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "artifact.db")
    monkeypatch.setattr(app, "ARTIFACT_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(app, "ADMIN_TOKEN", "admin")
    monkeypatch.setattr(app, "ENROLLMENT_TOKEN", "enroll")
    app.init_db()


def _worker(client):
    enrolled = client.post(
        "/workers/enroll",
        headers={"Authorization": "Bearer enroll"},
        json={"name": "artifact-node"},
    ).json()
    wid = enrolled["worker_id"]
    headers = {"Authorization": f"Bearer {enrolled['device_token']}"}
    client.post(
        f"/workers/{wid}/register",
        headers=headers,
        json={
            "name": "artifact-node",
            "os_name": "Linux",
            "platform": "Linux-test",
            "arch": "x86_64",
            "cores": 4,
            "benchmark": 1,
            "capabilities": ["cpu", "task:sha256_artifact"],
        },
    )
    return wid, headers


def test_content_addressed_artifact_and_lease_scoped_download(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    content = b"large-input" * 4096
    digest = hashlib.sha256(content).hexdigest()
    with TestClient(app.app) as client:
        wid, worker_headers = _worker(client)
        uploaded = client.post(
            "/artifacts?name=input.bin",
            headers={"Authorization": "Bearer admin", "X-Artifact-Sha256": digest},
            content=content,
        )
        assert uploaded.status_code == 200
        assert uploaded.json()["artifact_id"] == digest

        forbidden = client.get(
            f"/artifacts/{digest}",
            headers={**worker_headers, "X-Worker-ID": wid},
        )
        assert forbidden.status_code == 403

        client.post(
            "/jobs",
            headers={"Authorization": "Bearer admin"},
            json={
                "kind": "sha256_artifact",
                "units": [{
                    "alias": "input",
                    "artifact_inputs": [{"artifact_id": digest, "alias": "input", "name": "input.bin"}],
                }],
            },
        )
        lease = client.post(f"/workers/{wid}/lease", headers=worker_headers)
        assert lease.json()["work"] is not None

        downloaded = client.get(
            f"/artifacts/{digest}",
            headers={**worker_headers, "X-Worker-ID": wid},
        )
        assert downloaded.status_code == 200
        assert downloaded.content == content


def test_worker_output_upload_is_recorded(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    with TestClient(app.app) as client:
        wid, worker_headers = _worker(client)
        uploaded = client.post(
            f"/workers/{wid}/artifacts?name=result.txt",
            headers={**worker_headers, "Content-Type": "text/plain"},
            content=b"result",
        )
        assert uploaded.status_code == 200
        artifact_id = uploaded.json()["artifact_id"]
        meta = client.get(
            f"/artifacts/{artifact_id}/meta",
            headers={"Authorization": "Bearer admin"},
        )
        assert meta.status_code == 200
        assert meta.json()["size_bytes"] == 6
