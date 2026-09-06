from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
import uuid
from typing import Any

from fastapi import Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app import ADMIN_TOKEN, LEASE_SECONDS, _json, _token_hash, admin_auth, app, db

PAIRING_TTL_SECONDS = int(os.getenv("SWARM_PAIRING_TTL_SECONDS", "900"))
PAIRING_SECRET = os.getenv("SWARM_PAIRING_SECRET", ADMIN_TOKEN)
MAX_PENDING_PER_ADDRESS = int(os.getenv("SWARM_PAIRING_MAX_PENDING_PER_ADDRESS", "8"))


class PairingRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    os_name: str | None = Field(default=None, max_length=100)
    platform: str | None = Field(default=None, max_length=300)
    arch: str | None = Field(default=None, max_length=100)
    gpu_name: str | None = Field(default=None, max_length=500)
    labels: dict[str, str] = Field(default_factory=dict)


def _init_pairing_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS pairing_requests (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                os_name TEXT,
                platform TEXT,
                arch TEXT,
                gpu_name TEXT,
                labels_json TEXT NOT NULL DEFAULT '{}',
                remote_addr TEXT,
                secret_hash TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                requested_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                decided_at REAL,
                worker_id TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_pairing_status_time
                ON pairing_requests(status, requested_at);
            """
        )


def _cleanup(conn, now: float) -> None:
    conn.execute(
        "UPDATE pairing_requests SET status='expired', decided_at=? "
        "WHERE status='pending' AND expires_at<?",
        (now, now),
    )
    conn.execute(
        "DELETE FROM pairing_requests WHERE status IN ('denied','expired') AND COALESCE(decided_at, requested_at)<?",
        (now - 86400,),
    )


def _derive_device_token(request_id: str, claim_secret: str) -> str:
    digest = hmac.new(
        PAIRING_SECRET.encode("utf-8"),
        f"{request_id}:{claim_secret}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _labels(row) -> dict[str, str]:
    if not row["labels_json"]:
        return {}
    try:
        value = __import__("json").loads(row["labels_json"])
        return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}
    except Exception:
        return {}


def _label_int(labels: dict[str, str], key: str) -> int | None:
    try:
        value = int(labels.get(key, ""))
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def _public_request(row) -> dict[str, Any]:
    return {
        "request_id": row["id"],
        "name": row["name"],
        "os_name": row["os_name"],
        "platform": row["platform"],
        "arch": row["arch"],
        "gpu_name": row["gpu_name"],
        "labels": _labels(row),
        "remote_addr": row["remote_addr"],
        "status": row["status"],
        "requested_at": row["requested_at"],
        "expires_at": row["expires_at"],
        "decided_at": row["decided_at"],
        "worker_id": row["worker_id"],
    }


_init_pairing_db()


@app.post("/pairing/request")
def request_pairing(req: PairingRequest, request: Request):
    now = time.time()
    remote_addr = request.client.host if request.client else "unknown"
    claim_secret = secrets.token_urlsafe(32)
    request_id = str(uuid.uuid4())

    with db() as conn:
        _cleanup(conn, now)
        pending = conn.execute(
            "SELECT COUNT(*) AS n FROM pairing_requests WHERE remote_addr=? AND status='pending' AND expires_at>=?",
            (remote_addr, now),
        ).fetchone()["n"]
        if pending >= MAX_PENDING_PER_ADDRESS:
            raise HTTPException(429, "too many pending pairing requests from this address")
        conn.execute(
            """
            INSERT INTO pairing_requests(
                id,name,os_name,platform,arch,gpu_name,labels_json,remote_addr,
                secret_hash,status,requested_at,expires_at
            ) VALUES(?,?,?,?,?,?,?,?,?,'pending',?,?)
            """,
            (
                request_id,
                req.name,
                req.os_name,
                req.platform,
                req.arch,
                req.gpu_name,
                _json(req.labels),
                remote_addr,
                _token_hash(claim_secret),
                now,
                now + PAIRING_TTL_SECONDS,
            ),
        )

    return {
        "request_id": request_id,
        "claim_secret": claim_secret,
        "status": "pending",
        "expires_at": now + PAIRING_TTL_SECONDS,
        "poll_after_seconds": 2,
    }


@app.get("/pairing/request/{request_id}")
def pairing_status(request_id: str, secret: str = Query(min_length=20, max_length=200)):
    now = time.time()
    with db() as conn:
        _cleanup(conn, now)
        row = conn.execute("SELECT * FROM pairing_requests WHERE id=?", (request_id,)).fetchone()
        if row is None or not secrets.compare_digest(row["secret_hash"], _token_hash(secret)):
            raise HTTPException(404, "pairing request not found")
        if row["status"] == "denied":
            return {"status": "denied"}
        if row["status"] == "expired" or row["expires_at"] < now:
            return {"status": "expired"}
        if row["status"] == "pending":
            return {"status": "pending", "expires_at": row["expires_at"]}

        device_token = _derive_device_token(request_id, secret)
        worker_id = row["worker_id"] or str(uuid.uuid4())
        existing = conn.execute("SELECT id FROM workers WHERE id=?", (worker_id,)).fetchone()
        if existing is None:
            labels = _labels(row)
            cores = min(_label_int(labels, "cpu_cores") or 1, 4096)
            memory_mb = _label_int(labels, "memory_mb")
            hardware_caps: list[str] = []
            gpu_vendor = labels.get("gpu_vendor", "").strip().lower()
            if gpu_vendor and gpu_vendor != "unknown":
                hardware_caps.append(f"gpu:{gpu_vendor}")
            conn.execute(
                """
                INSERT INTO workers(
                    id,name,os_name,platform,arch,cores,memory_mb,benchmark,
                    capabilities_json,labels_json,agent_version,device_token_hash,enrolled_at,last_seen
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    worker_id,
                    row["name"],
                    row["os_name"] or "unknown",
                    row["platform"] or "unknown",
                    row["arch"] or "unknown",
                    cores,
                    memory_mb,
                    1.0,
                    _json(hardware_caps),
                    row["labels_json"] or "{}",
                    None,
                    _token_hash(device_token),
                    now,
                    now,
                ),
            )
        conn.execute(
            "UPDATE pairing_requests SET status='claimed', worker_id=?, decided_at=COALESCE(decided_at, ?) WHERE id=?",
            (worker_id, now, request_id),
        )

    return {
        "status": "approved",
        "worker_id": worker_id,
        "device_token": device_token,
        "lease_seconds": LEASE_SECONDS,
    }


