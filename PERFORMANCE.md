# Phase 4 — Performance + Ops Hardening

## Domain-Tagged Latency

Request logs include `domain=auth|trivia|store|messaging|payments|notifications` based on path prefix.

Env:
- `LATENCY_LOG_INTERVAL_SECONDS` (default `300`)
- `LATENCY_TOP_N` (default `5`)
- `LATENCY_STATS_WINDOW` (default `200`)

## Slow Request Sampling

Env:
- `SLOW_REQUEST_THRESHOLD_MS` (default `500`)
- `SLOW_REQUEST_LOG_STACK` (default `false`)

## Slow DB Query Logging + Pool Tuning

Env:
- `SLOW_DB_QUERY_THRESHOLD_MS` (default `200`)
- `DB_POOL_SIZE` (default `10`)
- `DB_MAX_OVERFLOW` (default `20`)
- `DB_POOL_TIMEOUT_SECONDS` (default `30`)
- `DB_POOL_RECYCLE_SECONDS` (default `300`)

## Caching Strategy (initial)

Safe hot-read caches (in-memory TTL via `core/cache.py`):
- `GET /store/gem-packages`: TTL 60s
- `GET /cosmetics/avatars`: TTL 60s (keyed by `skip/limit/include_urls`)
- `GET /cosmetics/frames`: TTL 60s (keyed by `skip/limit/include_urls`)
- `GET /draw/next`: already cached in trivia service (TTL via `DRAW_PRIZE_POOL_CACHE_SECONDS`)

Guideline:
- Only cache idempotent GET-like reads.
- Keep TTL short unless invalidation exists.
- Do not cache per-user sensitive responses without keying by user/account_id.

## Background Offloading Pattern

- For simple non-critical work, use FastAPI `BackgroundTasks` and keep the router thin.
- For heavier/CPU or retryable tasks, use the optional Redis worker (`workers/`).

Env:
- `USE_WORKER_QUEUE` (default `false`) to enqueue certain non-critical tasks instead of running them in-process.

Currently offloaded (when `USE_WORKER_QUEUE=true`):
- Trivia live chat push + pusher fanout (fallback path) via `push.trivia_live_chat` / `pusher.trivia_live_chat`.

## Rate Limiting Hotspots

Uses `core/rate_limit.py` (Redis preferred, in-memory fallback) for:
- Global chat send (minute + burst)
- Private chat send (minute + burst)
- DM send (minute + per-conversation burst)
- Group message send (minute + per-group burst)

