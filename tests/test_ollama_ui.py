from pathlib import Path
import sys

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "coordinator"))

import app
import ollama_ui
import server


def test_ollama_ui_status_and_chat(monkeypatch):
    monkeypatch.setattr(app, "ADMIN_TOKEN", "admin")

    def fake_request(path, payload=None, *, timeout=180):
        if path == "/api/tags":
            return {"models": [{"name": "qwen2.5:7b"}, {"name": "llama3.2:3b"}]}
        if path == "/api/chat":
            assert payload["model"] == "qwen2.5:7b"
            assert payload["messages"][-1] == {"role": "user", "content": "hello"}
            return {
                "model": "qwen2.5:7b",
                "message": {"role": "assistant", "content": "hi from ollama"},
                "done": True,
                "eval_count": 4,
            }
        raise AssertionError(path)

    monkeypatch.setattr(ollama_ui, "_ollama_request", fake_request)

    with TestClient(server.app) as client:
        page = client.get("/ollama")
        assert page.status_code == 200
        assert "Experiment assistant" in page.text
        assert "/llm/chat" in page.text

        unauthorized = client.get("/llm/status")
        assert unauthorized.status_code == 401

        headers = {"Authorization": "Bearer admin"}
        status = client.get("/llm/status", headers=headers)
        assert status.status_code == 200
        assert status.json()["models"] == ["llama3.2:3b", "qwen2.5:7b"]

        chat = client.post(
            "/llm/chat",
            headers=headers,
            json={
                "model": "qwen2.5:7b",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        assert chat.status_code == 200
        data = chat.json()
        assert data["message"]["content"] == "hi from ollama"
        assert data["metrics"]["eval_count"] == 4


def test_ollama_chat_history_limit(monkeypatch):
    monkeypatch.setattr(app, "ADMIN_TOKEN", "admin")
    called = False

    def fake_request(path, payload=None, *, timeout=180):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(ollama_ui, "_ollama_request", fake_request)
    headers = {"Authorization": "Bearer admin"}
    with TestClient(server.app) as client:
        response = client.post(
            "/llm/chat",
            headers=headers,
            json={"messages": [{"role": "user", "content": "x" * 20_000}] * 6},
        )
        assert response.status_code == 400
        assert called is False
