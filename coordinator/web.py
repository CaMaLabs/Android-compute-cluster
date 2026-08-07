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
    :root{color-scheme:dark;--bg:#0b1020;--panel:#141b2d;--panel2:#1a2338;--text:#eef2ff;--muted:#9aa6bd;--accent:#8b7cff;--ok:#56d39b;--bad:#ff6b7a;--line:#28334d}
    *{box-sizing:border-box} body{margin:0;background:radial-gradient(circle at top,#18213b 0,#0b1020 36rem);color:var(--text);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
    .wrap{max-width:1180px;margin:auto;padding:28px 18px 48px}.top{display:flex;gap:16px;align-items:center;justify-content:space-between;flex-wrap:wrap;margin-bottom:20px}
    h1{font-size:28px;margin:0}.sub{color:var(--muted);margin-top:4px}.auth{display:flex;gap:8px;align-items:center;min-width:min(100%,460px)}
    input{flex:1;background:#0d1425;border:1px solid var(--line);border-radius:11px;color:var(--text);padding:11px 12px;outline:none}button{border:0;border-radius:11px;padding:11px 16px;background:var(--accent);color:white;font-weight:700;cursor:pointer}
    .statusline{display:flex;gap:10px;align-items:center;color:var(--muted);margin:10px 0 18px}.dot{width:10px;height:10px;border-radius:50%;background:#71809b}.dot.ok{background:var(--ok)}.dot.bad{background:var(--bad)}
    .grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:18px}.card{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:16px;padding:16px;box-shadow:0 12px 28px #0003}
    .metric{font-size:29px;font-weight:800;margin-top:4px}.label{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.08em}
    .section{margin-top:16px}.section h2{font-size:18px;margin:0 0 10px}table{width:100%;border-collapse:collapse;min-width:680px}.scroll{overflow:auto}.th,th{color:var(--muted);font-size:12px;text-align:left;font-weight:600}th,td{padding:10px 8px;border-bottom:1px solid var(--line);vertical-align:top}.pill{display:inline-block;padding:3px 8px;border-radius:999px;background:#27314c;margin:2px 3px 2px 0;font-size:11px}.online{color:var(--ok)}.offline{color:var(--bad)}.empty{color:var(--muted);padding:16px 0}.error{color:#ff9da8;white-space:pre-wrap}
    @media(max-width:850px){.grid{grid-template-columns:repeat(2,1fr)}} @media(max-width:520px){.grid{grid-template-columns:1fr}.wrap{padding:18px 12px}h1{font-size:23px}}
  </style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div><h1>Compute Swarm</h1><div class="sub">Controller dashboard</div></div>
    <div class="auth"><input id="token" type="password" autocomplete="off" placeholder="Admin token"/><button id="connect">Connect</button></div>
  </div>
  <div class="statusline"><span id="dot" class="dot"></span><span id="state">Enter the controller admin token.</span></div>
  <div class="grid">
    <div class="card"><div class="label">Workers online</div><div id="workersOnline" class="metric">—</div></div>
    <div class="card"><div class="label">Workers total</div><div id="workersTotal" class="metric">—</div></div>
    <div class="card"><div class="label">Jobs</div><div id="jobsTotal" class="metric">—</div></div>
    <div class="card"><div class="label">Artifacts</div><div id="artifacts" class="metric">—</div></div>
  </div>
  <div class="card section"><h2>Workers</h2><div class="scroll"><table><thead><tr><th>Status</th><th>Name</th><th>Platform</th><th>CPU / RAM</th><th>Battery / Temp</th><th>Capabilities</th><th>Last seen</th></tr></thead><tbody id="workers"></tbody></table></div></div>
  <div class="card section"><h2>Jobs</h2><div class="scroll"><table><thead><tr><th>Kind</th><th>Progress</th><th>Leased</th><th>Failed</th><th>Priority</th><th>Created</th><th>ID</th></tr></thead><tbody id="jobs"></tbody></table></div></div>
  <div id="error" class="error"></div>
</div>
<script>
const $=id=>document.getElementById(id); let timer=null;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function bytes(n){if(!n)return '0 B';let u=['B','KB','MB','GB','TB'],i=0;while(n>=1024&&i<u.length-1){n/=1024;i++}return n.toFixed(i?1:0)+' '+u[i]}
function age(ts){if(!ts)return '—';let s=Math.max(0,Date.now()/1000-ts);if(s<60)return Math.round(s)+'s ago';if(s<3600)return Math.round(s/60)+'m ago';return Math.round(s/3600)+'h ago'}
async function refresh(){
 const token=$('token').value.trim(); if(!token)return;
 try{
  const r=await fetch('/status',{headers:{Authorization:'Bearer '+token}}); if(!r.ok)throw new Error(r.status===401?'Invalid admin token':'Controller returned HTTP '+r.status);
  const d=await r.json(); localStorage.setItem('swarmAdminToken',token); $('dot').className='dot ok'; $('state').textContent='Connected · auto-refreshing every 3 seconds'; $('error').textContent='';
  $('workersOnline').textContent=d.workers.filter(w=>w.online).length; $('workersTotal').textContent=d.workers.length; $('jobsTotal').textContent=d.jobs.length; $('artifacts').textContent=d.artifacts.count+' · '+bytes(d.artifacts.bytes);
  $('workers').innerHTML=d.workers.length?d.workers.map(w=>`<tr><td class="${w.online?'online':'offline'}">${w.online?'● Online':'● Offline'}</td><td><b>${esc(w.name)}</b><br><small>${esc(w.id).slice(0,12)}</small></td><td>${esc(w.os_name)}<br><small>${esc(w.arch)}</small></td><td>${w.cores} cores<br><small>${w.memory_mb?Math.round(w.memory_mb/1024*10)/10+' GB':'—'}</small></td><td>${w.battery_pct==null?'—':w.battery_pct+'%'}${w.charging?' ⚡':''}<br><small>${w.temperature_c==null?'—':w.temperature_c+' °C'}</small></td><td>${(w.capabilities||[]).slice(0,12).map(c=>`<span class="pill">${esc(c)}</span>`).join('')}</td><td>${age(w.last_seen)}</td></tr>`).join(''):'<tr><td colspan="7" class="empty">No workers enrolled yet.</td></tr>';
  $('jobs').innerHTML=d.jobs.length?d.jobs.map(j=>`<tr><td><b>${esc(j.kind)}</b></td><td>${j.done||0} / ${j.units||0}</td><td>${j.leased||0}</td><td>${j.failed||0}</td><td>${j.priority}</td><td>${new Date(j.created_at*1000).toLocaleString()}</td><td><small>${esc(j.id).slice(0,12)}</small></td></tr>`).join(''):'<tr><td colspan="7" class="empty">No jobs submitted yet.</td></tr>';
 }catch(e){$('dot').className='dot bad';$('state').textContent='Not connected';$('error').textContent=e.message;}
}
$('connect').onclick=()=>{refresh();if(timer)clearInterval(timer);timer=setInterval(refresh,3000)};
$('token').addEventListener('keydown',e=>{if(e.key==='Enter')$('connect').click()});
const saved=localStorage.getItem('swarmAdminToken');if(saved){$('token').value=saved;$('connect').click()}
</script>
</body></html>'''


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard():
    return HTMLResponse(DASHBOARD_HTML)


@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard_alias():
    return HTMLResponse(DASHBOARD_HTML)
