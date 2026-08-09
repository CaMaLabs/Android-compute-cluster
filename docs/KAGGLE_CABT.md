# Pokémon TCG / Kaggle CABT workload

Compute Swarm can distribute local evaluation episodes for Kaggle's **The Pokémon Company - PTCG AI Battle Challenge Simulation**.

The competition uses the `cabt` environment in `kaggle-environments`. A Kaggle submission is a `.tar.gz` with `main.py` and `deck.csv` at the archive root. The swarm keeps that same bundle format.

## Controller workflow

After updating the controller, open:

```text
http://CONTROLLER:8765/pokemon
```

The page lets you:

1. upload your `submission.tar.gz`;
2. optionally upload a second submission as the opponent (blank means self-play);
3. choose the number of independent episodes;
4. alternate seats automatically;
5. watch distributed progress and W/L/D;
6. download per-episode results as CSV.

The uploaded bundles are stored in the controller's SHA-256 artifact store. Work units reference those immutable artifact IDs. Faster compatible workers naturally lease more episodes.

## Enable CABT on a Python worker

CABT execution is deliberately opt-in because a Kaggle agent bundle contains Python code.

Update the worker to a revision containing this feature, then run:

```bash
sudo bash scripts/enable-kaggle-cabt-worker.sh
```

The script installs the pinned `kaggle-environments` package, smoke-tests `make("cabt")`, enables the local `plugins.kaggle_cabt` task, and restarts the worker service.

A ready worker advertises:

```text
python
kaggle:cabt
competition:pokemon-tcg-ai-battle
task:kaggle_cabt_episode
```

If the package or CABT native engine is unsupported on a particular architecture, the smoke test fails and the task is not enabled on that machine.

## Security boundary

The controller still cannot send shell commands or arbitrary command lines. It can only schedule the named `kaggle_cabt_episode` task.

This task is intentionally allowed to execute the uploaded Kaggle agent bundle because that is the competition's workload model. For that reason it is **not enabled by default**. Only enable it on machines where you trust the agent bundles you upload.

Archive extraction rejects path traversal, links, devices, excessive file counts, and excessive expanded size.

## API

Upload an agent:

```bash
curl -X POST \
  -H "Authorization: Bearer $SWARM_ADMIN_TOKEN" \
  -H "Content-Type: application/gzip" \
  --data-binary @submission.tar.gz \
  "http://127.0.0.1:8765/artifacts?name=submission.tar.gz"
```

Create 100 episodes:

```json
POST /kaggle/cabt/matches

{
  "name": "candidate-v7 self play",
  "agent_artifact_id": "<sha256 artifact id>",
  "opponent_artifact_id": null,
  "episodes": 100,
  "alternate_seats": true,
  "run_timeout_seconds": 1200
}
```

Read results:

```text
GET /kaggle/cabt/matches
GET /kaggle/cabt/matches/{match_id}
GET /kaggle/cabt/matches/{match_id}/csv
```

## Notes

- Each episode is an independent work unit, making CABT evaluation a good fit for the heterogeneous pull scheduler.
- Android's native Kotlin worker will not advertise this task. Python workers can.
- Replays are disabled by default because thousands of replay artifacts can become large. Set `save_replays: true` through the API when needed.
- The controller enforces Kaggle's 197.7 MiB compressed submission-size limit.
