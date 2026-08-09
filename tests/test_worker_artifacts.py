import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker"))
import worker


def test_text_task_declares_artifact_output(tmp_path):
    result = worker.text_artifact({"_work_dir": str(tmp_path), "name": "hello.txt", "text": "hello"})
    assert (tmp_path / "hello.txt").read_text() == "hello"
    assert result["_artifact_outputs"][0]["path"] == "hello.txt"


def test_worker_rejects_output_outside_sandbox(tmp_path, monkeypatch):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("do not upload")
    monkeypatch.setattr(worker, "upload_artifact", lambda *args, **kwargs: {"unexpected": True})
    try:
        worker.normalize_artifact_outputs(
            object(),
            "worker",
            tmp_path,
            {"_artifact_outputs": [{"path": "../outside.txt"}]},
        )
    except ValueError as exc:
        assert "sandbox" in str(exc)
    else:
        raise AssertionError("sandbox escape should fail")
