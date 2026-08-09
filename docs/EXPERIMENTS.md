# Distributed experiment engine

The experiment API is the high-level interface for parameter sweeps, simulation ensembles, optimization searches, and other naturally parallel studies.

The controller expands a parameter grid into independent work units. Workers pull compatible units when they are ready, so faster CUDA/desktop nodes naturally complete more points while slower phones and Raspberry Pis take fewer. There is no static assignment that forces the entire experiment to wait on one slow device.

## Submit a sweep

`POST /experiments` uses the normal admin token.

```bash
curl -X POST http://127.0.0.1:8765/experiments \
  -H "Authorization: Bearer $SWARM_ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "name":"example parameter sweep",
    "task":"my_simulation",
    "parameters":{
      "voltage":{"values":[100,200,300,400]},
      "field_strength":{"start":0.1,"stop":1.0,"step":0.1},
      "geometry_scale":{"start":0.5,"stop":1.0,"step":0.05}
    },
    "objective":{"path":"metrics.efficiency","direction":"maximize"},
    "base_payload":{"mode":"reduced_model"},
    "requirements":{"capabilities":["cpu"]}
  }'
```

A parameter supports either explicit `values` or an inclusive numeric `start` / `stop` / `step` range. The Cartesian product is expanded into work units, up to 10,000 units per experiment. Split larger grids into stages rather than creating an unbounded queue.

The task must already be installed on workers and advertised as `task:<task-name>`. The experiment layer does not send executable code.

## Replicates / seeds

Use `replicates` when every parameter point should run multiple times. `replicate_parameter` optionally copies the zero-based replicate number into the task payload, which is useful as a deterministic seed.

```json
{
  "replicates": 10,
  "replicate_parameter": "seed"
}
```

Each unit also receives private `_swarm_experiment` metadata containing the exact parameter point and replicate number. Existing task handlers may ignore it.

## Ranking results

`GET /experiments/{experiment_id}` returns progress, worker throughput and ranked completed results.

The objective uses a dotted result path. Examples:

```json
{"path":"efficiency","direction":"maximize"}
```

```json
{"path":"metrics.loss","direction":"minimize"}
```

Array indices are supported, for example `outputs.0.score`.

The response includes `best`, `ranked_results`, and per-worker `units_done`, `avg_elapsed_ms`, and `units_per_second` measurements.

## Adaptive scheduling

The scheduler uses fine-grained adaptive pull scheduling instead of preassigning fixed chunks:

1. The controller queues independent parameter points.
2. Each compatible worker requests another unit when it becomes free.
3. Fast nodes therefore consume more units automatically.
4. Thermal-throttled phones, slow SBCs, and temporarily disconnected nodes do not become global stragglers.
5. Expired leases return to the queue and may be completed by another device.

This is especially effective for heterogeneous swarms where device speeds differ by orders of magnitude.

## Refinement pass

Once scored results exist, the controller can create a child experiment around the best regions:

```bash
curl -X POST http://127.0.0.1:8765/experiments/$EXPERIMENT_ID/refine \
  -H "Authorization: Bearer $SWARM_ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "top_k":3,
    "shrink":0.25,
    "points_per_axis":5
  }'
```

For numeric range parameters, refinement samples a smaller neighborhood around each of the top results while staying inside the original bounds. Explicit categorical/value parameters remain fixed to the winning candidate value. Duplicate points are removed.

The child response contains `parent_experiment_id` and `generation`, making coarse-to-fine searches traceable.

## List experiments

```bash
curl http://127.0.0.1:8765/experiments \
  -H "Authorization: Bearer $SWARM_ADMIN_TOKEN"
```

This returns experiment progress without mixing ordinary low-level jobs into the list.

## Recommended task result shape

Experiment tasks may return anything JSON-serializable, but a predictable result structure makes ranking and analysis easier:

```json
{
  "metrics": {
    "efficiency": 0.83,
    "loss": 0.17
  },
  "valid": true,
  "diagnostics": {}
}
```

Then use an objective such as `metrics.efficiency`.
