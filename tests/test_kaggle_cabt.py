import io
import sys
import tarfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "coordinator"))
sys.path.insert(0, str(ROOT / "worker"))

import app
import server
from plugins import kaggle_cabt as cabt_plugin


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "cabt.db")
    monkeypatch.setattr(app, "ARTIFACT_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(app, "ADMIN_TOKEN", "admin")
    monkeypatch.setattr(app, "ENROLLMENT_TOKEN", "enroll")
    app.init_db()


def _artifact(client, name, body=b"bundle"):
    r = client.post(
        f"/artifacts?name={name}",
        headers={"Authorization": "Bearer admin", "Content-Type": "application/gzip"},
        content=body,
    )
    assert r.status_code == 200, r.text
    return r.json()["artifact_id"]


def test_cabt_controller_creates_balanced_distributed_match(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    with TestClient(server.app) as client:
        a = _artifact(client, "candidate.tar.gz", b"a")
        b = _artifact(client, "opponent.tar.gz", b"b")
        created = client.post(
            "/kaggle/cabt/matches",
            headers={"Authorization": "Bearer admin"},
            json={
                "name": "candidate vs opponent",
                "agent_artifact_id": a,
                "opponent_artifact_id": b,
                "episodes": 4,
                "alternate_seats": True,
            },
        )
        assert created.status_code == 200, created.text
        match_id = created.json()["match_id"]

        identity = client.post(
            "/workers/enroll",
            headers={"Authorization": "Bearer enroll"},
            json={"name": "cabt-worker"},
        ).json()
        wid = identity["worker_id"]
        wh = {"Authorization": f"Bearer {identity['device_token']}"}
        registered = client.post(
            f"/workers/{wid}/register",
            headers=wh,
            json={
                "name": "cabt-worker",
                "os_name": "Linux",
                "platform": "test",
                "arch": "x86_64",
                "cores": 8,
                "benchmark": 1,
                "capabilities": [
                    "python",
                    "kaggle:cabt",
                    "task:kaggle_cabt_episode",
                ],
            },
        )
        assert registered.status_code == 200

        outcomes = ["agent_win", "opponent_win", "draw", "agent_win"]
        swaps = []
        for outcome in outcomes:
            lease = client.post(f"/workers/{wid}/lease", headers=wh).json()["work"]
            assert lease is not None
            swaps.append(lease["payload"]["seat_swap"])
            result = client.post(
                f"/workers/{wid}/units/{lease['unit_id']}/result",
                headers=wh,
                json={
                    "lease_id": lease["lease_id"],
                    "elapsed_ms": 100,
                    "result": {
                        "outcome": outcome,
                        "seat_swap": lease["payload"]["seat_swap"],
                        "agent_reward": 1 if outcome == "agent_win" else 0,
                        "opponent_reward": 1 if outcome == "opponent_win" else 0,
                        "agent_status": "DONE",
                        "opponent_status": "DONE",
                        "steps": 10,
                        "engine_version": "test",
                    },
                },
            )
            assert result.status_code == 200
        assert swaps == [False, True, False, True]

        detail = client.get(
            f"/kaggle/cabt/matches/{match_id}",
            headers={"Authorization": "Bearer admin"},
        )
        assert detail.status_code == 200
        data = detail.json()
        assert data["done"] == 4
        assert data["wins"] == 2
        assert data["losses"] == 1
        assert data["draws"] == 1
        assert data["score_rate"] == 0.625

        csv_result = client.get(
            f"/kaggle/cabt/matches/{match_id}/csv",
            headers={"Authorization": "Bearer admin"},
        )
        assert csv_result.status_code == 200
        assert "agent_win" in csv_result.text
        assert "engine_version" in csv_result.text


def _tar_bytes(files):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tf:
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return out.getvalue()


def test_cabt_bundle_validation_and_wrapper(tmp_path):
    bundle = tmp_path / "agent.tar.gz"
    bundle.write_bytes(
        _tar_bytes(
            {
                "main.py": "def agent(obs_dict):\n    return [0]\n",
                "deck.csv": "\n".join(["3"] * 60),
            }
        )
    )
    root = tmp_path / "agent"
    cabt_plugin._safe_extract(bundle, root)
    wrapper = cabt_plugin._write_wrapper(root, "test")
    text = wrapper.read_text()
    assert "_DECK" in text
    assert "main.py" in text


def test_cabt_bundle_rejects_path_traversal(tmp_path):
    bundle = tmp_path / "bad.tar.gz"
    bundle.write_bytes(_tar_bytes({"../escape.py": "bad"}))
    try:
        cabt_plugin._safe_extract(bundle, tmp_path / "out")
    except ValueError as exc:
        assert "unsafe archive path" in str(exc)
    else:
        raise AssertionError("path traversal was accepted")
