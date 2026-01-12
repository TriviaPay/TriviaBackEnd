# Optional Worker

This repo includes an optional lightweight Redis-backed worker (no Celery/RQ).

Run locally:
- Start Redis
- Start API
- Start worker: `python3 -m workers.worker`

Enqueue a task from the API process:
- `from core.queue import enqueue_task`
- `enqueue_task(name="noop", payload={})`

