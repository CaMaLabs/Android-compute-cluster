from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

DB_PATH = Path(os.getenv("SWARM_DB", Path(__file__).with_name("swarm.db")))
ADMIN_TOKEN = os.getenv("SWARM_ADMIN_TOKEN", "dev-admin-token-change-me")
ENROLLMENT_TOKEN = os.getenv("SWARM_ENROLLMENT_TOKEN", "dev-enroll-token-change-me")
LEASE_SECONDS = int(os.getenv("SWARM_LEASE_SECONDS", "120"))
MAX_LONG_POLL_SECONDS = int(os.getenv("SWARM_MAX_LONG_POLL_SECONDS", "20"))

@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Universal Compute Swarm Controller", version="0.2.0", lifespan=lifespan)


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;

            CREATE TABLE IF NOT EXISTS workers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                os_name TEXT NOT NULL,
                platform TEXT NOT NULL,
                arch TEXT NOT NULL,
                cores INTEGER NOT NULL,
                memory_mb INTEGER,
                benchmark REAL NOT NULL DEFAULT 1,
                capabilities_json TEXT NOT NULL DEFAULT '[]',
                labels_json TEXT NOT NULL DEFAULT '{}',
                agent_version TEXT,
                temperature_c REAL,
                battery_pct REAL,
                charging INTEGER,
                enabled INTEGER NOT NULL DEFAULT 1,
                device_token_hash TEXT NOT NULL,
                enrolled_at REAL NOT NULL,
                last_seen REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                requirements_json TEXT NOT NULL DEFAULT '{}',
                priority INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS work_units (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                worker_id TEXT,
                lease_id TEXT,
                lease_until REAL,
                result_json TEXT,
                elapsed_ms REAL,
                error TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_work_units_sched
                ON work_units(status, lease_until, job_id, sequence);
            CREATE INDEX IF NOT EXISTS idx_workers_seen ON workers(last_seen);
            """
        )


def admin_auth(authorization: str | None = Header(default=None)) -> None:
    if authorization != f"Bearer {ADMIN_TOKEN}":
        raise HTTPException(status_code=401, detail="invalid admin token")


def enrollment_auth(authorization: str | None = Header(default=None)) -> None:
    if authorization != f"Bearer {ENROLLMENT_TOKEN}":
        raise HTTPException(status_code=401, detail="invalid enrollment token")


def verify_worker(worker_id: str, authorization: str | None) -> sqlite3.Row:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing worker token")
    supplied = authorization[7:]
    with db() as conn:
        row = conn.execute("SELECT * FROM workers WHERE id=?", (worker_id,)).fetchone()
    if row is None or not secrets.compare_digest(row["device_token_hash"], _token_hash(supplied)):
        raise HTTPException(status_code=401, detail="invalid worker credentials")
    if not row["enabled"]:
        raise HTTPException(status_code=403, detail="worker disabled")
    return row


class WorkerEnroll(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    requested_id: str | None = Field(default=None, max_length=200)


class WorkerRegistration(BaseModel):
    name: str
    os_name: str
    platform: str
    arch: str
    cores: int = Field(ge=1, le=4096)
    memory_mb: int | None = Field(default=None, ge=1)
    benchmark: float = Field(default=1.0, gt=0)
    capabilities: list[str] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)
    agent_version: str | None = None
    temperature_c: float | None = None
    battery_pct: float | None = Field(default=None, ge=0, le=100)
    charging: bool | None = None


class Heartbeat(BaseModel):
    benchmark: float | None = Field(default=None, gt=0)
    temperature_c: float | None = None
    battery_pct: float | None = Field(default=None, ge=0, le=100)
    charging: bool | None = None
    capabilities: list[str] | None = None


class Requirements(BaseModel):
    capabilities: list[str] = Field(default_factory=list)
    os: list[str] = Field(default_factory=list)
    arch: list[str] = Field(default_factory=list)
    min_cores: int | None = Field(default=None, ge=1)
    min_memory_mb: int | None = Field(default=None, ge=1)
    labels: dict[str, str] = Field(default_factory=dict)


class JobRequest(BaseModel):
    kind: str = Field(min_length=1, max_length=200)
    units: list[dict[str, Any]] = Field(min_length=1, max_length=10000)
    requirements: Requirements = Field(default_factory=Requirements)
    priority: int = Field(default=0, ge=-1000, le=1000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RangeJobRequest(BaseModel):
    kind: str = Field(min_length=1, max_length=200)
    start: int
    end: int
    chunk_size: int = Field(gt=0, le=1_000_000_000)
    payload: dict[str, Any] = Field(default_factory=dict)
    requirements: Requirements = Field(default_factory=Requirements)
    priority: int = Field(default=0, ge=-1000, le=1000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResultRequest(BaseModel):
    lease_id: str
    result: Any
    elapsed_ms: float = Field(ge=0)


class FailureRequest(BaseModel):
    lease_id: str
    error: str = Field(min_length=1, max_length=4000)
    retry: bool = True


def _worker_matches(worker: sqlite3.Row, requirements_json: str, kind: str) -> bool:
    req = _loads(requirements_json, {})
    caps = set(_loads(worker["capabilities_json"], []))
    required_caps = set(req.get("capabilities", [])) | {f"task:{kind}"}
    if not required_caps.issubset(caps):
        return False
    allowed_os = {str(x).lower() for x in req.get("os", [])}
    if allowed_os and str(worker["os_name"]).lower() not in allowed_os:
        return False
    allowed_arch = {str(x).lower() for x in req.get("arch", [])}
    if allowed_arch and str(worker["arch"]).lower() not in allowed_arch:
        return False
    if req.get("min_cores") and worker["cores"] < int(req["min_cores"]):
        return False
    if req.get("min_memory_mb") and (worker["memory_mb"] or 0) < int(req["min_memory_mb"]):
        return False
    worker_labels = _loads(worker["labels_json"], {})
    for key, value in req.get("labels", {}).items():
        if worker_labels.get(key) != value:
            return False
    return True


def _recover_expired(conn: sqlite3.Connection, now: float) -> None:
    conn.execute(
        """
        UPDATE work_units
        SET status='queued', worker_id=NULL, lease_id=NULL, lease_until=NULL
        WHERE status='leased' AND lease_until < ?
        """,
        (now,),
    )


def _try_lease(worker_id: str) -> dict[str, Any] | None:
    now = time.time()
    with db() as conn:
        worker = conn.execute("SELECT * FROM workers WHERE id=?", (worker_id,)).fetchone()
        if worker is None:
            raise HTTPException(404, "worker not registered")
        conn.execute("BEGIN IMMEDIATE")
        _recover_expired(conn, now)
        candidates = conn.execute(
            """
            SELECT u.id AS unit_id, u.job_id, u.sequence, u.payload_json,
                   j.kind, j.requirements_json, j.priority, j.created_at
            FROM work_units u JOIN jobs j ON j.id=u.job_id
            WHERE u.status='queued'
            ORDER BY j.priority DESC, j.created_at ASC, u.sequence ASC
            LIMIT 200
            """
        ).fetchall()
        row = next((r for r in candidates if _worker_matches(worker, r["requirements_json"], r["kind"])), None)
        if row is None:
            return None
        lease_id = str(uuid.uuid4())
        lease_until = now + LEASE_SECONDS
        updated = conn.execute(
            """
            UPDATE work_units
            SET status='leased', worker_id=?, lease_id=?, lease_until=?, attempts=attempts+1
            WHERE id=? AND status='queued'
            """,
            (worker_id, lease_id, lease_until, row["unit_id"]),
        )
        if updated.rowcount != 1:
            return None
        return {
            "lease_id": lease_id,
            "lease_until": lease_until,
            "lease_seconds": LEASE_SECONDS,
            "job_id": row["job_id"],
            "unit_id": row["unit_id"],
            "sequence": row["sequence"],
            "kind": row["kind"],
            "payload": _loads(row["payload_json"], {}),
        }


@app.get("/health")
def health():
    return {"ok": True, "time": time.time(), "version": app.version}


@app.post("/workers/enroll", dependencies=[Depends(enrollment_auth)])
def enroll_worker(req: WorkerEnroll):
    now = time.time()
    wid = req.requested_id or str(uuid.uuid4())
    device_token = secrets.token_urlsafe(36)
    with db() as conn:
        exists = conn.execute("SELECT id FROM workers WHERE id=?", (wid,)).fetchone()
        if exists is not None:
            raise HTTPException(409, "requested worker id already exists")
        conn.execute(
            """
            INSERT INTO workers(
                id,name,os_name,platform,arch,cores,memory_mb,benchmark,
                capabilities_json,labels_json,agent_version,device_token_hash,enrolled_at,last_seen
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                wid, req.name, "unknown", "unknown", "unknown", 1, None, 1.0,
                "[]", "{}", None, _token_hash(device_token), now, now,
            ),
        )
    return {"worker_id": wid, "device_token": device_token, "lease_seconds": LEASE_SECONDS}


