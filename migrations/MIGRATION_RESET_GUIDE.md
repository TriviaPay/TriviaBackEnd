# Migration Reset Guide (Squashed Alembic)

This repo has been reset to a single, squashed Alembic baseline migration:

- Active revisions: `migrations/versions/`
- Legacy (ignored) revisions: `migrations/versions_legacy/`

## Fresh / Empty Database

1. Ensure `DATABASE_URL` points to the target Postgres DB (or set it in `.env`).
2. Run: `alembic upgrade head`
3. (Optional seed data) Run: `python3 initialize_db.py`

## Existing Database (already has tables)

If your DB already contains the schema, you should **stamp** the baseline rather than re-creating tables:

1. Ensure `DATABASE_URL` points to the correct DB.
2. Run: `alembic stamp 0001_initial_schema`

From that point forward, new migrations can be created normally with `alembic revision -m "..."`.

