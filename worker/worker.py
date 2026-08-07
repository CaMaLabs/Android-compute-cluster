from __future__ import annotations

import hashlib
import importlib
import json
import math
import mimetypes
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import requests

from swarm_plugin import TASKS, task

AGENT_VERSION = "0.3.0"
CONTROLLER_URL = os.getenv("SWARM_CONTROLLER_URL", os.getenv("CLUSTER_URL", "http://127.0.0.1:8765")).rstrip("/")
ENROLLMENT_TOKEN = os.getenv("SWARM_ENROLLMENT_TOKEN", "dev-enroll-token-change-me")
IDENTITY_FILE = Path(os.getenv("SWARM_IDENTITY_FILE", str(Path.home() / ".compute-swarm-identity.json")))
WORK_ROOT = Path(os.getenv("SWARM_WORK_ROOT", str(Path(tempfile.gettempdir()) / "compute-swarm")))
POLL_SECONDS = float(os.getenv("SWARM_POLL_SECONDS", "1.5"))
MAX_TEMP_C = float(os.getenv("SWARM_MAX_TEMP_C", "46"))
RESUME_TEMP_C = float(os.getenv("SWARM_RESUME_TEMP_C", "42"))
MIN_BATTERY_PCT = float(os.getenv("SWARM_MIN_BATTERY_PCT", "20"))
ALLOW_INSECURE_REMOTE = os.getenv("SWARM_ALLOW_INSECURE_REMOTE", "0") == "1"
EXTRA_CAPABILITIES = [x.strip() for x in os.getenv("SWARM_CAPABILITIES", "").split(",") if x.strip()]
LABELS = dict(
    item.split("=", 1)
    for item in (x.strip() for x in os.getenv("SWARM_LABELS", "").split(","))
    if item and "=" in item
)


@task("prime_count")
def prime_count(payload: dict[str, Any]) -> dict[str, int]:
    start = int(payload["start"])
    end = int(payload["end"])

    def is_prime(n: int) -> bool:
        if n < 2:
            return False
        if n % 2 == 0:
            return n == 2
        limit = int(math.isqrt(n))
        d = 3
        while d <= limit:
            if n % d == 0:
                return False
            d += 2
        return True

    return {"count": sum(1 for n in range(max(2, start), end) if is_prime(n))}


@task("monte_carlo_pi")
def monte_carlo_pi(payload: dict[str, Any]) -> dict[str, int]:
    start = int(payload["start"])
    end = int(payload["end"])
    mask = (1 << 64) - 1

    def mix(value: int) -> int:
        x = value & mask
        x ^= x >> 12
        x ^= (x << 25) & mask
        x ^= x >> 27
        return (x * 0x2545F4914F6CDD1D) & mask

    inside = 0
    scale = float(mask)
    for i in range(start, end):
        a = mix((i + 0x9E3779B97F4A7C15) & mask)
        b = mix(a ^ 0xD1B54A32D192ED03)
        x = a / scale
        y = b / scale
        inside += int(x * x + y * y <= 1.0)
    return {"inside": inside, "samples": end - start}


@task("sha256_artifact")
def sha256_artifact(payload: dict[str, Any]) -> dict[str, Any]:
    alias = str(payload.get("alias", "input"))
    paths = payload.get("_artifact_paths", {})
    if alias not in paths:
        raise ValueError(f"artifact alias not found: {alias}")
    path = Path(paths[alias])
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
            size += len(chunk)
    return {"sha256": hasher.hexdigest(), "size_bytes": size}


@task("text_artifact")
def text_artifact(payload: dict[str, Any]) -> dict[str, Any]:
    work_dir = Path(payload["_work_dir"])
    name = Path(str(payload.get("name", "output.txt"))).name
    text = str(payload.get("text", ""))
    output = work_dir / name
    output.write_text(text, encoding="utf-8")
    return {
        "bytes": output.stat().st_size,
        "_artifact_outputs": [
            {"path": name, "name": name, "content_type": "text/plain; charset=utf-8"}
        ],
    }