@app.post("/workers/{worker_id}/register")
def register_worker(
    worker_id: str,
    req: WorkerRegistration,
    authorization: str | None = Header(default=None),
):
    verify_worker(worker_id, authorization)
    now = time.time()
    capabilities = sorted(set(req.capabilities))
    with db() as conn:
        conn.execute(
            """
            UPDATE workers SET
                name=?,os_name=?,platform=?,arch=?,cores=?,memory_mb=?,benchmark=?,
                capabilities_json=?,labels_json=?,agent_version=?,temperature_c=?,battery_pct=?,charging=?,last_seen=?
            WHERE id=?
            """,
            (
                req.name, req.os_name, req.platform, req.arch, req.cores, req.memory_mb,
                req.benchmark, _json(capabilities), _json(req.labels), req.agent_version,
                req.temperature_c, req.battery_pct,
                None if req.charging is None else int(req.charging), now, worker_id,
            ),
        )
    return {"ok": True, "lease_seconds": LEASE_SECONDS}


@app.post("/workers/{worker_id}/heartbeat")
def heartbeat(
    worker_id: str,
    req: Heartbeat,
    authorization: str | None = Header(default=None),
):
    verify_worker(worker_id, authorization)
    with db() as conn:
        conn.execute(
            """
            UPDATE workers SET
                benchmark=COALESCE(?,benchmark), temperature_c=?, battery_pct=?, charging=?,
                capabilities_json=COALESCE(?,capabilities_json), last_seen=?
            WHERE id=?
            """,
            (
                req.benchmark, req.temperature_c, req.battery_pct,
                None if req.charging is None else int(req.charging),
                None if req.capabilities is None else _json(sorted(set(req.capabilities))),
                time.time(), worker_id,
            ),
        )
    return {"ok": True}


