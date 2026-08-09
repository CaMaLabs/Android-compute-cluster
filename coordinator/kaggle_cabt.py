from __future__ import annotations

import csv
import io
import time
from typing import Any

from fastapi import Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from app import JobRequest, Requirements, _loads, admin_auth, app, create_job, db

MATCH_META_KEY = "_kaggle_cabt_match"
KAGGLE_SUBMISSION_MAX_BYTES = int(197.7 * 1024 * 1024)


class CabtMatchRequest(BaseModel):
    name: str = Field(default="Pokémon TCG CABT evaluation", min_length=1, max_length=200)
    agent_artifact_id: str = Field(min_length=1, max_length=200)
    opponent_artifact_id: str | None = Field(default=None, max_length=200)
    episodes: int = Field(default=100, ge=1, le=10_000)
    alternate_seats: bool = True
    run_timeout_seconds: int = Field(default=1200, ge=30, le=2000)
    priority: int = Field(default=0, ge=-1000, le=1000)
    save_replays: bool = False


def _artifact_meta(artifact_id: str) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"artifact not found: {artifact_id}")
    name = str(row["name"])
    if not (name.lower().endswith(".tar.gz") or name.lower().endswith(".tgz")):
        raise HTTPException(400, f"{name}: CABT agents must be Kaggle .tar.gz submission bundles")
    if int(row["size_bytes"]) > KAGGLE_SUBMISSION_MAX_BYTES:
        raise HTTPException(
            400,
            f"{name}: bundle is larger than the Kaggle 197.7 MiB submission limit",
        )
    return {
        "artifact_id": row["id"],
        "sha256": row["sha256"],
        "name": name,
        "size_bytes": row["size_bytes"],
    }


