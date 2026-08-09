from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Literal

from fastapi import Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app import admin_auth, app
from llm_experiments import OLLAMA_MODEL, OLLAMA_URL


class OllamaMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=20_000)


class OllamaChatRequest(BaseModel):
    messages: list[OllamaMessage] = Field(min_length=1, max_length=64)
    model: str | None = Field(default=None, max_length=200)
    temperature: float = Field(default=0.3, ge=0, le=1)


def _ollama_request(path: str, payload: dict[str, Any] | None = None, *, timeout: int = 180) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{OLLAMA_URL}{path}",
        data=data,
        method="GET" if payload is None else "POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
        except Exception:
            pass
        raise HTTPException(502, f"Ollama returned HTTP {exc.code}: {detail or exc.reason}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise HTTPException(503, f"Ollama unavailable at {OLLAMA_URL}: {exc}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(502, "Ollama returned an invalid JSON response") from exc
    if not isinstance(body, dict):
        raise HTTPException(502, "Ollama returned an unexpected response")
    return body


@app.get("/llm/status", dependencies=[Depends(admin_auth)])
def ollama_status():
    body = _ollama_request("/api/tags")
    models: list[str] = []
    for item in body.get("models") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("model")
        if name:
            models.append(str(name))
    return {
        "ok": True,
        "url": OLLAMA_URL,
        "default_model": OLLAMA_MODEL,
        "models": sorted(set(models)),
    }


@app.post("/llm/chat", dependencies=[Depends(admin_auth)])
def ollama_chat(req: OllamaChatRequest):
    if sum(len(message.content) for message in req.messages) > 100_000:
        raise HTTPException(400, "chat history is larger than 100,000 characters")
    model = req.model or OLLAMA_MODEL
    body = _ollama_request(
        "/api/chat",
        {
            "model": model,
            "messages": [message.model_dump() for message in req.messages],
            "stream": False,
            "options": {"temperature": req.temperature},
        },
    )
    message = body.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise HTTPException(502, "Ollama chat response did not contain message.content")
    return {
        "model": str(body.get("model") or model),
        "message": {
            "role": str(message.get("role") or "assistant"),
            "content": message["content"],
        },
        "done": bool(body.get("done", True)),
        "metrics": {
            key: body.get(key)
            for key in (
                "total_duration",
                "load_duration",
                "prompt_eval_count",
                "prompt_eval_duration",
                "eval_count",
                "eval_duration",
            )
            if body.get(key) is not None
        },
    }


OLLAMA_HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ollama · Compute Swarm</title>
<style>
:root{color-scheme:dark;--bg:#0b1020;--panel:#141b2d;--line:#28334d;--text:#eef2ff;--muted:#9aa6bd;--accent:#8b7cff;--ok:#56d39b;--bad:#ff6b7a}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at top,#18213b 0,#0b1020 38rem);color:var(--text);font:15px/1.45 system-ui,sans-serif}.wrap{max-width:1100px;margin:auto;padding:26px 16px 48px}
a{color:#c0b8ff}.nav{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:12px}.card{background:var(--panel);border:1px solid var(--line);border-radius:15px;padding:16px;margin-top:14px}.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}.field{margin:8px 0}label{display:block;color:var(--muted);font-size:12px;margin-bottom:5px}
input,select,textarea{width:100%;background:#0d1425;border:1px solid var(--line);border-radius:10px;color:var(--text);padding:10px;outline:none}textarea{min-height:100px;resize:vertical}button{border:0;border-radius:10px;padding:10px 14px;background:var(--accent);color:white;font-weight:700;cursor:pointer}button.secondary{background:#27314c}button:disabled{opacity:.5}.hint{color:var(--muted)}.ok{color:var(--ok)}.bad{color:var(--bad)}
.chat{height:420px;overflow:auto;background:#0d1425;border:1px solid var(--line);border-radius:12px;padding:12px}.msg{padding:10px 12px;border-radius:10px;margin:8px 0;white-space:pre-wrap;overflow-wrap:anywhere}.user{background:#252f4a;margin-left:9%}.assistant{background:#172138;margin-right:9%}.system{background:#241f35;color:#cfc7e8}.who{font-size:11px;color:var(--muted);text-transform:uppercase;margin-bottom:4px}.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#0d1425;border:1px solid var(--line);border-radius:10px;padding:12px;min-height:80px}
@media(max-width:760px){.row{grid-template-columns:1fr}.chat{height:360px}}
</style>
</head>
<body><div class="wrap">
<div class="nav"><a href="/">← Dashboard</a><a href="/pokemon">Pokémon CABT</a><a href="/docs">API docs</a></div>
<h1>Ollama</h1><div class="hint">Local-model chat and natural-language experiment drafting through the Compute Swarm controller.</div>
<div class="card">
 <div class="row">
  <div class="field"><label>Admin token</label><input id="token" type="password" autocomplete="off" placeholder="Controller admin token"></div>
  <div class="field"><label>Model</label><select id="model"><option>Loading…</option></select></div>
 </div>
 <div class="field"><label>Optional system prompt</label><input id="system" placeholder="You are a concise scientific compute assistant."></div>
 <div class="actions"><button id="connect">Connect to Ollama</button><button id="clear" class="secondary">Clear chat</button></div>
 <div id="status" class="hint" style="margin-top:8px">Enter the controller admin token.</div>
</div>
<div class="card">
 <h2>Chat</h2>
 <div id="chat" class="chat"><div class="hint">Messages stay in this browser session; the controller forwards them to its configured Ollama instance.</div></div>
 <div class="field"><label>Message</label><textarea id="prompt" placeholder="Ask the local model anything…"></textarea></div>
 <div class="actions"><button id="send">Send</button><span class="hint">Ctrl/Cmd + Enter to send</span></div>
</div>
<div class="card">
 <h2>Experiment assistant</h2>
 <div class="hint">Describe a distributed parameter sweep in normal language. Draft mode validates the JSON without starting work.</div>
 <div class="field"><textarea id="experiment" placeholder="Run 20 replicates of the available simulation while sweeping voltage from 100 to 400 and maximize metrics.efficiency."></textarea></div>
 <div class="actions"><button id="draft">Draft experiment</button><button id="launch" class="secondary">Draft & launch</button></div>
 <pre id="experimentResult">No experiment draft yet.</pre>
</div>
<script>
const $=id=>document.getElementById(id);let messages=[];
const headers=()=>({Authorization:'Bearer '+$('token').value.trim(),'Content-Type':'application/json'});
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function jsonFetch(path,opts={}){const r=await fetch(path,opts);let d=null;try{d=await r.json()}catch(_){ }if(!r.ok)throw new Error(d?.detail||`HTTP ${r.status}`);return d}
function render(){if(!messages.length){$('chat').innerHTML='<div class="hint">Start a conversation with the local model.</div>';return}$('chat').innerHTML=messages.map(m=>`<div class="msg ${esc(m.role)}"><div class="who">${esc(m.role)}</div>${esc(m.content)}</div>`).join('');$('chat').scrollTop=$('chat').scrollHeight}
async function connect(){try{const d=await jsonFetch('/llm/status',{headers:headers()});localStorage.setItem('swarmAdminToken',$('token').value.trim());const names=d.models.length?d.models:[d.default_model];$('model').innerHTML=names.map(n=>`<option value="${esc(n)}" ${n===d.default_model?'selected':''}>${esc(n)}</option>`).join('');$('status').className='ok';$('status').textContent=`Connected · ${names.length} model(s) · ${d.url}`;}catch(e){$('status').className='bad';$('status').textContent=e.message}}
async function send(){const text=$('prompt').value.trim();if(!text)return;$('send').disabled=true;try{if(!messages.length&&$('system').value.trim())messages.push({role:'system',content:$('system').value.trim()});messages.push({role:'user',content:text});$('prompt').value='';render();const d=await jsonFetch('/llm/chat',{method:'POST',headers:headers(),body:JSON.stringify({model:$('model').value,messages,temperature:.3})});messages.push(d.message);render();$('status').className='ok';$('status').textContent=`${d.model} replied${d.metrics?.eval_count?` · ${d.metrics.eval_count} tokens`:''}`;}catch(e){$('status').className='bad';$('status').textContent=e.message}finally{$('send').disabled=false}}
async function experiment(mode){const text=$('experiment').value.trim();if(!text)return;const button=mode==='launch'?$('launch'):$('draft');button.disabled=true;try{const d=await jsonFetch('/llm/experiments',{method:'POST',headers:headers(),body:JSON.stringify({request:text,mode,model:$('model').value})});$('experimentResult').textContent=JSON.stringify(d,null,2);}catch(e){$('experimentResult').textContent=e.message}finally{button.disabled=false}}
$('connect').onclick=connect;$('send').onclick=send;$('clear').onclick=()=>{messages=[];render()};$('draft').onclick=()=>experiment('draft');$('launch').onclick=()=>experiment('launch');$('prompt').addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key==='Enter')send()});const saved=localStorage.getItem('swarmAdminToken');if(saved){$('token').value=saved;connect()}
</script></div></body></html>'''


@app.get("/ollama", response_class=HTMLResponse, include_in_schema=False)
@app.get("/llm", response_class=HTMLResponse, include_in_schema=False)
def ollama_dashboard():
    return HTMLResponse(OLLAMA_HTML)