@app.get("/pairing/pending", dependencies=[Depends(admin_auth)])
def pending_pairings():
    now = time.time()
    with db() as conn:
        _cleanup(conn, now)
        rows = conn.execute(
            "SELECT * FROM pairing_requests WHERE status='pending' AND expires_at>=? ORDER BY requested_at ASC",
            (now,),
        ).fetchall()
    return {"requests": [_public_request(row) for row in rows]}


@app.post("/pairing/request/{request_id}/approve", dependencies=[Depends(admin_auth)])
def approve_pairing(request_id: str):
    now = time.time()
    with db() as conn:
        _cleanup(conn, now)
        row = conn.execute("SELECT status, expires_at FROM pairing_requests WHERE id=?", (request_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "pairing request not found")
        if row["status"] == "expired" or row["expires_at"] < now:
            raise HTTPException(409, "pairing request expired")
        if row["status"] == "denied":
            raise HTTPException(409, "pairing request was denied")
        conn.execute(
            "UPDATE pairing_requests SET status='approved', decided_at=? WHERE id=? AND status='pending'",
            (now, request_id),
        )
    return {"ok": True, "status": "approved"}


@app.post("/pairing/request/{request_id}/deny", dependencies=[Depends(admin_auth)])
def deny_pairing(request_id: str):
    now = time.time()
    with db() as conn:
        row = conn.execute("SELECT status FROM pairing_requests WHERE id=?", (request_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "pairing request not found")
        if row["status"] == "claimed":
            raise HTTPException(409, "worker has already claimed this pairing")
        conn.execute(
            "UPDATE pairing_requests SET status='denied', decided_at=? WHERE id=?",
            (now, request_id),
        )
    return {"ok": True, "status": "denied"}
