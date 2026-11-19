# Migrations (history)

Schema changes live in `backend/app/db.py` → `MIGRATIONS` and are applied automatically by
`db.migrate()` (backup + one transaction). `SCHEMA_VERSION = len(MIGRATIONS)`.
Files here are the written proposals they came from.

| File | Status |
|---|---|
| `2026-11-04-embedding-core.sql` | **Applied as schema v3**: `embeddings.dimensions` + `embeddings.text_hash`; existing table and `UNIQUE(image_id, dimension)` reused, no new table. |

New migrations: append a new entry at the end of `MIGRATIONS` (never reorder, never hard-code a
version number — v3 is taken by the embedding change, so any branch that assumed "v3"
must be renumbered against the latest `MIGRATIONS` when it is merged). Keep them additive.
