# TG Crawler Qt Desktop Admin (Cross-Platform)

PySide6-based desktop UI for the TG Crawler MVP.

## Features (current + planned)

- **Messages**: Real-time filtered table from Postgres, quick approve/reject/flag, detail view with extracted JSON.
- **Persons**: Grouped profiles search (reuses code/album logic).
- **Operations**: One-click start/stop for local MinIO + Crawler (ports the robust logic from web `ops` + scripts).
- **Settings**: DB/S3 connection, per-user crawler config.
- Reuses `common/` (normalize, extracted helpers) and same DB schema.

## Run (after main project setup)

**Recommended (one command from repo root):**

```bash
./scripts/local/setup-python.sh     # auto-creates desktop/.venv (the .sh script *prefers uv* if you have it installed)
source desktop/.venv/bin/activate
python desktop/main.py
```

(If `uv` is installed, `setup-python.sh` will use it automatically for faster venv creation + installs.)

Manual with uv only for desktop:

```bash
cd desktop
uv venv --python 3.11 .venv
source .venv/bin/activate
uv pip install -r requirements.txt
python main.py
```

The app will look for `DATABASE_URL` (or default to compose one on port 5433).
It respects root `.env` / `.env.local` (the code loads them via common patterns).

## Cross-platform notes

- Uses the same platform-aware process detection and script launching patterns as the web ops (see web/main.py `_collect_*_process_status` and `_start_local_service_script`).
- Qt handles native look & feel on Win/macOS/Linux.
- For full media preview, thumbnails are fetched via boto3 (MinIO/S3).

## Development status

This is the initial implementation as part of the global optimization + new UI work.
See `OPTIMIZATION_ROADMAP.md` and the Phase 0 commits.

Current working:
- Messages tab with live DB query, search/status filters, QTableWidget, quick review (approve/reject) that writes to messages + audit_logs.
- Reuses common/ and the same DB schema.
- Ops/Control tab prepared for service launcher (ports web logic).
- Settings and Persons tabs stubbed.

To run (after venv setup):
  source desktop/.venv/bin/activate && python desktop/main.py
  # or with uv: uv run --with-editable . python desktop/main.py (advanced)

Next (easy extensions):
- Real media thumbnails (boto3 + QPixmap)
- Full review dialog with profile edit form
- Ported service controller (start/stop using the platform scripts + ps detection from web/main.py)
- Login dialog using reviewers table
- Packaging for distributable binaries.

Next steps in roadmap style:
- Full media viewer + download
- Real service controller ported from web
- Review dialog with editable profile fields
- Login / multi-user support
- Packaging (PyInstaller / brief case for real cross-platform binaries)

## Relation to existing

- Complements (does not replace) the web UI and crawler.
- Can run at the same time as the FastAPI web.
- Shares the Postgres + MinIO backend 100%.
