from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import admin_auth, app


AGENT_URL = os.getenv("SWARM_AGENT_URL", "http://127.0.0.1:8766")
AGENT_TOKEN = os.getenv("SWARM_AGENT_TOKEN", "")
TALKINGHEAD_DIR = os.getenv("SWARM_TALKINGHEAD_DIR", "/opt/compute-swarm/talkinghead")
TUNNEL_URL_FILE = os.getenv("SWARM_TUNNEL_URL_FILE", "/var/lib/compute-swarm/cloudflared-url.txt")

if os.path.isdir(TALKINGHEAD_DIR):
    app.mount("/agent-assets/talkinghead", StaticFiles(directory=TALKINGHEAD_DIR), name="agent-talkinghead")


def _agent_request(path: str, payload: dict[str, Any] | None = None, *, timeout: int = 600) -> Any:
    if not AGENT_TOKEN:
        raise HTTPException(503, "SWARM_AGENT_TOKEN is not configured on the controller")
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{AGENT_URL}{path}",
        data=data,
        method="GET" if payload is None else "POST",
        headers={"Content-Type": "application/json", "X-Agent-Token": AGENT_TOKEN},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:2000]
        except Exception:
            pass
        raise HTTPException(exc.code, detail or exc.reason) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise HTTPException(503, f"local root agent unavailable at {AGENT_URL}: {exc}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(502, "local root agent returned invalid JSON") from exc


def _trusted_agent_url() -> str:
    try:
        with open(TUNNEL_URL_FILE, "r", encoding="utf-8") as handle:
            url = handle.read().strip().rstrip("/")
            if url.startswith("https://") and ".trycloudflare.com" in url:
                return url + "/agent"
    except OSError:
        pass
    return ""


@app.get("/agent/status", dependencies=[Depends(admin_auth)])
def agent_status():
    return JSONResponse(_agent_request("/status", timeout=20))


@app.post("/agent/chat", dependencies=[Depends(admin_auth)])
async def agent_chat(request: Request):
    payload = await request.json()
    return JSONResponse(_agent_request("/chat", payload))


@app.post("/agent/chat/start", dependencies=[Depends(admin_auth)])
async def agent_chat_start(request: Request):
    payload = await request.json()
    return JSONResponse(_agent_request("/chat/start", payload, timeout=20), status_code=202)


@app.get("/agent/chat/jobs/{job_id}", dependencies=[Depends(admin_auth)])
def agent_chat_job(job_id: str):
    return JSONResponse(_agent_request(f"/chat/jobs/{job_id}", timeout=20))


AGENT_HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent · Compute Swarm</title>
<style>
	:root{color-scheme:dark;--bg:#101114;--panel:#181b20;--panel2:#20242b;--line:#343a44;--text:#f4f5f7;--muted:#a9b0bb;--accent:#37c27a;--bad:#f36c6c}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
.app{display:grid;grid-template-columns:310px 1fr;min-height:100vh}.side{border-right:1px solid var(--line);background:#15171b;padding:18px;display:flex;flex-direction:column;gap:14px}
main{display:grid;grid-template-rows:auto 1fr auto;min-width:0}header{border-bottom:1px solid var(--line);padding:14px 18px;background:#14161a;display:flex;justify-content:space-between;gap:12px;align-items:center}
h1{font-size:18px;margin:0}.sub,.hint{color:var(--muted);font-size:12px}.nav{display:flex;gap:12px;flex-wrap:wrap}.nav a{color:#9bdcb9;text-decoration:none}
label{display:block;color:var(--muted);font-size:12px;margin-bottom:6px}input,select,textarea,button{width:100%;border:1px solid var(--line);border-radius:7px;background:var(--panel2);color:var(--text);font:inherit}
input,select{padding:10px}textarea{padding:12px;resize:vertical}button{padding:10px 12px;background:var(--accent);border-color:#2ca96a;color:#07120c;font-weight:700;cursor:pointer}button.secondary{background:var(--panel2);color:var(--text);border-color:var(--line)}button.voice-on{background:#4da3ff;border-color:#2c80d1;color:#06111d}button:disabled{opacity:.55}
	.row{display:flex;gap:8px}.row button{flex:1}.checkrow{display:flex;gap:8px;align-items:center;color:var(--muted);font-size:12px}.checkrow input{width:auto}.box{border:1px solid var(--line);background:var(--panel);border-radius:7px;padding:12px;white-space:pre-wrap;overflow-wrap:anywhere}
	.avatarbox{height:240px;border:1px solid var(--line);background:#0f1115;border-radius:7px;position:relative;overflow:hidden}.avatarbox #avatar{position:absolute;inset:0;transform-origin:50% 62%}.avatarbox.live #avatar{animation:avatarIdle 3.8s ease-in-out infinite}.avatarbox.speaking #avatar{animation:avatarTalk .95s ease-in-out infinite}.avatarstatus{position:absolute;left:10px;right:10px;bottom:8px;color:var(--muted);font-size:12px;text-shadow:0 1px 2px #000}@keyframes avatarIdle{0%,100%{transform:translateY(0) rotate(0deg)}50%{transform:translateY(-3px) rotate(.45deg)}}@keyframes avatarTalk{0%,100%{transform:translateY(0) rotate(-.7deg) scale(1)}25%{transform:translateY(-4px) rotate(.8deg) scale(1.01)}50%{transform:translateY(1px) rotate(-.2deg) scale(1.005)}75%{transform:translateY(-2px) rotate(.6deg) scale(1.01)}}
.working{display:inline-flex;align-items:center;gap:8px;color:var(--accent);font-size:12px}.spin{width:12px;height:12px;border:2px solid #2f5e43;border-top-color:var(--accent);border-radius:50%;animation:spin 1s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}
.chat{overflow:auto;padding:20px;display:flex;flex-direction:column;gap:14px}.msg{max-width:1050px;border:1px solid var(--line);border-radius:7px;padding:14px;background:var(--panel);white-space:pre-wrap;overflow-wrap:anywhere}.user{align-self:flex-end;background:#1f2a24;border-color:#2f5e43}.assistant{align-self:flex-start}.role{color:var(--muted);font-size:12px;margin-bottom:8px;text-transform:uppercase}
form{border-top:1px solid var(--line);padding:14px 18px;background:#14161a;display:grid;grid-template-columns:1fr 110px 120px;gap:10px}form textarea{min-height:58px;max-height:240px}.bad{color:var(--bad)}
@media(max-width:780px){.app{grid-template-columns:1fr}.side{border-right:0;border-bottom:1px solid var(--line)}form{grid-template-columns:1fr}}
</style>
	<script type="importmap">
	{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.180.0/build/three.module.js/+esm","three/addons/":"https://cdn.jsdelivr.net/npm/three@0.180.0/examples/jsm/","talkinghead":"/agent-assets/talkinghead/modules/talkinghead.mjs"}}
	</script>
	</head>
<body><div class="app">
<aside class="side">
 <div><h1>Swarm Agent</h1><div class="sub">System tools, Ollama, and compute-swarm access.</div></div>
 <div class="nav"><a href="/">Dashboard</a><a href="/ollama">Ollama</a><a href="/docs">API</a></div>
 <div><label>Admin token</label><input id="token" type="password" autocomplete="off"></div>
 <div><label>Model</label><input id="model" value="swarm-fast"></div>
	 <div><label>Working directory</label><input id="cwd" value="/root"></div>
	 <div><label>System prompt</label><textarea id="system">You are Codex-like operator for this Ubuntu compute-swarm controller. Use tools when needed, be concise, and explain completed actions.</textarea></div>
	 <label class="checkrow"><input id="speak" type="checkbox"> Speak replies in this browser</label>
	 <label class="checkrow"><input id="avatarToggle" type="checkbox"> Avatar speaks replies</label>
	 <div id="avatarBox" class="avatarbox"><div id="avatar"></div><div id="avatarState" class="avatarstatus">Avatar off</div></div>
	 <div class="row"><button id="status" class="secondary" type="button">Status</button><button id="clear" class="secondary" type="button">Clear</button></div>
 <div><label>Agent Status</label><div id="state" class="box">Enter the controller admin token.</div></div>
 <div class="hint">The backing service is localhost-only and runs as root. Access through this page requires the swarm admin token.</div>
</aside>
<main>
 <header><div><h1>Agent Chat</h1><div class="sub">Can run shell commands and call swarm APIs through approved tools.</div></div><div class="hint">Port 8765</div></header>
 <section id="chat" class="chat"></section>
 <form id="form"><textarea id="prompt" placeholder="Ask the agent to inspect the host, manage services, use Ollama, submit swarm work, or use Composio tools..."></textarea><button id="voice" class="secondary" type="button">Voice</button><button id="send" type="submit">Send</button></form>
</main></div>
	<script type="module">
	import { TalkingHead } from "talkinghead";
	const $=id=>document.getElementById(id);let messages=[];let recognition=null;let listening=false;const trustedVoiceUrl='__TRUSTED_AGENT_URL__';
	let head=null;let avatarReady=false;let avatarBusy=false;let handTimer=null;let mouthTimer=null;let mouthUntil=0;
	function headers(){return {Authorization:'Bearer '+$('token').value.trim(),'Content-Type':'application/json'}}
	function add(role,text){const el=document.createElement('div');el.className='msg '+role;el.innerHTML='<div class="role"></div><div></div>';el.firstChild.textContent=role;el.lastChild.textContent=text;$('chat').appendChild(el);$('chat').scrollTop=$('chat').scrollHeight;return el.lastChild}
	function setMouth(amount){if(!head||!head.armature)return;const weights={jawOpen:.2,mouthOpen:.16,viseme_aa:.18,viseme_E:.07,viseme_O:.055,viseme_U:.045};head.armature.traverse(o=>{if(!o.morphTargetDictionary||!o.morphTargetInfluences)return;for(const [name,weight] of Object.entries(weights)){const i=o.morphTargetDictionary[name];if(i!==undefined)o.morphTargetInfluences[i]=amount*weight}})}
	function pulseMouth(strength=.55,duration=170){mouthUntil=Math.max(mouthUntil,Date.now()+duration);setMouth(strength);setTimeout(()=>{if(Date.now()>=mouthUntil)setMouth(.04)},duration)}
	function startMouth(text){clearInterval(mouthTimer);const words=Math.max(1,(text.match(/\S+/g)||[]).length);const duration=Math.max(1800,Math.min(18000,words*310));let elapsed=0;setMouth(.03);mouthTimer=setInterval(()=>{elapsed+=190;if(elapsed>duration){stopMouth();return}const phrasePause=elapsed%1700<260;const strength=phrasePause?.08:(.28+Math.random()*.24);pulseMouth(strength,phrasePause?90:150+Math.random()*80)},190)}
	function stopMouth(){clearInterval(mouthTimer);mouthTimer=null;setMouth(0)}
	async function initAvatar(){if(avatarReady||avatarBusy)return avatarReady;avatarBusy=true;$('avatarBox').classList.add('live');$('avatarState').textContent='Loading avatar...';try{head=new TalkingHead($('avatar'),{lipsyncModules:['en'],cameraView:'head',modelPixelRatio:Math.min(window.devicePixelRatio||1,1.5),modelFPS:24,avatarSpeakingEyeContact:0.8,avatarSpeakingHeadMove:0.65});await head.showAvatar({url:'/agent-assets/talkinghead/avatars/brunette-t.glb',body:'F',avatarMood:'neutral',lipsyncLang:'en',baseline:{headRotateX:-0.04,eyeBlinkLeft:0.12,eyeBlinkRight:0.12}},ev=>{if(ev.lengthComputable)$('avatarState').textContent='Loading avatar '+Math.min(100,Math.round(ev.loaded/ev.total*100))+'%'});avatarReady=true;$('avatarState').textContent='Avatar ready';return true}catch(err){console.error(err);$('avatarState').textContent='Avatar unavailable: '+err.message;return false}finally{avatarBusy=false}}
	function startAvatar(text){if(!$('avatarToggle').checked||!text)return;$('avatarBox').classList.add('live','speaking');$('avatarState').textContent='Avatar speaking';initAvatar().then(ok=>{if(!ok||!head)return;try{head.setMood('neutral');head.lookAtCamera(500);head.startSpeaking(true);startMouth(text);clearInterval(handTimer);handTimer=setInterval(()=>{try{head.speakWithHands(0,0.45)}catch(_){}},1700)}catch(err){console.error(err)}})}
	function stopAvatar(){clearInterval(handTimer);handTimer=null;stopMouth();$('avatarBox').classList.remove('speaking');if($('avatarToggle').checked){$('avatarBox').classList.add('live');$('avatarState').textContent=avatarReady?'Avatar ready':'Avatar on'}else{$('avatarBox').classList.remove('live');$('avatarState').textContent='Avatar off'}if(head){try{head.stopSpeaking();head.setMood('neutral')}catch(err){console.error(err)}}}
	function speak(text){if(!text)return;const clean=text.replace(/\s+/g,' ').slice(0,1200);const shouldSpeak=$('speak').checked||$('avatarToggle').checked;startAvatar(clean);if(!shouldSpeak||!('speechSynthesis'in window)){if($('avatarToggle').checked){$('avatarState').textContent='Avatar moving; browser speech unavailable';setTimeout(stopAvatar,Math.max(1800,Math.min(9000,clean.length*45)))}return}window.speechSynthesis.cancel();const u=new SpeechSynthesisUtterance(clean);u.rate=.94;u.pitch=.96;u.onstart=()=>{if($('avatarToggle').checked)$('avatarState').textContent='Avatar speaking'};u.onboundary=e=>{if($('avatarToggle').checked&&e.name==='word')pulseMouth(.38,150)};u.onend=stopAvatar;u.onerror=e=>{if($('avatarToggle').checked)$('avatarState').textContent='Speech blocked: '+(e.error||'browser error');setTimeout(stopAvatar,1200)};window.speechSynthesis.speak(u);setTimeout(()=>{if(!window.speechSynthesis.speaking&&$('avatarToggle').checked)$('avatarState').textContent='Tap Send/Voice once more if browser audio was blocked'},900)}
async function req(path,body){const r=await fetch(path,{method:body?'POST':'GET',headers:headers(),body:body?JSON.stringify(body):undefined});let d=null;try{d=await r.json()}catch(_){ }if(!r.ok)throw new Error(d?.detail||`HTTP ${r.status}`);return d}
async function status(){try{localStorage.setItem('swarmAdminToken',$('token').value.trim());const d=await req('/agent/status');$('state').textContent=JSON.stringify(d,null,2)}catch(e){$('state').innerHTML='<span class="bad">'+e.message+'</span>'}}
async function send(e){e.preventDefault();const text=$('prompt').value.trim();if(!text)return;$('prompt').value='';add('user',text);messages.push({role:'user',content:text});$('send').disabled=true;const old=$('send').textContent;$('send').textContent='Running';const started=Date.now();const pending=add('assistant','Starting...');$('state').innerHTML='<span class="working"><span class="spin"></span><span id="elapsed">Starting agent job...</span></span>';let timer=null;try{localStorage.setItem('swarmAdminToken',$('token').value.trim());const start=await req('/agent/chat/start',{model:$('model').value,cwd:$('cwd').value,system:$('system').value,messages});timer=setInterval(()=>{const s=Math.floor((Date.now()-started)/1000);pending.textContent=`Working... ${s}s elapsed`;const el=$('elapsed');if(el)el.textContent=`Agent job ${start.job_id.slice(0,8)} running... ${s}s elapsed`},1000);let d=null;while(true){await new Promise(r=>setTimeout(r,1000));d=await req('/agent/chat/jobs/'+encodeURIComponent(start.job_id));const s=d.elapsed_seconds ?? Math.floor((Date.now()-started)/1000);pending.textContent=`Working... ${s}s elapsed`;if(d.status==='done'){d=d.result;break}if(d.status==='error'||d.status==='missing')throw new Error(d.error||d.status)}messages=d.messages||messages.concat([{role:'assistant',content:d.final||''}]);pending.textContent=d.final||'(no final response)';speak(d.final||'');$('state').textContent=`completed in ${Math.floor((Date.now()-started)/1000)}s · tool calls: ${d.tool_calls||0}`+(d.trace?`\n\n${d.trace}`:'')}catch(err){pending.textContent='Error: '+err.message;$('state').innerHTML='<span class="bad">'+err.message+'</span>'}finally{if(timer)clearInterval(timer);$('send').disabled=false;$('send').textContent=old}}
function setupVoice(){const SpeechRecognition=window.SpeechRecognition||window.webkitSpeechRecognition;if(!window.isSecureContext){$('voice').onclick=()=>{const link=trustedVoiceUrl?'<a href="'+trustedVoiceUrl+'">'+trustedVoiceUrl+'</a>':'the current Cloudflare tunnel URL from the server console';$('state').innerHTML='<span class="bad">Voice input needs a browser-trusted HTTPS page. Open '+link+', then tap Voice again.</span>';$('prompt').focus()};return}if(!SpeechRecognition){$('voice').onclick=()=>{$('state').innerHTML='<span class="bad">This browser does not expose speech recognition. Tap the text box and use your keyboard microphone.</span>';$('prompt').focus()};return}recognition=new SpeechRecognition();recognition.continuous=false;recognition.interimResults=true;recognition.lang=navigator.language||'en-US';recognition.onstart=()=>{listening=true;$('voice').classList.add('voice-on');$('voice').textContent='Listening'};recognition.onend=()=>{listening=false;$('voice').classList.remove('voice-on');$('voice').textContent='Voice'};recognition.onerror=e=>{const extra=trustedVoiceUrl?' Open <a href="'+trustedVoiceUrl+'">'+trustedVoiceUrl+'</a>.':'';$('state').innerHTML='<span class="bad">Voice input: '+(e.error||'error')+'.'+extra+'</span>'};recognition.onresult=e=>{let text='';for(let i=e.resultIndex;i<e.results.length;i++){text+=e.results[i][0].transcript}if(text.trim())$('prompt').value=text.trim();if(e.results[e.results.length-1].isFinal&&$('prompt').value.trim())$('form').requestSubmit()};$('voice').onclick=()=>{if(listening){recognition.stop()}else{recognition.start()}}}
	$('speak').onchange=()=>localStorage.setItem('swarmSpeakReplies',$('speak').checked?'1':'0');$('avatarToggle').onchange=()=>{localStorage.setItem('swarmAvatarReplies',$('avatarToggle').checked?'1':'0');if($('avatarToggle').checked){$('speak').checked=true;localStorage.setItem('swarmSpeakReplies','1');$('avatarBox').classList.add('live');initAvatar()}else stopAvatar()};$('status').onclick=status;$('clear').onclick=()=>{messages=[];$('chat').innerHTML='';if('speechSynthesis'in window)window.speechSynthesis.cancel();stopAvatar()};$('form').onsubmit=send;$('prompt').addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key==='Enter')$('form').requestSubmit()});setupVoice();const savedSpeak=localStorage.getItem('swarmSpeakReplies');if(savedSpeak==='1')$('speak').checked=true;const savedAvatar=localStorage.getItem('swarmAvatarReplies');if(savedAvatar==='1'){$('avatarToggle').checked=true;$('speak').checked=true;$('avatarBox').classList.add('live');initAvatar()}const saved=localStorage.getItem('swarmAdminToken');if(saved){$('token').value=saved;status()}
</script></body></html>'''


@app.get("/agent", response_class=HTMLResponse, include_in_schema=False)
def agent_dashboard():
    return HTMLResponse(AGENT_HTML.replace("__TRUSTED_AGENT_URL__", _trusted_agent_url()))