@app.post("/jobs", dependencies=[Depends(admin_auth)])
def create_job(req: JobRequest):
    job_id = str(uuid.uuid4())
    now = time.time()
    units = [(str(uuid.uuid4()), job_id, i, _json(payload)) for i, payload in enumerate(req.units)]
    with db() as conn:
        conn.execute(
            "INSERT INTO jobs(id,kind,metadata_json,requirements_json,priority,created_at) VALUES(?,?,?,?,?,?)",
            (job_id, req.kind, _json(req.metadata), _json(req.requirements.model_dump()), req.priority, now),
        )
        conn.executemany(
            "INSERT INTO work_units(id,job_id,sequence,payload_json) VALUES(?,?,?,?)",
            units,
        )
    return {"job_id": job_id, "units": len(units)}


@app.post("/jobs/range", dependencies=[Depends(admin_auth)])
def create_range_job(req: RangeJobRequest):
    if req.end <= req.start:
        raise HTTPException(400, "end must be greater than start")
    units: list[dict[str, Any]] = []
    cursor = req.start
    while cursor < req.end:
        end = min(cursor + req.chunk_size, req.end)
        payload = dict(req.payload)
        payload.update({"start": cursor, "end": end})
        units.append(payload)
        if len(units) > 10000:
            raise HTTPException(400, "range produces more than 10000 work units")
        cursor = end
    return create_job(
        JobRequest(
            kind=req.kind,
            units=units,
            requirements=req.requirements,
            priority=req.priority,
            metadata=req.metadata,
        )
    )


@app.post("/workers/{worker_id}/lease")
def lease_work(
    worker_id: str,
    wait_seconds: int = Query(default=0, ge=0, le=MAX_LONG_POLL_SECONDS),
    authorization: str | None = Header(default=None),
):
    verify_worker(worker_id, authorization)
    deadline = time.monotonic() + wait_seconds
    while True:
        work = _try_lease(worker_id)
        if work is not None:
            return {"work": work}
        if time.monotonic() >= deadline:
            return {"work": None}
        time.sleep(0.5)


