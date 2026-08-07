from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import platform
import random
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

AGENT_VERSION = "0.2.0"
CONTROLLER_URL = os.getenv("SWARM_CONTROLLER_URL", os.getenv("CLUSTER_URL", "http://127.0.0.1:8765")).rstrip("/")
ENROLLMENT_TOKEN = os.getenv("SWARM_ENROLLMENT_TOKEN", "dev-enroll-token-change-me")
IDENTITY_FILE = Path(os.getenv("SWARM_IDENTITY_FILE", str(Path.home() / ".compute-swarm-identity.json")))
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

from swarm_plugin import TASKS, task


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
    inside = 0
    for i in range(start, end):
        rng = random.Random(i)
        x = rng.random()
        y = rng.random()
        inside += int(x * x + y * y <= 1.0)
    return {"inside": inside, "samples": end - start}


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
            p = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=2, check=True)
            return int(p.stdout.strip()) // (1024 * 1024)
        if os.name == "nt":
            try:
                import ctypes
                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
                stat = MEMORYSTATUSEX()
                stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
                ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
                return int(stat.ullTotalPhys // (1024 * 1024))
            except Exception:
                pass
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


def execute_with_renewal(
    session: requests.Session,
    wid: str,
    work: dict[str, Any],
    lease_seconds: int,
) -> tuple[Any, float]:
    handler = TASKS.get(work["kind"])
    if handler is None:
        raise RuntimeError(f"unsupported task kind: {work['kind']}")
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="swarm-task") as executor:
        future = executor.submit(handler, work["payload"])
        interval = max(5.0, lease_seconds / 3)
        while True:
            try:
                result = future.result(timeout=interval)
                return result, (time.perf_counter() - started) * 1000
            except TimeoutError:
                post(session, f"/workers/{wid}/leases/{work['lease_id']}/renew", {}, timeout=15)


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
                result, elapsed_ms = execute_with_renewal(session, wid, work, lease_seconds)
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