def load_plugins() -> None:
    modules = [m.strip() for m in os.getenv("SWARM_PLUGIN_MODULES", "").split(",") if m.strip()]
    for module_name in modules:
        importlib.import_module(module_name)


def validate_controller_url() -> None:
    parsed = urlparse(CONTROLLER_URL)
    host = (parsed.hostname or "").lower()
    local_hosts = {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and (host in local_hosts or ALLOW_INSECURE_REMOTE):
        return
    raise RuntimeError(
        "Refusing plaintext remote controller. Use https://, or set "
        "SWARM_ALLOW_INSECURE_REMOTE=1 only for a trusted LAN/hotspot."
    )


def read_identity() -> dict[str, str] | None:
    try:
        data = json.loads(IDENTITY_FILE.read_text())
        if data.get("worker_id") and data.get("device_token"):
            return {"worker_id": data["worker_id"], "device_token": data["device_token"]}
    except Exception:
        pass
    return None


def save_identity(identity: dict[str, str]) -> None:
    IDENTITY_FILE.parent.mkdir(parents=True, exist_ok=True)
    IDENTITY_FILE.write_text(json.dumps(identity, indent=2))
    try:
        os.chmod(IDENTITY_FILE, 0o600)
    except OSError:
        pass


def base_session(token: str | None = None) -> requests.Session:
    session = requests.Session()
    if token:
        session.headers.update({"Authorization": f"Bearer {token}"})
    return session


def enroll() -> dict[str, str]:
    identity = read_identity()
    if identity:
        return identity
    session = base_session(ENROLLMENT_TOKEN)
    response = session.post(
        f"{CONTROLLER_URL}/workers/enroll",
        json={"name": socket.gethostname()},
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    identity = {"worker_id": data["worker_id"], "device_token": data["device_token"]}
    save_identity(identity)
    return identity


def termux_battery() -> dict[str, Any]:
    try:
        p = subprocess.run(
            ["termux-battery-status"], capture_output=True, text=True, timeout=3, check=True
        )
        data = json.loads(p.stdout)
        return {
            "temperature_c": float(data["temperature"]) if data.get("temperature") is not None else None,
            "battery_pct": float(data["percentage"]) if data.get("percentage") is not None else None,
            "charging": str(data.get("status", "")).upper() in {"CHARGING", "FULL"},
        }
    except Exception:
        return {}


def sysfs_temperature() -> float | None:
    temps = []
    for path in Path("/sys/class/thermal").glob("thermal_zone*/temp"):
        try:
            raw = float(path.read_text().strip())
            temp = raw / 1000 if raw > 500 else raw
            if 10 <= temp <= 120:
                temps.append(temp)
        except Exception:
            pass
    return max(temps) if temps else None


def memory_mb() -> int | None:
    try:
        if sys.platform.startswith("linux"):
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
        if sys.platform == "darwin":
            p = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            )
            return int(p.stdout.strip()) // (1024 * 1024)
        if os.name == "nt":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return int(stat.ullTotalPhys // (1024 * 1024))
    except Exception:
        pass
    return None


def telemetry() -> dict[str, Any]:
    data = termux_battery()
    if data.get("temperature_c") is None:
        data["temperature_c"] = sysfs_temperature()
    return {
        "temperature_c": data.get("temperature_c"),
        "battery_pct": data.get("battery_pct"),
        "charging": data.get("charging"),
    }


def benchmark() -> float:
    n = 150_000
    started = time.perf_counter()
    x = b"universal-compute-swarm"
    for _ in range(n):
        x = hashlib.blake2s(x, digest_size=16).digest()
    return n / max(time.perf_counter() - started, 1e-9)


def capabilities() -> list[str]:
    caps = {"cpu", "python", f"os:{platform.system().lower()}", f"arch:{platform.machine().lower()}"}
    caps.update(EXTRA_CAPABILITIES)
    caps.update(f"task:{name}" for name in TASKS)
    return sorted(caps)


def post(session: requests.Session, path: str, payload: dict[str, Any], timeout: float = 30) -> dict[str, Any]:
    response = session.post(f"{CONTROLLER_URL}{path}", json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()


def should_pause(t: dict[str, Any], paused: bool) -> bool:
    temp = t.get("temperature_c")
    battery = t.get("battery_pct")
    charging = t.get("charging")
    if battery is not None and battery < MIN_BATTERY_PCT and not charging:
        return True
    if temp is None:
        return False
    if paused:
        return temp > RESUME_TEMP_C
    return temp >= MAX_TEMP_C


def register(session: requests.Session, wid: str, score: float) -> int:
    info = telemetry()
    response = post(
        session,
        f"/workers/{wid}/register",
        {
            "name": socket.gethostname(),
            "os_name": platform.system(),
            "platform": platform.platform(),
            "arch": platform.machine(),
            "cores": os.cpu_count() or 1,
            "memory_mb": memory_mb(),
            "benchmark": score,
            "capabilities": capabilities(),
            "labels": LABELS,
            "agent_version": AGENT_VERSION,
            **info,
        },
    )
    return int(response.get("lease_seconds", 120))


def _safe_name(value: str, fallback: str) -> str:
    name = Path(value).name.strip()
    return name or fallback


def download_artifacts(
    session: requests.Session,
    wid: str,
    work: dict[str, Any],
    sandbox: Path,
) -> dict[str, str]:
    inputs = work.get("payload", {}).get("artifact_inputs", [])
    if not isinstance(inputs, list):
        raise ValueError("artifact_inputs must be a list")
    paths: dict[str, str] = {}
    for index, item in enumerate(inputs):
        if not isinstance(item, dict) or not item.get("artifact_id"):
            raise ValueError("each artifact input needs artifact_id")
        artifact_id = str(item["artifact_id"])
        alias = str(item.get("alias") or f"artifact_{index}")
        name = _safe_name(str(item.get("name") or artifact_id), f"artifact_{index}")
        destination = sandbox / name
        with session.get(
            f"{CONTROLLER_URL}/artifacts/{artifact_id}",
            headers={"X-Worker-ID": wid},
            stream=True,
            timeout=(20, 300),
        ) as response:
            response.raise_for_status()
            expected = response.headers.get("X-Artifact-Sha256")
            hasher = hashlib.sha256()
            with destination.open("wb") as out:
                for chunk in response.iter_content(1024 * 1024):
                    if not chunk:
                        continue
                    hasher.update(chunk)
                    out.write(chunk)
        if expected and hasher.hexdigest().lower() != expected.lower():
            destination.unlink(missing_ok=True)
            raise RuntimeError(f"artifact checksum mismatch: {artifact_id}")
        paths[alias] = str(destination)
    return paths


def upload_artifact(
    session: requests.Session,
    wid: str,
    path: Path,
    *,
    name: str,
    content_type: str | None = None,
) -> dict[str, Any]:
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    digest = hasher.hexdigest()
    with path.open("rb") as fh:
        response = session.post(
            f"{CONTROLLER_URL}/workers/{wid}/artifacts?name={quote(name)}",
            data=fh,
            headers={
                "Content-Type": content_type or mimetypes.guess_type(name)[0] or "application/octet-stream",
                "X-Artifact-Sha256": digest,
            },
            timeout=(20, 600),
        )
    response.raise_for_status()
    return response.json()


def normalize_artifact_outputs(
    session: requests.Session,
    wid: str,
    sandbox: Path,
    result: Any,
) -> Any:
    if not isinstance(result, dict) or "_artifact_outputs" not in result:
        return result
    outputs = result.pop("_artifact_outputs")
    if not isinstance(outputs, list):
        raise ValueError("_artifact_outputs must be a list")

    uploaded = []
    sandbox_real = sandbox.resolve()
    for index, item in enumerate(outputs):
        if not isinstance(item, dict) or not item.get("path"):
            raise ValueError("each artifact output needs a path")
        candidate = (sandbox / str(item["path"])).resolve()
        try:
            candidate.relative_to(sandbox_real)
        except ValueError as exc:
            raise ValueError("artifact output must stay inside the work sandbox") from exc
        if not candidate.is_file():
            raise FileNotFoundError(candidate)
        name = _safe_name(str(item.get("name") or candidate.name), f"output_{index}")
        uploaded.append(
            upload_artifact(
                session,
                wid,
                candidate,
                name=name,
                content_type=item.get("content_type"),
            )
        )
    result["artifacts"] = uploaded
    return result


class LeaseKeeper:
    def __init__(self, session: requests.Session, wid: str, lease_id: str, lease_seconds: int):
        self.session = session
        self.wid = wid
        self.lease_id = lease_id
        self.interval = max(5.0, lease_seconds / 3)
        self.stop_event = threading.Event()
        self.error: Exception | None = None
        self.thread = threading.Thread(target=self._run, name="swarm-lease-keeper", daemon=True)

    def _run(self) -> None:
        while not self.stop_event.wait(self.interval):
            try:
                post(self.session, f"/workers/{self.wid}/leases/{self.lease_id}/renew", {}, timeout=15)
            except Exception as exc:
                self.error = exc
                return

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.stop_event.set()
        self.thread.join(timeout=2)


def execute_work(
    session: requests.Session,
    wid: str,
    work: dict[str, Any],
    lease_seconds: int,
) -> tuple[Any, float]:
    handler = TASKS.get(work["kind"])
    if handler is None:
        raise RuntimeError(f"unsupported task kind: {work['kind']}")

    sandbox = WORK_ROOT / work["job_id"] / work["unit_id"]
    shutil.rmtree(sandbox, ignore_errors=True)
    sandbox.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    try:
        with LeaseKeeper(session, wid, work["lease_id"], lease_seconds) as keeper:
            payload = dict(work.get("payload", {}))
            payload["_work_dir"] = str(sandbox)
            payload["_artifact_paths"] = download_artifacts(session, wid, work, sandbox)
            result = handler(payload)
            result = normalize_artifact_outputs(session, wid, sandbox, result)
            if keeper.error:
                raise RuntimeError(f"lease renewal failed: {keeper.error}")
            return result, (time.perf_counter() - started) * 1000
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def main() -> None:
    validate_controller_url()
    load_plugins()
    identity = enroll()
    wid = identity["worker_id"]
    session = base_session(identity["device_token"])
    score = benchmark()
    lease_seconds = register(session, wid, score)
    print(
        f"joined swarm as {wid} | {platform.system()} {platform.machine()} | "
        f"{len(TASKS)} tasks | benchmark={score:,.0f} iter/s"
    )

    paused = False
    last_heartbeat = 0.0
    while True:
        try:
            now = time.time()
            info = telemetry()
            paused = should_pause(info, paused)
            if now - last_heartbeat > 10:
                post(session, f"/workers/{wid}/heartbeat", {**info, "capabilities": capabilities()})
                last_heartbeat = now
            if paused:
                print(f"paused for thermal/battery limits: {info}")
                time.sleep(5)
                continue

            leased = post(session, f"/workers/{wid}/lease?wait_seconds=15", {}, timeout=25)
            work = leased["work"]
            if work is None:
                time.sleep(POLL_SECONDS)
                continue

            try:
                result, elapsed_ms = execute_work(session, wid, work, lease_seconds)
                post(
                    session,
                    f"/workers/{wid}/units/{work['unit_id']}/result",
                    {"lease_id": work["lease_id"], "result": result, "elapsed_ms": elapsed_ms},
                )
                print(f"done {work['kind']} unit={work['sequence']} in {elapsed_ms:.0f} ms")
            except Exception as exc:
                post(
                    session,
                    f"/workers/{wid}/units/{work['unit_id']}/failure",
                    {"lease_id": work["lease_id"], "error": str(exc), "retry": False},
                )
                print(f"task failed: {exc}")
        except KeyboardInterrupt:
            raise
        except requests.HTTPError as exc:
            print(f"controller HTTP error: {exc}")
            time.sleep(3)
        except Exception as exc:
            print(f"worker error: {exc}")
            time.sleep(3)


if __name__ == "__main__":
    main()
