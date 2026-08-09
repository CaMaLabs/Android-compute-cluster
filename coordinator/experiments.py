from __future__ import annotations

import itertools
import math
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app import JobRequest, Requirements, _loads, admin_auth, app, create_job, db

MAX_EXPERIMENT_UNITS = 10_000
EXPERIMENT_META_KEY = "_swarm_experiment"
PAYLOAD_META_KEY = "_swarm_experiment"


class SweepParameter(BaseModel):
    values: list[Any] | None = None
    start: int | float | None = None
    stop: int | float | None = None
    step: int | float | None = None


class Objective(BaseModel):
    path: str = Field(min_length=1, max_length=300)
    direction: Literal["maximize", "minimize"] = "maximize"


class ExperimentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    task: str = Field(min_length=1, max_length=200)
    parameters: dict[str, SweepParameter] = Field(min_length=1)
    objective: Objective | None = None
    base_payload: dict[str, Any] = Field(default_factory=dict)
    requirements: Requirements = Field(default_factory=Requirements)
    priority: int = Field(default=0, ge=-1000, le=1000)
    replicates: int = Field(default=1, ge=1, le=1000)
    replicate_parameter: str | None = Field(default=None, max_length=200)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RefinementRequest(BaseModel):
    top_k: int = Field(default=3, ge=1, le=50)
    shrink: float = Field(default=0.25, gt=0, le=1)
    points_per_axis: int = Field(default=5, ge=3, le=21)
    priority_delta: int = Field(default=1, ge=-100, le=100)


def _number(value: Any) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("range parameters require numeric start/stop/step")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("range parameters must be finite")
    return value


def _range_values(spec: SweepParameter) -> list[Any]:
    if spec.values is not None:
        if not spec.values:
            raise ValueError("values must not be empty")
        if len(spec.values) > MAX_EXPERIMENT_UNITS:
            raise ValueError("parameter has too many explicit values")
        return list(spec.values)

    if spec.start is None or spec.stop is None or spec.step is None:
        raise ValueError("parameter must define either values or start/stop/step")
    start = _number(spec.start)
    stop = _number(spec.stop)
    step = _number(spec.step)
    if step == 0:
        raise ValueError("step must not be zero")
    if start < stop and step < 0:
        raise ValueError("step must be positive when stop > start")
    if start > stop and step > 0:
        raise ValueError("step must be negative when stop < start")

    try:
        d_start = Decimal(str(start))
        d_stop = Decimal(str(stop))
        d_step = Decimal(str(step))
    except InvalidOperation as exc:
        raise ValueError("invalid numeric range") from exc

    integer_range = all(isinstance(v, int) and not isinstance(v, bool) for v in (start, stop, step))
    values: list[Any] = []
    current = d_start

    def in_bounds(value: Decimal) -> bool:
        return value <= d_stop if d_step > 0 else value >= d_stop

    while in_bounds(current):
        values.append(int(current) if integer_range else float(current))
        if len(values) > MAX_EXPERIMENT_UNITS:
            raise ValueError("parameter range produces too many values")
        current += d_step
    if not values:
        raise ValueError("parameter range is empty")
    return values


def _parameter_axes(parameters: dict[str, SweepParameter]) -> tuple[list[str], list[list[Any]]]:
    names = list(parameters)
    axes: list[list[Any]] = []
    for name in names:
        if name.startswith("_"):
            raise ValueError(f"parameter name is reserved: {name}")
        axes.append(_range_values(parameters[name]))
    return names, axes


def _expand_points(parameters: dict[str, SweepParameter]) -> list[dict[str, Any]]:
    names, axes = _parameter_axes(parameters)
    count = 1
    for axis in axes:
        count *= len(axis)
        if count > MAX_EXPERIMENT_UNITS:
            raise ValueError(
                f"experiment expands to more than {MAX_EXPERIMENT_UNITS} parameter points; "
                "split the sweep or reduce the grid"
            )
    return [dict(zip(names, values)) for values in itertools.product(*axes)]


