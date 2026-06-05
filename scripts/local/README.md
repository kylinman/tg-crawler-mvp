# Local (No Docker) Runbook

This folder provides scripts for running the project locally on **Windows**, **macOS**, and **Linux** without Docker.

## 1) Prepare Python envs

**Windows:**
```powershell
./scripts/local/setup-python.ps1
```

**macOS / Linux:**
```bash
chmod +x scripts/local/*.sh
./scripts/local/setup-python.sh
```

`setup-python.sh` 会为 `web/`、`crawler/` 和 `desktop/`（Qt 桌面界面）分别创建独立的 `.venv`。

## 2) Initialize PostgreSQL schema

**Windows:**
```powershell
./scripts/local/init-db.ps1 -PgHost 127.0.0.1 -PgPort 5432 -PgAdminUser postgres
```

**macOS / Linux:**
```bash
./scripts/local/init-db.sh
```

Notes:
- If `psql` is not installed, the script falls back to Python (`scripts/local/init_db.py`) automatically.
- You may need PostgreSQL admin password; pass it with `-PgAdminPassword "your-password"` (PowerShell) or set `PGPASSWORD` env var (Bash).

## 3) Prepare env file

**Windows:**
```powershell
Copy-Item .env.local.example .env.local
```

**macOS / Linux:**
```bash
cp .env.local.example .env.local
```

Then fill `TG_API_ID`, `TG_API_HASH`, `TG_PHONE` and proxy values if needed.

## 4) Start web (recommended first)

**Windows:**
```powershell
./scripts/local/run-web.ps1
```

**macOS / Linux:**
```bash
./scripts/local/run-web.sh
```

Open: http://localhost:8080

Web pages:
- Message list: `http://localhost:8080/`
- Person search: `http://localhost:8080/persons`
- System control: `http://localhost:8080/ops`
- User management (admin): `http://localhost:8080/users`
- My account: `http://localhost:8080/account`
- My crawler settings: `http://localhost:8080/settings`
- Crawl logs: `http://localhost:8080/crawl-logs`
- Audit logs: `http://localhost:8080/audit`

## 5) One-click start data pipeline from Web

After login, click either:
- `消息` page button: **一键启动采集链路**
- `系统控制` page button: **一键启动采集链路**

This will start MinIO and crawler from the browser, so you do not need to run extra local commands for normal operation.

## 6) Optional manual start (advanced)

### Start MinIO locally

**Windows:**
```powershell
./scripts/local/run-minio.ps1
```

**macOS / Linux:**
```bash
./scripts/local/run-minio.sh
```

Default:
- S3 API: `http://127.0.0.1:9000`
- Console: `http://127.0.0.1:9001`
- Credentials: `minioadmin / minioadmin`

### Start crawler

**Windows:**
```powershell
./scripts/local/run-crawler.ps1
```

**macOS / Linux:**
```bash
./scripts/local/run-crawler.sh
```

The first run requires Telegram login code/2FA password interaction.

### Start desktop Qt UI (experimental cross-platform GUI)

After running `setup-python.sh`:

**Windows (PowerShell):**
```powershell
. .\desktop\.venv\Scripts\Activate.ps1
python desktop/main.py
```

**macOS / Linux:**
```bash
source desktop/.venv/bin/activate
python desktop/main.py
```

(Or use uv for the desktop venv as shown in `desktop/README.md`.)

The Qt app provides a native desktop alternative to the web UI (same DB backend).

## Conflict guards (single-instance)

- All scripts refuse to start if an existing same service process is detected.
- Add `-Force` (PowerShell) or set `FORCE=1` (Bash) to bypass the script-level guard.
- Crawler also uses PostgreSQL advisory lock at runtime, so only one crawler instance can actually run against the same DB.

## Optional: override DB or MinIO endpoints

**Windows:**
```powershell
./scripts/local/run-web.ps1 -DatabaseUrl "postgresql://tguser:tgpwd@127.0.0.1:5432/tg_crawler"
./scripts/local/run-crawler.ps1 -S3Endpoint "http://127.0.0.1:9000"
```

**macOS / Linux:**
```bash
DATABASE_URL="postgresql://tguser:tgpwd@127.0.0.1:5432/tg_crawler" ./scripts/local/run-web.sh
S3_ENDPOINT="http://127.0.0.1:9000" ./scripts/local/run-crawler.sh
```