@app.post("/workers/{worker_id}/leases/{lease_id}/renew")
def renew_lease(
    worker_id: str,
    lease_id: str,
    authorization: str | None = Header(default=None),
):
    verify_worker(worker_id, authorization)
    lease_until = time.time() + LEASE_SECONDS
    with db() as conn:
        updated = conn.execute(
            """
            UPDATE work_units SET lease_until=?
            WHERE worker_id=? AND lease_id=? AND status='leased'
            """,
            (lease_until, worker_id, lease_id),
        )
        if updated.rowcount != 1:
            raise HTTPException(409, "active lease not found")
    return {"ok": True, "lease_until": lease_until}


@app.post("/workers/{worker_id}/units/{unit_id}/result")
def submit_result(
    worker_id: str,
    unit_id: str,
    req: ResultRequest,
    authorization: str | None = Header(default=None),
):
    verify_worker(worker_id, authorization)
    with db() as conn:
        row = conn.execute("SELECT * FROM work_units WHERE id=?", (unit_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "work unit not found")
        if row["status"] != "leased" or row["worker_id"] != worker_id or row["lease_id"] != req.lease_id:
            raise HTTPException(409, "lease mismatch")
        conn.execute(
            """
            UPDATE work_units
            SET status='done',result_json=?,elapsed_ms=?,lease_until=NULL,error=NULL
            WHERE id=?
            """,
            (_json(req.result), req.elapsed_ms, unit_id),
        )
    return {"ok": True}


@app.post("/workers/{worker_id}/units/{unit_id}/failure")
def submit_failure(
    worker_id: str,
    unit_id: str,
    req: FailureRequest,
    authorization: str | None = Header(default=None),
):
    verify_worker(worker_id, authorization)
    with db() as conn:
        row = conn.execute("SELECT * FROM work_units WHERE id=?", (unit_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "work unit not found")
        if row["status"] != "leased" or row["worker_id"] != worker_id or row["lease_id"] != req.lease_id:
            raise HTTPException(409, "lease mismatch")
        if req.retry:
            conn.execute(
                """
                UPDATE work_units SET status='queued',worker_id=NULL,lease_id=NULL,lease_until=NULL,error=?
                WHERE id=?
                """,
                (req.error, unit_id),
            )
        else:
            conn.execute(
                """
                UPDATE work_units SET status='failed',lease_until=NULL,error=?
                WHERE id=?
                """,
                (req.error, unit_id),
            )
    return {"ok": True}


@app.get("/jobs/{job_id}", dependencies=[Depends(admin_auth)])
def get_job(job_id: str):
    with db() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if job is None:
            raise HTTPException(404, "job not found")
        units = [dict(r) for r in conn.execute(
            "SELECT id,sequence,status,worker_id,elapsed_ms,error,attempts,result_json FROM work_units WHERE job_id=? ORDER BY sequence",
            (job_id,),
        )]
    for unit in units:
        unit["result"] = _loads(unit.pop("result_json"), None)
    result = dict(job)
    result["metadata"] = _loads(result.pop("metadata_json"), {})
    result["requirements"] = _loads(result.pop("requirements_json"), {})
    result["units"] = units
    return result


@app.get("/status", dependencies=[Depends(admin_auth)])
def status():
    now = time.time()
    with db() as conn:
        workers = [dict(r) for r in conn.execute(
            "SELECT id,name,os_name,platform,arch,cores,memory_mb,benchmark,capabilities_json,labels_json,agent_version,temperature_c,battery_pct,charging,enabled,last_seen FROM workers ORDER BY name"
        )]
        jobs = [dict(r) for r in conn.execute(
            """
            SELECT j.id,j.kind,j.priority,j.created_at,
                   COUNT(u.id) units,
                   SUM(CASE WHEN u.status='done' THEN 1 ELSE 0 END) done,
                   SUM(CASE WHEN u.status='leased' THEN 1 ELSE 0 END) leased,
                   SUM(CASE WHEN u.status='failed' THEN 1 ELSE 0 END) failed
            FROM jobs j LEFT JOIN work_units u ON u.job_id=j.id
            GROUP BY j.id ORDER BY j.created_at DESC
            """
        )]
    for worker in workers:
        worker["capabilities"] = _loads(worker.pop("capabilities_json"), [])
        worker["labels"] = _loads(worker.pop("labels_json"), {})
        worker["online"] = now - worker["last_seen"] < max(30, LEASE_SECONDS)
        if worker["charging"] is not None:
            worker["charging"] = bool(worker["charging"])
    return {"workers": workers, "jobs": jobs, "lease_seconds": LEASE_SECONDS}
