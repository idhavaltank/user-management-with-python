# Database migrations

Alembic is the schema-change tool for this backend. Migration files describe
the PostgreSQL tables, indexes, and constraints independently of application
startup, so every environment can be moved to the same schema version.

Run from `backend/` after setting `DATABASE_URL`:

```bash
uv run alembic upgrade head
uv run alembic downgrade base
```

The initial migration creates PostgreSQL-backed users and opaque session records. It is reversible for local and disposable environments; production downgrades require an explicit backup and release review.
