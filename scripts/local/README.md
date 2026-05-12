# Local (No Docker) Runbook

This folder provides PowerShell scripts for running the project locally on Windows without Docker.

## 1) Prepare Python envs

```powershell
./scripts/local/setup-python.ps1
```

## 2) Initialize PostgreSQL schema

```powershell
./scripts/local/init-db.ps1 -PgHost 127.0.0.1 -PgPort 5432 -PgAdminUser postgres
```

Notes:
- If `psql` is not installed, the script falls back to Python (`scripts/local/init_db.py`) automatically.
- You may need PostgreSQL admin password; pass it with `-PgAdminPassword "your-password"`.

## 3) Prepare env file

```powershell
Copy-Item .env.local.example .env.local
```

Then fill `TG_API_ID`, `TG_API_HASH`, `TG_PHONE` and proxy values if needed.

## 4) Start web (recommended first)

```powershell
./scripts/local/run-web.ps1
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

```powershell
./scripts/local/run-minio.ps1
```

Default:
### Start crawler

```powershell
./scripts/local/run-crawler.ps1
```

The first run requires Telegram login code/2FA password interaction.

## Conflict guards (single-instance)

- `run-web.ps1`, `run-crawler.ps1`, `run-minio.ps1` refuse to start if an existing same service process is detected.
- Add `-Force` to bypass the script-level guard.
- Crawler also uses PostgreSQL advisory lock at runtime, so only one crawler instance can actually run against the same DB.

## Optional: override DB or MinIO endpoints

```powershell
./scripts/local/run-web.ps1 -DatabaseUrl "postgresql://tguser:tgpwd@127.0.0.1:5432/tg_crawler"
./scripts/local/run-crawler.ps1 -S3Endpoint "http://127.0.0.1:9000"
```