def _units_from_points(
    points: list[dict[str, Any]],
    *,
    base_payload: dict[str, Any],
    replicates: int,
    replicate_parameter: str | None,
    parent_id: str | None,
    generation: int,
) -> list[dict[str, Any]]:
    if len(points) * replicates > MAX_EXPERIMENT_UNITS:
        raise HTTPException(
            400,
            f"experiment expands to more than {MAX_EXPERIMENT_UNITS} work units after replicates",
        )
    units: list[dict[str, Any]] = []
    for params in points:
        for replicate in range(replicates):
            payload = dict(base_payload)
            payload.update(params)
            if replicate_parameter:
                payload[replicate_parameter] = replicate
            payload[PAYLOAD_META_KEY] = {
                "parameters": params,
                "replicate": replicate,
                "parent_experiment_id": parent_id,
                "generation": generation,
            }
            units.append(payload)
    return units


def _spec_metadata(req: ExperimentRequest) -> dict[str, Any]:
    return {
        "name": req.name,
        "task": req.task,
        "parameters": {name: spec.model_dump(exclude_none=True) for name, spec in req.parameters.items()},
        "objective": req.objective.model_dump() if req.objective else None,
        "replicates": req.replicates,
        "replicate_parameter": req.replicate_parameter,
        "base_payload": req.base_payload,
        "scheduler": "adaptive_pull",
    }


