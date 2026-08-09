# Ollama controller interface

The controller exposes an admin-token-protected Ollama interface at:

```text
http://<controller>:8765/ollama
```

It provides:

- local-model discovery via `GET /llm/status`
- browser chat via `POST /llm/chat`
- natural-language experiment drafting/launching via the existing `POST /llm/experiments`
- browser-session chat history (the controller does not persist chat history)

## Default configuration

The controller defaults to:

```text
SWARM_OLLAMA_URL=http://127.0.0.1:11434
SWARM_OLLAMA_MODEL=qwen2.5:7b
```

If Ollama runs on the controller host, no URL change is needed. Make sure Ollama is running and the desired model is installed.

If Ollama runs elsewhere, add the settings to `/etc/compute-swarm/controller.env`, for example:

```text
SWARM_OLLAMA_URL=http://192.168.1.50:11434
SWARM_OLLAMA_MODEL=qwen2.5:7b
```

Then restart the controller:

```bash
sudo systemctl restart compute-swarm-controller
```

Do not expose Ollama's unauthenticated API directly to the public Internet. Keep Ollama private and access it through the authenticated Compute Swarm controller or another properly secured reverse proxy.

## API examples

Status/model list:

```bash
curl -H "Authorization: Bearer $SWARM_ADMIN_TOKEN" \
  http://127.0.0.1:8765/llm/status
```

Chat:

```bash
curl -X POST \
  -H "Authorization: Bearer $SWARM_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  http://127.0.0.1:8765/llm/chat \
  -d '{
    "model":"qwen2.5:7b",
    "messages":[{"role":"user","content":"Summarize the swarm status."}]
  }'
```

The browser page also exposes the experiment assistant. Draft mode validates generated experiment JSON without starting work; launch mode submits the validated experiment to the existing adaptive-pull experiment engine.
