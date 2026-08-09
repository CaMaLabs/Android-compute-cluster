from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from app import _loads, admin_auth, app, db
from experiments import ExperimentRequest, _create_experiment

OLLAMA_URL = os.getenv("SWARM_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("SWARM_OLLAMA_MODEL", "qwen2.5:7b")

TASK_PAYLOAD_HINTS: dict[str, dict[str, Any]] = {
    "prime_count": {
        "required_numeric_fields": ["start", "end"],
        "example_parameters": {"start": {"values": [2]}, "end": {"values": [10000, 20000, 30000]}},
        "objective_paths": ["count"],
    },
    "monte_carlo_pi": {
        "required_numeric_fields": ["start", "end"],
        "example_parameters": {"start": {"values": [0]}, "end": {"values": [10000, 20000, 30000]}},
        "objective_paths": ["inside"],
    },
    "sha256_artifact": {
        "required_fields": ["text"],
        "example_parameters": {"text": {"values": ["hello"]}},
        "objective_paths": ["sha256", "bytes"],
    },
    "text_artifact": {
        "required_fields": ["name", "text"],
        "example_parameters": {"name": {"values": ["result.txt"]}, "text": {"values": ["hello"]}},
        "objective_paths": ["bytes"],
    },
}


class ExperimentAssistantRequest(BaseModel):
    request: str = Field(min_length=1, max_length=8000)
    mode: Literal["draft", "launch"] = "draft"
    model: str | None = Field(default=None, max_length=200)
    temperature: float = Field(default=0.1, ge=0, le=1)


class ExperimentAssistantResponse(BaseModel):
    mode: str
    model: str
    draft: dict[str, Any]
    validation: dict[str, Any]
    launched: dict[str, Any] | None = None


def _known_swarm_context() -> dict[str, Any]:
    with db() as conn:
        workers = conn.execute(
            "SELECT name,os_name,arch,cores,memory_mb,capabilities_json,labels_json,enabled,last_seen FROM workers"
        ).fetchall()
    tasks: set[str] = set()
    capabilities: set[str] = set()
    worker_summaries = []
    for row in workers:
        caps = _loads(row["capabilities_json"], [])
        labels = _loads(row["labels_json"], {})
        for cap in caps:
            capabilities.add(str(cap))
            if str(cap).startswith("task:"):
                tasks.add(str(cap)[5:])
        worker_summaries.append(
            {
                "name": row["name"],
                "os_name": row["os_name"],
                "arch": row["arch"],
                "cores": row["cores"],
                "memory_mb": row["memory_mb"],
                "labels": labels,
                "capabilities": caps,
                "enabled": bool(row["enabled"]),
            }
        )
    return {
        "available_tasks": sorted(tasks),
        "available_capabilities": sorted(capabilities),
        "task_payload_hints": {name: TASK_PAYLOAD_HINTS[name] for name in sorted(tasks) if name in TASK_PAYLOAD_HINTS},
        "workers": worker_summaries,
        "experiment_schema": {
            "name": "string",
            "task": "one available task name",
            "parameters": {"task_payload_field": {"values": ["..."], "start": 0, "stop": 10, "step": 1}},
            "objective": {"path": "result field path", "direction": "maximize|minimize"},
            "base_payload": {},
            "requirements": {"capabilities": ["cpu"], "labels": {}},
            "priority": 0,
            "replicates": 1,
            "replicate_parameter": None,
            "metadata": {},
        },
    }


def _prompt(user_request: str) -> str:
    context = _known_swarm_context()
    return (
        "You translate a user's natural-language compute experiment request into one JSON object. "
        "Return only JSON, no Markdown. Do not invent shell commands, code execution, or new protocols. "
        "Use only locally advertised typed tasks and capabilities. If details are missing, choose a small safe sweep. "
        "Parameter names must be actual task payload fields. Prefer values over start/stop/step for small sweeps. "
        "The JSON must match this ExperimentRequest shape: "
        f"{json.dumps(context['experiment_schema'], separators=(',', ':'))}. "
        "Use requirements.capabilities so workers can lease compatible jobs. "
        f"Current swarm context: {json.dumps(context, separators=(',', ':'))}. "
        f"User request: {user_request}"
    )


def _ollama_generate(prompt: str, *, model: str, temperature: float) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": temperature},
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise HTTPException(503, f"Ollama unavailable at {OLLAMA_URL}: {exc}") from exc
    return str(body.get("response", ""))


def _json_from_text(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise HTTPException(422, "model did not return a JSON object")
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise HTTPException(422, "model response must be a JSON object")
    return value


def _coerce_scalar(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return value
    try:
        if re.fullmatch(r"[-+]?\d+", stripped):
            return int(stripped)
        if re.fullmatch(r"[-+]?(?:\d+\.\d*|\d*\.\d+)(?:[eE][-+]?\d+)?|[-+]?\d+[eE][-+]?\d+", stripped):
            return float(stripped)
    except ValueError:
        return value
    return value


def _normalize_draft(draft: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(draft)
    parameters = normalized.get("parameters")
    if not isinstance(parameters, dict):
        return normalized

    normalized_parameters: dict[str, Any] = {}
    for name, spec in parameters.items():
        if not isinstance(spec, dict):
            normalized_parameters[name] = spec
            continue
        normalized_spec = dict(spec)
        if isinstance(normalized_spec.get("values"), list):
            normalized_spec["values"] = [_coerce_scalar(value) for value in normalized_spec["values"]]
        for key in ("start", "stop", "step"):
            if key in normalized_spec:
                normalized_spec[key] = _coerce_scalar(normalized_spec[key])
        normalized_parameters[name] = normalized_spec
    normalized["parameters"] = normalized_parameters
    return normalized


def _validate_task_payload(experiment: ExperimentRequest) -> None:
    hints = TASK_PAYLOAD_HINTS.get(experiment.task)
    if not hints:
        return
    payload_fields = set(experiment.base_payload) | set(experiment.parameters)
    missing = [field for field in hints.get("required_fields", []) if field not in payload_fields]
    missing += [field for field in hints.get("required_numeric_fields", []) if field not in payload_fields]
    if missing:
        raise ValueError(f"{experiment.task} requires payload field(s): {', '.join(missing)}")

    for field in hints.get("required_numeric_fields", []):
        if field in experiment.base_payload:
            values = [experiment.base_payload[field]]
        else:
            spec = experiment.parameters[field]
            values = spec.values or [spec.start, spec.stop, spec.step]
        for value in values:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{experiment.task}.{field} must be numeric")


@app.post("/llm/experiments", dependencies=[Depends(admin_auth)])
def interpret_experiment(req: ExperimentAssistantRequest):
    model = req.model or OLLAMA_MODEL
    text = _ollama_generate(_prompt(req.request), model=model, temperature=req.temperature)
    draft = _normalize_draft(_json_from_text(text))
    try:
        experiment = ExperimentRequest(**draft)
        _validate_task_payload(experiment)
    except Exception as exc:
        return ExperimentAssistantResponse(
            mode=req.mode,
            model=model,
            draft=draft,
            validation={"ok": False, "error": str(exc)},
            launched=None,
        )
    launched = _create_experiment(experiment) if req.mode == "launch" else None
    return ExperimentAssistantResponse(
        mode=req.mode,
        model=model,
        draft=experiment.model_dump(),
        validation={"ok": True},
        launched=launched,
    )
