from __future__ import annotations

import csv
import importlib.metadata
import importlib.util
import json
import os
import tarfile
from pathlib import Path
from typing import Any

from swarm_plugin import advertise, task

ENABLED = os.getenv("SWARM_ENABLE_KAGGLE_CABT", "0") == "1"
MAX_FILES = 20_000
MAX_EXTRACTED_BYTES = 512 * 1024 * 1024


def _safe_extract(bundle: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    total = 0
    count = 0
    root = destination.resolve()
    with tarfile.open(bundle, "r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            count += 1
            if count > MAX_FILES:
                raise ValueError("CABT bundle contains too many files")
            if member.issym() or member.islnk() or member.isdev():
                raise ValueError(f"unsupported archive member: {member.name}")
            if member.size < 0:
                raise ValueError(f"invalid archive member size: {member.name}")
            total += int(member.size)
            if total > MAX_EXTRACTED_BYTES:
                raise ValueError("CABT bundle expands beyond 512 MiB")
            target = (destination / member.name).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"unsafe archive path: {member.name}") from exc
        archive.extractall(destination, members=members, filter="data")


def _read_deck(root: Path) -> list[int]:
    deck_path = root / "deck.csv"
    if not deck_path.is_file():
        raise ValueError("Kaggle CABT bundle must contain deck.csv at the archive root")
    values: list[int] = []
    with deck_path.open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.reader(fh):
            for cell in row:
                for token in cell.split():
                    if token.strip():
                        values.append(int(token.strip()))
    if len(values) != 60:
        raise ValueError(f"deck.csv must contain exactly 60 card IDs; found {len(values)}")
    return values


def _write_wrapper(root: Path, logical_name: str) -> Path:
    main = root / "main.py"
    if not main.is_file():
        raise ValueError("Kaggle CABT bundle must contain main.py at the archive root")
    deck = _read_deck(root)
    wrapper = root / f"_swarm_{logical_name}.py"
    source = f"""# Generated locally by Compute Swarm. The controller never supplies this code.
import importlib.util as _iu
import os as _os
import sys as _sys
from pathlib import Path as _Path

_ROOT = _Path({str(root)!r})
_DECK = {deck!r}
_sys.path.insert(0, str(_ROOT))
_spec = _iu.spec_from_file_location("_swarm_user_{logical_name}", _ROOT / "main.py")
if _spec is None or _spec.loader is None:
    raise RuntimeError("could not load CABT main.py")
_mod = _iu.module_from_spec(_spec)
_old = _os.getcwd()
try:
    _os.chdir(_ROOT)
    _spec.loader.exec_module(_mod)
finally:
    _os.chdir(_old)
_user_agent = getattr(_mod, "agent", None)
if not callable(_user_agent):
    raise RuntimeError("main.py must expose callable agent(obs_dict)")

def agent(obs_dict):
    if not isinstance(obs_dict, dict):
        try:
            obs_dict = dict(obs_dict)
        except Exception:
            pass
    if isinstance(obs_dict, dict) and obs_dict.get("select") is None:
        return list(_DECK)
    old = _os.getcwd()
    try:
        _os.chdir(_ROOT)
        return _user_agent(obs_dict)
    finally:
        _os.chdir(old)
"""
    wrapper.write_text(source, encoding="utf-8")
    return wrapper


def _state_value(state: Any, key: str) -> Any:
    if isinstance(state, dict):
        return state.get(key)
    return getattr(state, key, None)


def _classify(a_reward: Any, b_reward: Any, a_status: Any, b_status: Any) -> str:
    if isinstance(a_reward, (int, float)) and isinstance(b_reward, (int, float)):
        if a_reward > b_reward:
            return "agent_win"
        if b_reward > a_reward:
            return "opponent_win"
        return "draw"
    bad = {"ERROR", "INVALID", "TIMEOUT"}
    if str(a_status) in bad and str(b_status) not in bad:
        return "opponent_win"
    if str(b_status) in bad and str(a_status) not in bad:
        return "agent_win"
    return "runtime_error"


def _run_episode(payload: dict[str, Any]) -> dict[str, Any]:
    from kaggle_environments import make

    paths = payload.get("_artifact_paths", {})
    agent_bundle = Path(str(paths.get("agent", "")))
    opponent_bundle = Path(str(paths.get("opponent", "")))
    if not agent_bundle.is_file() or not opponent_bundle.is_file():
        raise ValueError("CABT episode requires agent and opponent artifact inputs")

    work_dir = Path(payload["_work_dir"])
    agent_root = work_dir / "agent"
    opponent_root = work_dir / "opponent"
    _safe_extract(agent_bundle, agent_root)
    _safe_extract(opponent_bundle, opponent_root)
    agent_wrapper = _write_wrapper(agent_root, "agent")
    opponent_wrapper = _write_wrapper(opponent_root, "opponent")

    seat_swap = bool(payload.get("seat_swap", False))
    timeout = max(30, min(2000, int(payload.get("run_timeout_seconds", 1200))))
    ordered = (
        [str(opponent_wrapper), str(agent_wrapper)]
        if seat_swap
        else [str(agent_wrapper), str(opponent_wrapper)]
    )
    env = make("cabt", configuration={"runTimeout": timeout}, debug=False)
    steps = env.run(ordered)
    if not steps:
        raise RuntimeError("CABT returned no episode steps")
    final = steps[-1]
    if len(final) != 2:
        raise RuntimeError("CABT final state does not contain two agents")

    logical_a_seat = 1 if seat_swap else 0
    logical_b_seat = 1 - logical_a_seat
    a = final[logical_a_seat]
    b = final[logical_b_seat]
    a_reward = _state_value(a, "reward")
    b_reward = _state_value(b, "reward")
    a_status = _state_value(a, "status")
    b_status = _state_value(b, "status")

    result: dict[str, Any] = {
        "match_index": int(payload.get("match_index", 0)),
        "seat_swap": seat_swap,
        "outcome": _classify(a_reward, b_reward, a_status, b_status),
        "agent_reward": a_reward,
        "opponent_reward": b_reward,
        "agent_status": a_status,
        "opponent_status": b_status,
        "steps": len(steps),
        "engine_version": importlib.metadata.version("kaggle-environments"),
    }
    errors = []
    for index, state in enumerate(final):
        err = _state_value(state, "error")
        if err:
            errors.append({"seat": index, "error": str(err)[:2000]})
    if errors:
        result["errors"] = errors

    if bool(payload.get("save_replay", False)):
        name = f"cabt-replay-{int(payload.get('match_index', 0)):06d}.json"
        replay = work_dir / name
        try:
            replay.write_text(json.dumps(env.toJSON()), encoding="utf-8")
            result["_artifact_outputs"] = [
                {"path": name, "name": name, "content_type": "application/json"}
            ]
        except Exception as exc:
            result["replay_error"] = str(exc)[:1000]
    return result


if ENABLED and importlib.util.find_spec("kaggle_environments") is not None:
    advertise("kaggle:cabt")
    advertise("competition:pokemon-tcg-ai-battle")

    @task("kaggle_cabt_episode")
    def kaggle_cabt_episode(payload: dict[str, Any]) -> dict[str, Any]:
        return _run_episode(payload)