def _create_from_points(
    req: ExperimentRequest,
    points: list[dict[str, Any]],
    *,
    parent_id: str | None = None,
    generation: int = 0,
    refinement: dict[str, Any] | None = None,
    original_parameter_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    units = _units_from_points(
        points,
        base_payload=req.base_payload,
        replicates=req.replicates,
        replicate_parameter=req.replicate_parameter,
        parent_id=parent_id,
        generation=generation,
    )
    experiment_meta = _spec_metadata(req)
    if original_parameter_spec is not None:
        experiment_meta["parameters"] = original_parameter_spec
    experiment_meta.update(
        {
            "parent_experiment_id": parent_id,
            "generation": generation,
            "refinement": refinement,
            "parameter_points": len(points),
        }
    )
    metadata = dict(req.metadata)
    metadata[EXPERIMENT_META_KEY] = experiment_meta
    created = create_job(
        JobRequest(
            kind=req.task,
            units=units,
            requirements=req.requirements,
            priority=req.priority,
            metadata=metadata,
        )
    )
    return {
        "experiment_id": created["job_id"],
        "job_id": created["job_id"],
        "name": req.name,
        "task": req.task,
        "parameter_points": len(points),
        "units": created["units"],
        "scheduler": "adaptive_pull",
        "objective": req.objective.model_dump() if req.objective else None,
        "parent_experiment_id": parent_id,
        "generation": generation,
    }


def _create_experiment(req: ExperimentRequest) -> dict[str, Any]:
    try:
        points = _expand_points(req.parameters)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _create_from_points(req, points)


def _extract_path(value: Any, path: str) -> Any:
    current = value
    for token in path.split("."):
        if isinstance(current, dict):
            if token not in current:
                return None
            current = current[token]
        elif isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def _numeric_score(result: Any, objective: dict[str, Any] | None) -> float | None:
    if not objective:
        return None
    value = _extract_path(result, str(objective["path"]))
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    score = float(value)
    return score if math.isfinite(score) else None


def _experiment_job(experiment_id: str):
    with db() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (experiment_id,)).fetchone()
        if job is None:
            raise HTTPException(404, "experiment not found")
        metadata = _loads(job["metadata_json"], {})
        experiment = metadata.get(EXPERIMENT_META_KEY)
        if not isinstance(experiment, dict):
            raise HTTPException(404, "job is not an experiment")
        units = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id,sequence,status,worker_id,elapsed_ms,error,attempts,payload_json,result_json
                FROM work_units WHERE job_id=? ORDER BY sequence
                """,
                (experiment_id,),
            )
        ]
    return job, metadata, experiment, units


def _experiment_summary(job: Any, experiment: dict[str, Any], units: list[dict[str, Any]]) -> dict[str, Any]:
    statuses: dict[str, int] = {}
    for unit in units:
        statuses[unit["status"]] = statuses.get(unit["status"], 0) + 1
    return {
        "experiment_id": job["id"],
        "name": experiment.get("name"),
        "task": job["kind"],
        "generation": experiment.get("generation", 0),
        "parent_experiment_id": experiment.get("parent_experiment_id"),
        "objective": experiment.get("objective"),
        "created_at": job["created_at"],
        "priority": job["priority"],
        "parameter_points": experiment.get("parameter_points"),
        "units": len(units),
        "queued": statuses.get("queued", 0),
        "leased": statuses.get("leased", 0),
        "done": statuses.get("done", 0),
        "failed": statuses.get("failed", 0),
        "scheduler": experiment.get("scheduler", "adaptive_pull"),
    }


def _job_requirements(job_id: str) -> Requirements:
    with db() as conn:
        row = conn.execute("SELECT requirements_json FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "experiment not found")
    return Requirements(**_loads(row["requirements_json"], {}))


@app.post("/experiments", dependencies=[Depends(admin_auth)])
def create_experiment(req: ExperimentRequest):
    return _create_experiment(req)


@app.get("/experiments", dependencies=[Depends(admin_auth)])
def list_experiments():
    with db() as conn:
        jobs = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
        summaries: list[dict[str, Any]] = []
        for job in jobs:
            metadata = _loads(job["metadata_json"], {})
            experiment = metadata.get(EXPERIMENT_META_KEY)
            if not isinstance(experiment, dict):
                continue
            units = [
                dict(row)
                for row in conn.execute("SELECT status FROM work_units WHERE job_id=?", (job["id"],))
            ]
            summaries.append(_experiment_summary(job, experiment, units))
    return {"experiments": summaries}


@app.get("/experiments/{experiment_id}", dependencies=[Depends(admin_auth)])
def get_experiment(experiment_id: str, top: int = Query(default=25, ge=1, le=500)):
    job, metadata, experiment, units = _experiment_job(experiment_id)
    objective = experiment.get("objective")
    ranked: list[dict[str, Any]] = []
    worker_stats: dict[str, dict[str, Any]] = {}

    for unit in units:
        payload = _loads(unit.pop("payload_json"), {})
        result = _loads(unit.pop("result_json"), None)
        sweep_meta = payload.get(PAYLOAD_META_KEY, {}) if isinstance(payload, dict) else {}
        parameters = sweep_meta.get("parameters", {}) if isinstance(sweep_meta, dict) else {}
        replicate = sweep_meta.get("replicate", 0) if isinstance(sweep_meta, dict) else 0

        if unit["status"] == "done":
            score = _numeric_score(result, objective)
            if score is not None:
                ranked.append(
                    {
                        "unit_id": unit["id"],
                        "sequence": unit["sequence"],
                        "worker_id": unit["worker_id"],
                        "elapsed_ms": unit["elapsed_ms"],
                        "parameters": parameters,
                        "replicate": replicate,
                        "score": score,
                        "result": result,
                    }
                )
            if unit["worker_id"]:
                stat = worker_stats.setdefault(
                    unit["worker_id"],
                    {"worker_id": unit["worker_id"], "units_done": 0, "elapsed_ms": 0.0},
                )
                stat["units_done"] += 1
                stat["elapsed_ms"] += float(unit["elapsed_ms"] or 0.0)

    if objective:
        reverse = objective.get("direction", "maximize") == "maximize"
        ranked.sort(key=lambda item: item["score"], reverse=reverse)

    stats: list[dict[str, Any]] = []
    for stat in worker_stats.values():
        elapsed = stat["elapsed_ms"]
        stat["avg_elapsed_ms"] = elapsed / stat["units_done"] if stat["units_done"] else None
        stat["units_per_second"] = (stat["units_done"] * 1000.0 / elapsed) if elapsed > 0 else None
        stats.append(stat)
    stats.sort(key=lambda item: item["units_done"], reverse=True)

    return {
        **_experiment_summary(job, experiment, units),
        "metadata": {k: v for k, v in metadata.items() if k != EXPERIMENT_META_KEY},
        "spec": experiment,
        "ranked_results": ranked[:top],
        "best": ranked[0] if ranked else None,
        "worker_throughput": stats,
    }


def _refinement_axis(
    spec: dict[str, Any],
    center: Any,
    *,
    shrink: float,
    points_per_axis: int,
) -> list[Any]:
    if not isinstance(center, (int, float)) or isinstance(center, bool):
        return [center]
    if not all(key in spec for key in ("start", "stop", "step")):
        return [center]

    low = float(min(spec["start"], spec["stop"]))
    high = float(max(spec["start"], spec["stop"]))
    span = high - low
    if span <= 0:
        return [center]
    local_span = span * shrink
    start = max(low, float(center) - local_span / 2.0)
    stop = min(high, float(center) + local_span / 2.0)
    integer_axis = all(
        isinstance(spec.get(key), int) and not isinstance(spec.get(key), bool)
        for key in ("start", "stop", "step")
    )
    values: list[Any] = []
    for i in range(points_per_axis):
        value = start + (stop - start) * i / (points_per_axis - 1)
        value = int(round(value)) if integer_axis else value
        if value not in values:
            values.append(value)
    if center not in values:
        values.append(center)
    return sorted(values)


@app.post("/experiments/{experiment_id}/refine", dependencies=[Depends(admin_auth)])
def refine_experiment(experiment_id: str, req: RefinementRequest):
    detail = get_experiment(experiment_id, top=req.top_k)
    ranked = detail["ranked_results"]
    if not ranked:
        raise HTTPException(409, "experiment has no scored completed results to refine")

    spec = detail["spec"]
    original_parameters = spec.get("parameters", {})
    points_by_key: dict[str, dict[str, Any]] = {}
    for candidate in ranked[: req.top_k]:
        axes: dict[str, list[Any]] = {}
        for name, parameter_spec in original_parameters.items():
            center = candidate["parameters"].get(name)
            axes[name] = _refinement_axis(
                parameter_spec,
                center,
                shrink=req.shrink,
                points_per_axis=req.points_per_axis,
            )
        names = list(axes)
        for values in itertools.product(*(axes[name] for name in names)):
            point = dict(zip(names, values))
            points_by_key[repr(sorted(point.items()))] = point
            if len(points_by_key) > MAX_EXPERIMENT_UNITS:
                raise HTTPException(
                    400,
                    f"refinement produces more than {MAX_EXPERIMENT_UNITS} parameter points; "
                    "reduce top_k or points_per_axis",
                )

    objective = Objective(**spec["objective"]) if spec.get("objective") else None
    child = ExperimentRequest(
        name=f"{spec.get('name', 'experiment')} refinement {int(spec.get('generation', 0)) + 1}",
        task=spec.get("task") or detail["task"],
        parameters={name: SweepParameter(values=[ranked[0]["parameters"].get(name)]) for name in original_parameters},
        objective=objective,
        base_payload=spec.get("base_payload") or {},
        requirements=_job_requirements(experiment_id),
        priority=int(detail["priority"]) + req.priority_delta,
        replicates=int(spec.get("replicates", 1)),
        replicate_parameter=spec.get("replicate_parameter"),
        metadata={"refined_from": experiment_id},
    )
    return _create_from_points(
        child,
        list(points_by_key.values()),
        parent_id=experiment_id,
        generation=int(spec.get("generation", 0)) + 1,
        refinement={
            "top_k": req.top_k,
            "shrink": req.shrink,
            "points_per_axis": req.points_per_axis,
            "source_best_score": ranked[0]["score"],
        },
        original_parameter_spec=original_parameters,
    )
