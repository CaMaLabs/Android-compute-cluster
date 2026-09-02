from __future__ import annotations

from fastapi.responses import HTMLResponse

from app import app


DASHBOARD_HTML = r'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Compute Swarm Controller</title>
  <style>
    :root{color-scheme:dark;--bg:#0b1020;--panel:#141b2d;--panel2:#1a2338;--text:#eef2ff;--muted:#9aa6bd;--accent:#8b7cff;--ok:#56d39b;--bad:#ff6b7a;--warn:#f0b55b;--line:#28334d}
    *{box-sizing:border-box} body{margin:0;background:radial-gradient(circle at top,#18213b 0,#0b1020 36rem);color:var(--text);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
    .wrap{max-width:1240px;margin:auto;padding:28px 18px 48px}.top{display:flex;gap:16px;align-items:center;justify-content:space-between;flex-wrap:wrap;margin-bottom:20px}
    h1{font-size:28px;margin:0}.sub{color:var(--muted);margin-top:4px}.auth{display:flex;gap:8px;align-items:center;min-width:min(100%,460px)}
    input,textarea{background:#0d1425;border:1px solid var(--line);border-radius:11px;color:var(--text);padding:11px 12px;outline:none}.auth input{flex:1}textarea{width:100%;min-height:250px;resize:vertical;font:13px/1.45 ui-monospace,SFMono-Regular,Consolas,monospace}
    button{border:0;border-radius:11px;padding:11px 16px;background:var(--accent);color:white;font-weight:700;cursor:pointer}button.secondary{background:#27314c}button.danger{background:#813745}button.small{padding:7px 10px;font-size:12px}button:disabled{opacity:.5;cursor:not-allowed}
    .statusline{display:flex;gap:10px;align-items:center;color:var(--muted);margin:10px 0 18px}.dot{width:10px;height:10px;border-radius:50%;background:#71809b}.dot.ok{background:var(--ok)}.dot.bad{background:var(--bad)}
    .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:18px}.card{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:16px;padding:16px;box-shadow:0 12px 28px #0003}
    .metric{font-size:29px;font-weight:800;margin-top:4px}.label{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.08em}
    .section{margin-top:16px}.section h2{font-size:18px;margin:0}.sectionhead{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px;flex-wrap:wrap}.hint{color:var(--muted);font-size:13px;margin:5px 0 12px}
    .pairing{border-color:#6f61dc;box-shadow:0 0 0 1px #6f61dc55,0 12px 28px #0003}.pairing h2{display:flex;gap:8px;align-items:center}.badge{display:inline-flex;min-width:24px;height:24px;padding:0 7px;border-radius:999px;align-items:center;justify-content:center;background:var(--warn);color:#17120b;font-size:12px;font-weight:800}
    table{width:100%;border-collapse:collapse;min-width:680px}.scroll{overflow:auto}.th,th{color:var(--muted);font-size:12px;text-align:left;font-weight:600}th,td{padding:10px 8px;border-bottom:1px solid var(--line);vertical-align:top}.pill{display:inline-block;padding:3px 8px;border-radius:999px;background:#27314c;margin:2px 3px 2px 0;font-size:11px}.online{color:var(--ok)}.offline{color:var(--bad)}.warn{color:var(--warn)}.empty{color:var(--muted);padding:16px 0}.error{color:#ff9da8;white-space:pre-wrap;margin-top:10px}.success{color:var(--ok);white-space:pre-wrap;margin-top:10px}.hidden{display:none}
    .twocol{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(300px,.7fr);gap:14px}.best{background:#10182a;border:1px solid var(--line);border-radius:12px;padding:12px;min-height:80px;white-space:pre-wrap;font:12px/1.4 ui-monospace,SFMono-Regular,Consolas,monospace;color:#cdd6ed}
    @media(max-width:850px){.twocol{grid-template-columns:1fr}} @media(max-width:520px){.wrap{padding:18px 12px}h1{font-size:23px}.auth{min-width:100%}}
  </style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div><h1>Compute Swarm</h1><div class="sub">Distributed experiment controller</div></div>
    <div class="auth"><input id="token" type="password" autocomplete="off" placeholder="Admin token"/><button id="connect">Connect</button></div>
  </div>
  <div class="statusline"><span id="dot" class="dot"></span><span id="state">Enter the controller admin token.</span></div>
  <div class="grid">
    <div class="card"><div class="label">Workers online</div><div id="workersOnline" class="metric">—</div></div>
    <div class="card"><div class="label">Workers total</div><div id="workersTotal" class="metric">—</div></div>
    <div class="card"><div class="label">Pending joins</div><div id="pairingsPending" class="metric">—</div></div>
    <div class="card"><div class="label">Experiments</div><div id="experimentsTotal" class="metric">—</div></div>
    <div class="card"><div class="label">Jobs</div><div id="jobsTotal" class="metric">—</div></div>
    <div class="card"><div class="label">Artifacts</div><div id="artifacts" class="metric">—</div></div>
  </div>

  <div id="pairingCard" class="card section pairing hidden">
    <div class="sectionhead"><div><h2>Devices requesting access <span id="pairingBadge" class="badge">0</span></h2><div class="hint">Workers cannot execute swarm jobs until you explicitly approve them here. No shared enrollment token is sent to the worker.</div></div></div>
    <div class="scroll"><table><thead><tr><th>Device</th><th>Source</th><th>Platform</th><th>GPU</th><th>Requested</th><th>Decision</th></tr></thead><tbody id="pairings"></tbody></table></div>
  </div>

  <div class="card section">
    <div class="sectionhead"><div><h2>Experiments</h2><div class="hint">Parameter sweeps use adaptive pull scheduling: faster workers naturally consume more parameter points.</div></div><button id="toggleCreate">New experiment</button></div>
    <div id="createPanel" class="hidden">
      <div class="twocol">
        <div>
          <textarea id="experimentSpec" spellcheck="false">{
  "name": "Prime-count parameter sweep",
  "task": "prime_count",
  "parameters": {
    "start": {"start": 2, "stop": 800002, "step": 200000},
    "end": {"values": [1000000]}
  },
  "objective": {"path": "count", "direction": "maximize"},
  "requirements": {"capabilities": ["cpu"]}
}</textarea>
          <div style="display:flex;gap:8px;margin-top:10px"><button id="submitExperiment">Launch experiment</button><button id="cancelCreate" class="secondary">Cancel</button></div>
          <div id="createMessage"></div>
        </div>
        <div>
          <div class="label">Experiment JSON</div>
          <div class="hint">Use explicit <code>values</code> or inclusive numeric <code>start / stop / step</code> ranges. The Cartesian product becomes independently leased work units. The selected task must already exist on the workers.</div>
          <div class="hint"><b>Objective:</b> a dotted path into each task result, such as <code>metrics.efficiency</code>. Use <code>maximize</code> or <code>minimize</code>.</div>
          <div class="hint"><b>Replicates:</b> add <code>"replicates":10</code> and optionally <code>"replicate_parameter":"seed"</code>.</div>
        </div>
      </div>
    </div>
    <div class="scroll"><table><thead><tr><th>Name</th><th>Task</th><th>Generation</th><th>Progress</th><th>Queued / Leased</th><th>Failed</th><th>Objective</th><th>Actions</th></tr></thead><tbody id="experiments"></tbody></table></div>
    <div id="bestResult" class="best hidden"></div>
  </div>

  <div class="card section"><h2>Workers</h2><div class="scroll"><table><thead><tr><th>Status</th><th>Name</th><th>Platform</th><th>CPU / RAM</th><th>Battery / Temp</th><th>Capabilities</th><th>Last seen</th></tr></thead><tbody id="workers"></tbody></table></div></div>
  <div class="card section"><h2>All jobs</h2><div class="scroll"><table><thead><tr><th>Kind</th><th>Progress</th><th>Leased</th><th>Failed</th><th>Priority</th><th>Created</th><th>ID</th></tr></thead><tbody id="jobs"></tbody></table></div></div>
  <div id="error" class="error"></div>
</div>
<script>
const $=id=>document.getElementById(id); let timer=null; const promptedPairings=new Set();
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const cleanToken=()=>$('token').value.replace(/[^a-fA-F0-9]/g,'').trim();
const tokenHeaders=()=>({Authorization:'Bearer '+cleanToken()});
function bytes(n){if(!n)return '0 B';let u=['B','KB','MB','GB','TB'],i=0;while(n>=1024&&i<u.length-1){n/=1024;i++}return n.toFixed(i?1:0)+' '+u[i]}
function age(ts){if(!ts)return '—';let s=Math.max(0,Date.now()/1000-ts);if(s<60)return Math.round(s)+'s ago';if(s<3600)return Math.round(s/60)+'m ago';return Math.round(s/3600)+'h ago'}
function objectiveLabel(o){return o?`${esc(o.direction)} · ${esc(o.path)}`:'—'}
async function jsonFetch(path,options={}){const r=await fetch(path,options);let d=null;try{d=await r.json()}catch(_){ }if(!r.ok)throw new Error(d?.detail||`HTTP ${r.status}`);return d}
async function pairingDecision(id,approve){
 try{await jsonFetch(`/pairing/request/${encodeURIComponent(id)}/${approve?'approve':'deny'}`,{method:'POST',headers:tokenHeaders()});await refresh()}catch(e){$('error').textContent=e.message}
}
function renderPairings(items){
 $('pairingsPending').textContent=items.length;$('pairingBadge').textContent=items.length;$('pairingCard').classList.toggle('hidden',items.length===0);
 $('pairings').innerHTML=items.length?items.map(p=>`<tr><td><b>${esc(p.name)}</b><br><small>${esc(p.request_id).slice(0,12)}</small></td><td>${esc(p.remote_addr||'—')}</td><td>${esc(p.os_name||'unknown')} · ${esc(p.arch||'unknown')}<br><small>${esc(p.platform||'')}</small></td><td>${esc(p.gpu_name||'—')}</td><td>${age(p.requested_at)}<br><small>expires ${age(p.expires_at)}</small></td><td><button class="small" onclick="pairingDecision('${esc(p.request_id)}',true)">Approve</button> <button class="small danger" onclick="pairingDecision('${esc(p.request_id)}',false)">Deny</button></td></tr>`).join(''):'<tr><td colspan="6" class="empty">No devices waiting for approval.</td></tr>';
 for(const p of items){if(promptedPairings.has(p.request_id))continue;promptedPairings.add(p.request_id);const details=[p.name,p.remote_addr,p.os_name,p.arch,p.gpu_name].filter(Boolean).join('\n');if(confirm(`A device wants to join the Compute Swarm:\n\n${details}\n\nApprove this device?`)){pairingDecision(p.request_id,true)}}
}
async function refresh(){
 const token=cleanToken(); $('token').value=token; if(!token)return;
 try{
  const [d,e,p]=await Promise.all([
    jsonFetch('/status',{headers:tokenHeaders()}),
    jsonFetch('/experiments',{headers:tokenHeaders()}),
    jsonFetch('/pairing/pending',{headers:tokenHeaders()})
  ]);
  localStorage.setItem('swarmAdminToken',token); $('dot').className='dot ok'; $('state').textContent='Connected · auto-refreshing every 3 seconds'; $('error').textContent='';
  $('workersOnline').textContent=d.workers.filter(w=>w.online).length; $('workersTotal').textContent=d.workers.length; $('experimentsTotal').textContent=e.experiments.length; $('jobsTotal').textContent=d.jobs.length; $('artifacts').textContent=d.artifacts.count+' · '+bytes(d.artifacts.bytes);renderPairings(p.requests||[]);
  $('workers').innerHTML=d.workers.length?d.workers.map(w=>`<tr><td class="${w.online?'online':'offline'}">${w.online?'● Online':'● Offline'}</td><td><b>${esc(w.name)}</b><br><small>${esc(w.id).slice(0,12)}</small></td><td>${esc(w.os_name)}<br><small>${esc(w.arch)}</small></td><td>${w.cores} cores<br><small>${w.memory_mb?Math.round(w.memory_mb/1024*10)/10+' GB':'—'}</small></td><td>${w.battery_pct==null?'—':w.battery_pct+'%'}${w.charging?' ⚡':''}<br><small>${w.temperature_c==null?'—':w.temperature_c+' °C'}</small></td><td>${(w.capabilities||[]).slice(0,12).map(c=>`<span class="pill">${esc(c)}</span>`).join('')}</td><td>${age(w.last_seen)}</td></tr>`).join(''):'<tr><td colspan="7" class="empty">No workers enrolled yet.</td></tr>';
  $('experiments').innerHTML=e.experiments.length?e.experiments.map(x=>`<tr><td><b>${esc(x.name)}</b><br><small>${esc(x.experiment_id).slice(0,12)}</small></td><td>${esc(x.task)}</td><td>${x.generation||0}</td><td>${x.done||0} / ${x.units||0}</td><td>${x.queued||0} / ${x.leased||0}</td><td class="${x.failed?'warn':''}">${x.failed||0}</td><td>${objectiveLabel(x.objective)}</td><td><button class="small secondary" onclick="showExperiment('${esc(x.experiment_id)}')">Results</button> <button class="small secondary" onclick="refineExperiment('${esc(x.experiment_id)}')">Refine</button></td></tr>`).join(''):'<tr><td colspan="8" class="empty">No experiments yet. Launch one above.</td></tr>';
  $('jobs').innerHTML=d.jobs.length?d.jobs.map(j=>`<tr><td><b>${esc(j.kind)}</b></td><td>${j.done||0} / ${j.units||0}</td><td>${j.leased||0}</td><td>${j.failed||0}</td><td>${j.priority}</td><td>${new Date(j.created_at*1000).toLocaleString()}</td><td><small>${esc(j.id).slice(0,12)}</small></td></tr>`).join(''):'<tr><td colspan="7" class="empty">No jobs submitted yet.</td></tr>';
 }catch(e){$('dot').className='dot bad';$('state').textContent='Not connected';$('error').textContent=e.message;}
}
async function showExperiment(id){
 try{const d=await jsonFetch('/experiments/'+id+'?top=10',{headers:tokenHeaders()});$('bestResult').classList.remove('hidden');$('bestResult').textContent=d.best?`Best result for ${d.name}\nScore: ${d.best.score}\nParameters: ${JSON.stringify(d.best.parameters,null,2)}\nResult: ${JSON.stringify(d.best.result,null,2)}`:`${d.name}: no scored result yet (${d.done}/${d.units} done).`; }catch(e){$('error').textContent=e.message}
}
async function refineExperiment(id){
 if(!confirm('Create a finer child experiment around the current best results?'))return;
 try{const d=await jsonFetch('/experiments/'+id+'/refine',{method:'POST',headers:{...tokenHeaders(),'Content-Type':'application/json'},body:JSON.stringify({top_k:3,shrink:.25,points_per_axis:5})});$('bestResult').classList.remove('hidden');$('bestResult').textContent=`Created refinement generation ${d.generation}\nExperiment: ${d.experiment_id}\nParameter points: ${d.parameter_points}`;await refresh()}catch(e){$('error').textContent=e.message}
}
$('toggleCreate').onclick=()=>{$('createPanel').classList.toggle('hidden')};$('cancelCreate').onclick=()=>{$('createPanel').classList.add('hidden')};
$('submitExperiment').onclick=async()=>{const out=$('createMessage');out.className='';out.textContent='';try{const spec=JSON.parse($('experimentSpec').value);$('submitExperiment').disabled=true;const d=await jsonFetch('/experiments',{method:'POST',headers:{...tokenHeaders(),'Content-Type':'application/json'},body:JSON.stringify(spec)});out.className='success';out.textContent=`Launched ${d.units} work units · experiment ${d.experiment_id}`;await refresh()}catch(e){out.className='error';out.textContent=e.message}finally{$('submitExperiment').disabled=false}};
$('connect').onclick=()=>{refresh();if(timer)clearInterval(timer);timer=setInterval(refresh,3000)};
$('token').addEventListener('keydown',e=>{if(e.key==='Enter')$('connect').click()});
const saved=(localStorage.getItem('swarmAdminToken')||'').replace(/[^a-fA-F0-9]/g,'');if(saved){$('token').value=saved;localStorage.setItem('swarmAdminToken',saved);$('connect').click()}
</script>
</body></html>'''


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard():
    return HTMLResponse(DASHBOARD_HTML)


@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard_alias():
    return HTMLResponse(DASHBOARD_HTML)