def _match_job(match_id: str):
    with db() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (match_id,)).fetchone()
        if job is None:
            raise HTTPException(404, "CABT match not found")
        metadata = _loads(job["metadata_json"], {})
        match = metadata.get(MATCH_META_KEY)
        if not isinstance(match, dict):
            raise HTTPException(404, "job is not a CABT match")
        units = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id,sequence,status,worker_id,elapsed_ms,error,attempts,result_json
                FROM work_units WHERE job_id=? ORDER BY sequence
                """,
                (match_id,),
            )
        ]
    return job, match, units


def _summary(job: Any, match: dict[str, Any], units: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"queued": 0, "leased": 0, "done": 0, "failed": 0}
    wins = losses = draws = runtime_errors = 0
    elapsed_ms = 0.0
    for unit in units:
        status = str(unit["status"])
        counts[status] = counts.get(status, 0) + 1
        elapsed_ms += float(unit.get("elapsed_ms") or 0.0)
        if status != "done":
            continue
        result = _loads(unit.get("result_json"), {})
        outcome = result.get("outcome") if isinstance(result, dict) else None
        if outcome == "agent_win":
            wins += 1
        elif outcome == "opponent_win":
            losses += 1
        elif outcome == "draw":
            draws += 1
        else:
            runtime_errors += 1
    decided = wins + losses + draws
    return {
        "match_id": job["id"],
        "name": match.get("name"),
        "created_at": job["created_at"],
        "episodes": len(units),
        **counts,
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "runtime_errors": runtime_errors,
        "score_rate": ((wins + 0.5 * draws) / decided) if decided else None,
        "win_rate_decisive": (wins / (wins + losses)) if (wins + losses) else None,
        "elapsed_ms": elapsed_ms,
        "self_play": match.get("self_play", False),
        "agent": match.get("agent"),
        "opponent": match.get("opponent"),
        "alternate_seats": match.get("alternate_seats", True),
    }


@app.post("/kaggle/cabt/matches", dependencies=[Depends(admin_auth)])
def create_cabt_match(req: CabtMatchRequest):
    agent = _artifact_meta(req.agent_artifact_id)
    opponent = _artifact_meta(req.opponent_artifact_id or req.agent_artifact_id)
    self_play = agent["artifact_id"] == opponent["artifact_id"]

    units: list[dict[str, Any]] = []
    for index in range(req.episodes):
        swap = bool(req.alternate_seats and index % 2)
        units.append(
            {
                "match_index": index,
                "seat_swap": swap,
                "run_timeout_seconds": req.run_timeout_seconds,
                "save_replay": req.save_replays,
                "artifact_inputs": [
                    {
                        "artifact_id": agent["artifact_id"],
                        "alias": "agent",
                        "name": "agent.tar.gz",
                    },
                    {
                        "artifact_id": opponent["artifact_id"],
                        "alias": "opponent",
                        "name": "opponent.tar.gz",
                    },
                ],
            }
        )

    match_meta = {
        "name": req.name,
        "environment": "cabt",
        "competition": "pokemon-tcg-ai-battle",
        "episodes": req.episodes,
        "alternate_seats": req.alternate_seats,
        "run_timeout_seconds": req.run_timeout_seconds,
        "self_play": self_play,
        "agent": agent,
        "opponent": opponent,
        "created_at": time.time(),
    }
    created = create_job(
        JobRequest(
            kind="kaggle_cabt_episode",
            units=units,
            requirements=Requirements(capabilities=["python", "kaggle:cabt"]),
            priority=req.priority,
            metadata={MATCH_META_KEY: match_meta},
        )
    )
    return {
        "match_id": created["job_id"],
        "units": created["units"],
        "self_play": self_play,
        "scheduler": "adaptive_pull",
        "required_capability": "task:kaggle_cabt_episode",
    }


@app.get("/kaggle/cabt/matches", dependencies=[Depends(admin_auth)])
def list_cabt_matches():
    with db() as conn:
        jobs = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
        matches: list[dict[str, Any]] = []
        for job in jobs:
            metadata = _loads(job["metadata_json"], {})
            match = metadata.get(MATCH_META_KEY)
            if not isinstance(match, dict):
                continue
            units = [
                dict(row)
                for row in conn.execute(
                    "SELECT status,elapsed_ms,result_json FROM work_units WHERE job_id=?",
                    (job["id"],),
                )
            ]
            matches.append(_summary(job, match, units))
    return {"matches": matches}


@app.get("/kaggle/cabt/matches/{match_id}", dependencies=[Depends(admin_auth)])
def get_cabt_match(match_id: str):
    job, match, units = _match_job(match_id)
    detail = _summary(job, match, units)
    episodes = []
    worker_stats: dict[str, dict[str, Any]] = {}
    for unit in units:
        result = _loads(unit.pop("result_json"), None)
        unit["result"] = result
        episodes.append(unit)
        wid = unit.get("worker_id")
        if unit.get("status") == "done" and wid:
            stat = worker_stats.setdefault(
                wid,
                {"worker_id": wid, "episodes": 0, "elapsed_ms": 0.0},
            )
            stat["episodes"] += 1
            stat["elapsed_ms"] += float(unit.get("elapsed_ms") or 0.0)
    for stat in worker_stats.values():
        stat["avg_elapsed_ms"] = (
            stat["elapsed_ms"] / stat["episodes"] if stat["episodes"] else None
        )
    return {
        **detail,
        "spec": match,
        "worker_throughput": sorted(
            worker_stats.values(), key=lambda x: x["episodes"], reverse=True
        ),
        "episode_results": episodes,
    }


@app.get("/kaggle/cabt/matches/{match_id}/csv", dependencies=[Depends(admin_auth)])
def cabt_match_csv(match_id: str):
    _, match, units = _match_job(match_id)
    out = io.StringIO()
    writer = csv.DictWriter(
        out,
        fieldnames=[
            "episode",
            "status",
            "worker_id",
            "elapsed_ms",
            "seat_swap",
            "outcome",
            "agent_reward",
            "opponent_reward",
            "agent_status",
            "opponent_status",
            "steps",
            "engine_version",
            "error",
        ],
    )
    writer.writeheader()
    for unit in units:
        result = _loads(unit.get("result_json"), {})
        if not isinstance(result, dict):
            result = {}
        writer.writerow(
            {
                "episode": unit["sequence"],
                "status": unit["status"],
                "worker_id": unit.get("worker_id"),
                "elapsed_ms": unit.get("elapsed_ms"),
                "seat_swap": result.get("seat_swap"),
                "outcome": result.get("outcome"),
                "agent_reward": result.get("agent_reward"),
                "opponent_reward": result.get("opponent_reward"),
                "agent_status": result.get("agent_status"),
                "opponent_status": result.get("opponent_status"),
                "steps": result.get("steps"),
                "engine_version": result.get("engine_version"),
                "error": unit.get("error") or result.get("error"),
            }
        )
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(match.get("name") or "cabt"))
    return Response(
        out.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe}-results.csv"'},
    )


POKEMON_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pokémon TCG Swarm Runner</title>
<style>
:root{color-scheme:dark;--bg:#0b1020;--panel:#141b2d;--line:#28334d;--text:#eef2ff;--muted:#9aa6bd;--accent:#8b7cff;--ok:#56d39b;--bad:#ff6b7a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.45 system-ui,sans-serif}.wrap{max-width:1050px;margin:auto;padding:26px 16px}
a{color:#b9b0ff}.card{background:var(--panel);border:1px solid var(--line);border-radius:15px;padding:16px;margin-top:14px}h1,h2{margin:0 0 8px}.hint{color:var(--muted)}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}.field{margin:10px 0}label{display:block;color:var(--muted);font-size:12px;margin-bottom:5px}
input{width:100%;background:#0d1425;border:1px solid var(--line);border-radius:10px;color:var(--text);padding:10px}input[type=checkbox]{width:auto}
button{border:0;border-radius:10px;padding:10px 14px;background:var(--accent);color:white;font-weight:700;cursor:pointer}button.secondary{background:#27314c}button:disabled{opacity:.5}
.status{white-space:pre-wrap;margin-top:10px}.ok{color:var(--ok)}.bad{color:var(--bad)}table{width:100%;border-collapse:collapse;min-width:760px}.scroll{overflow:auto}th,td{padding:9px 7px;border-bottom:1px solid var(--line);text-align:left}th{color:var(--muted);font-size:12px}
@media(max-width:720px){.row{grid-template-columns:1fr}}
</style>
</head>
<body><div class="wrap">
<div><a href="/">← Compute Swarm</a><h1>Pokémon TCG · CABT Swarm Runner</h1><div class="hint">Upload a Kaggle submission bundle and fan independent CABT episodes across compatible workers.</div></div>
<div class="card">
  <div class="row">
    <div class="field"><label>Admin token</label><input id="token" type="password" autocomplete="off"></div>
    <div class="field"><label>Match name</label><input id="name" value="Pokémon CABT evaluation"></div>
  </div>
  <div class="row">
    <div class="field"><label>Your submission.tar.gz</label><input id="agent" type="file" accept=".gz,.tgz,application/gzip"></div>
    <div class="field"><label>Opponent submission.tar.gz (optional — blank = self-play)</label><input id="opponent" type="file" accept=".gz,.tgz,application/gzip"></div>
  </div>
  <div class="row">
    <div class="field"><label>Episodes</label><input id="episodes" type="number" min="1" max="10000" value="100"></div>
    <div class="field"><label>Per-episode timeout (seconds)</label><input id="timeout" type="number" min="30" max="2000" value="1200"></div>
  </div>
  <div class="field"><label><input id="alternate" type="checkbox" checked> Alternate seats every episode</label></div>
  <button id="launch">Upload & run</button>
  <button id="refresh" class="secondary">Refresh</button>
  <div id="workers" class="hint"></div><div id="message" class="status"></div>
</div>
<div class="card"><h2>CABT runs</h2><div class="scroll"><table><thead><tr><th>Name</th><th>Progress</th><th>W/L/D</th><th>Score</th><th>Failed</th><th>Actions</th></tr></thead><tbody id="runs"></tbody></table></div></div>
<div class="card"><h2>Selected run</h2><div id="detail" class="status hint">Select Results on a run.</div></div>
</div>
<script>
const $=id=>document.getElementById(id);
const token=()=>$('token').value.trim();
const headers=()=>({Authorization:'Bearer '+token()});
async function jfetch(path,opts={}){const r=await fetch(path,opts);let d=null;try{d=await r.json()}catch(_){ }if(!r.ok)throw new Error(d?.detail||`HTTP ${r.status}`);return d}
async function upload(file){const r=await fetch('/artifacts?name='+encodeURIComponent(file.name),{method:'POST',headers:{...headers(),'Content-Type':file.type||'application/gzip'},body:file});let d=null;try{d=await r.json()}catch(_){ }if(!r.ok)throw new Error(d?.detail||`Upload HTTP ${r.status}`);return d}
function pct(x){return x==null?'—':(x*100).toFixed(1)+'%'}
async function refresh(){
 if(!token())return;
 try{
  const [m,s]=await Promise.all([jfetch('/kaggle/cabt/matches',{headers:headers()}),jfetch('/status',{headers:headers()})]);
  localStorage.setItem('swarmAdminToken',token());
  const eligible=s.workers.filter(w=>w.online&&(w.capabilities||[]).includes('task:kaggle_cabt_episode'));
  $('workers').textContent=`CABT-capable workers online: ${eligible.length}. ${eligible.length?'Ready to run.':'Enable CABT on at least one Python worker before launching.'}`;
  $('runs').innerHTML=m.matches.length?m.matches.map(x=>`<tr><td><b>${x.name}</b><br><small>${x.match_id.slice(0,12)}</small></td><td>${x.done}/${x.episodes} · ${x.leased} leased</td><td>${x.wins}/${x.losses}/${x.draws}</td><td>${pct(x.score_rate)}</td><td>${x.failed}</td><td><button onclick="showRun('${x.match_id}')">Results</button> <button class="secondary" onclick="downloadCsv('${x.match_id}')">CSV</button></td></tr>`).join(''):'<tr><td colspan="6">No CABT runs yet.</td></tr>';
  $('message').textContent='';
 }catch(e){$('message').className='status bad';$('message').textContent=e.message}
}
async function showRun(id){
 try{const d=await jfetch('/kaggle/cabt/matches/'+id,{headers:headers()});$('detail').textContent=`${d.name}\n${d.done}/${d.episodes} complete · ${d.wins}W/${d.losses}L/${d.draws}D · score ${pct(d.score_rate)}\n\nWorkers:\n${JSON.stringify(d.worker_throughput,null,2)}\n\nRecent episodes:\n${JSON.stringify(d.episode_results.slice(-10),null,2)}`;}catch(e){$('detail').textContent=e.message}
}
async function downloadCsv(id){
 const r=await fetch('/kaggle/cabt/matches/'+id+'/csv',{headers:headers()});if(!r.ok){$('message').textContent='CSV download failed: HTTP '+r.status;return}const blob=await r.blob();const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='cabt-results.csv';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)
}
$('launch').onclick=async()=>{
 const a=$('agent').files[0];if(!a){$('message').className='status bad';$('message').textContent='Choose your submission.tar.gz first.';return}
 $('launch').disabled=true;$('message').className='status';$('message').textContent='Uploading agent bundle…';
 try{
  const aa=await upload(a);const o=$('opponent').files[0];let oo=null;if(o){$('message').textContent='Uploading opponent bundle…';oo=await upload(o)}
  $('message').textContent='Creating distributed CABT run…';
  const d=await jfetch('/kaggle/cabt/matches',{method:'POST',headers:{...headers(),'Content-Type':'application/json'},body:JSON.stringify({name:$('name').value,agent_artifact_id:aa.artifact_id,opponent_artifact_id:oo?.artifact_id||null,episodes:Number($('episodes').value),alternate_seats:$('alternate').checked,run_timeout_seconds:Number($('timeout').value)})});
  $('message').className='status ok';$('message').textContent=`Launched ${d.units} CABT episodes · ${d.match_id}`;await refresh()
 }catch(e){$('message').className='status bad';$('message').textContent=e.message}finally{$('launch').disabled=false}
};
$('refresh').onclick=refresh;
const saved=localStorage.getItem('swarmAdminToken');if(saved){$('token').value=saved;refresh()}
setInterval(refresh,4000);
</script></body></html>"""


@app.get("/pokemon", response_class=HTMLResponse, include_in_schema=False)
def pokemon_dashboard():
    return HTMLResponse(POKEMON_HTML)
