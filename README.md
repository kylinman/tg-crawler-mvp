# TG Crawler MVP

**English:**  
Telegram channel incremental crawling, structured extraction, media object storage (MinIO), PostgreSQL persistence, and a FastAPI-based admin review backend. Supports **deduplication of repeated posts by the same person** (rule-based numbering + optional LLM).

**中文：**  
Telegram 频道增量采集、结构化抽取、媒体对象存储（MinIO）、PostgreSQL 持久化，以及基于 FastAPI 的审核后台。支持**同一人重复发帖去重**（规则编号 + 可选大模型）。

---

## Features Overview / 功能概览

**English:**

| Module | Description |
|--------|-------------|
| **crawler** | Uses Telethon to log into channels, incrementally fetches messages by `last_crawled_msg_id`; performs rule-based text extraction; downloads images and uploads to MinIO, with optional Chinese OCR; supports field inheritance and historical backfill for `media_group_id` albums. |
| **web** | Admin interface: list filtering, details, review status and audit logs; session Cookie + JWT; supports admin/user roles and account management. |
| **postgres** | Channels, messages, normalized profiles, media metadata, reviewers and audit tables (see `init.sql`). |
| **minio** | Media files and thumbnails; default bucket policy allows anonymous object reads (for easy direct links; tighten in production). |

**中文：**

| 模块 | 说明 |
|------|------|
| **crawler** | Telethon 登录频道，按 `last_crawled_msg_id` 增量拉取消息；文本规则抽取；图片下载后上传 MinIO，可选中文 OCR；支持同一 `media_group_id` 图集的字段继承与历史回填。 |
| **web** | 管理端：列表筛选、详情、审核状态与审计日志；会话 Cookie + JWT；支持管理员/普通用户角色与账户管理。 |
| **postgres** | 频道、消息、规范化档案、媒体元数据、审核员与审计表（见 `init.sql`）。 |
| **minio** | 媒体文件与缩略图；默认桶策略允许匿名读对象（便于直链，生产请收紧）。 |

---

## Architecture and Data Flow / 架构与数据流

**English:**

```
Telegram ──► crawler ──► PostgreSQL (messages / profiles / …)
                │
                └──► MinIO (original images + thumbnails)

Browser ──► web (FastAPI) ──► PostgreSQL
                └──► Media URLs point to MinIO public addresses
```

- **Resume crawling**: `channels.last_crawled_msg_id` records progress; duplicate `telegram_message_id` will not be inserted again.
- **Deduplication**: Performed before writing to the database and downloading media (see "Same-person Deduplication" below).

**中文：**

```
Telegram ──► crawler ──► PostgreSQL (messages / profiles / …)
                │
                └──► MinIO (原图 + 缩略图)

浏览器 ──► web (FastAPI) ──► PostgreSQL
                └──► 媒体 URL 指向 MinIO 公网地址
```

- **断点续爬**：`channels.last_crawled_msg_id` 记录进度；重复 `telegram_message_id` 不会二次插入。
- **去重**：在写入数据库与下载媒体之前执行（见下文「同人去重」）。

---

## Quick Start (Docker Compose) / 快速开始（Docker Compose）

**English:**

### 1. Prepare Environment Variables

Copy the example file and fill in real values:

```bash
cp .env.example .env
```

Required items at minimum:

- `TG_API_ID`, `TG_API_HASH`: Obtain after creating an app at [my.telegram.org](https://my.telegram.org).
- `TG_PHONE`: Phone number with country code (first login requires completing verification code / two-step verification in the terminal).
- `TARGET_CHANNELS`: Channel usernames (without `@`), separated by English commas.

### 2. Start Dependencies and Web

```bash
docker compose up -d postgres minio web
```

- Admin backend: <http://localhost:8080>
- Default admin: On first start when there are no reviewers in the database, `admin` / `admin123` will be automatically created (see `web/main.py` startup logic). **Before going live, be sure to change the password and set a strong `ADMIN_SECRET`.**

Set environment variables for the `web` service in `docker-compose.yml`:

```yaml
ADMIN_SECRET: <random long string>
```

### 3. Run the Crawler (interactive login recommended in local terminal)

```bash
docker compose run --rm crawler
```

The first run will prompt for Telegram verification code; the session is saved in the Docker volume `tg_session` (mounted to `session/` inside the container).

To start only the crawler service (non-interactive scenarios require an existing session):

```bash
docker compose up crawler
```

**中文：**

### 1. 准备环境变量

复制示例文件并填写真实值：

```bash
cp .env.example .env
```

必填项至少包括：

- `TG_API_ID`、`TG_API_HASH`：在 [my.telegram.org](https://my.telegram.org) 创建应用后获得。
- `TG_PHONE`：带国家码的手机号（首次登录需在终端完成验证码 / 二步验证）。
- `TARGET_CHANNELS`：频道 username（不含 `@`），多个用英文逗号分隔。

### 2. 启动依赖与 Web

```bash
docker compose up -d postgres minio web
```

- 管理后台：<http://localhost:8080>
- 默认管理员：首次启动且数据库中无审核员时，会自动创建 `admin` / `admin123`（见 `web/main.py` 启动逻辑）。**上线前务必修改密码并设置强 `ADMIN_SECRET`。**

在 `docker-compose.yml` 中为 `web` 服务设置环境变量：

```yaml
ADMIN_SECRET: <随机长字符串>
```

### 3. 运行爬虫（需交互登录时建议本机终端）

```bash
docker compose run --rm crawler
```

首次运行会要求输入 Telegram 验证码；会话保存在 Docker 卷 `tg_session`（挂载到容器内 `session/`）。

仅启动爬虫服务（非交互场景需已有 session）：

```bash
docker compose up crawler
```

---

## Ports and Access / 端口与访问

**English:**

| Service | Port | Description |
|---------|------|-------------|
| Web | 8080 | Admin interface |
| PostgreSQL | **5433** (host) | Mapped to container `5432`; connect from host tools to `localhost:5433`, username/db name see compose |
| MinIO S3 API | 9000 | Used by crawler/web containers at `http://minio:9000` |
| MinIO Console | 9001 | Default credentials see compose (must change in production) |

**中文：**

| 服务 | 端口 | 说明 |
|------|------|------|
| Web | 8080 | 管理界面 |
| PostgreSQL | **5433**（主机） | 映射到容器内 `5432`；本机工具连接 `localhost:5433`，用户名库名见 compose |
| MinIO S3 API | 9000 | 爬虫/Web 容器内使用 `http://minio:9000` |
| MinIO 控制台 | 9001 | 默认账号见 compose（生产必须修改） |

---

## Environment Variables / 环境变量说明

**English:**

### Telegram and Crawling (crawler / local run)

| Variable | Required | Description |
|----------|----------|-------------|
| `TG_API_ID` | Yes | Telegram API ID |
| `TG_API_HASH` | Yes | Telegram API Hash |
| `TG_PHONE` | Yes | Phone number for login |
| `TG_PROXY_TYPE` / `TG_PROXY_HOST` / `TG_PROXY_PORT` | No | Configure proxy if Telegram access fails inside container (`socks5` / `socks4` / `http`) |
| `TARGET_CHANNELS` | No | Default example channels; comma-separated usernames |
| `DATABASE_URL` | Injected by Compose | PostgreSQL connection string |
| `S3_*` | Injected by Compose | MinIO endpoint, keys, bucket name |

### Same-person Deduplication (crawler)

Deduplication is executed **before writing to `messages` and downloading media**. If a match is found, the entire record is skipped (saves database and bandwidth).

| Variable | Default | Description |
|----------|---------|-------------|
| `DEDUP_LLM_ENABLED` | `false` | `true` / `1` / `yes` to enable LLM deduplication |
| `DEDUP_LLM_API_URL` | `https://api.deepseek.com/v1/chat/completions` | DeepSeek official compatible endpoint; change to the corresponding Chat Completions URL for other providers |
| `DEDUP_LLM_API_KEY` | empty | DeepSeek platform API Key (`Bearer`) |
| `DEDUP_LLM_MODEL` | `deepseek-chat` | Recommended for dedup: `deepseek-chat`; `deepseek-reasoner` is slower, generally unnecessary |
| `DEDUP_LLM_TIMEOUT_SEC` | `60` | Single request timeout (seconds) |
| `DEDUP_CANDIDATE_LIMIT` | `40` | For each new post, only compare against the **most recent N stored messages** in this channel |
| `DEDUP_MAX_TEXT_CHARS` | `1200` | Truncation length for main text sent to the model |
| `DEDUP_MAX_FIELD_JSON_CHARS` | `800` | Truncation length for extracted fields JSON sent to the model |

**Rule-based deduplication (no LLM dependency)**: If the extraction result contains a `code` (number), and the channel already has the same `extracted_json->>'code'`, it is treated as the same business record and skipped **without calling the model**.

**API contract**: `POST` JSON, compatible with OpenAI `v1/chat/completions` (DeepSeek is the same); the model response must be parseable JSON and contain the field:

```json
{"duplicate_of_db_id": 123}
```

If no duplicate, return `null`. If the returned `id` is not in the current candidate list, it will be ignored and the record will be **normally stored**.

**Failure policy**: On request failure, timeout, or unparseable response, **log a warning and store normally** to avoid interrupting collection.

### Admin Backend (web)

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | Shared with the crawler |
| `S3_*` | Consistent with MinIO when reading media metadata / constructing external links |
| `ADMIN_SECRET` | JWT signing secret, **must be changed in production** |

**中文：**

### Telegram 与采集（crawler / 本机运行）

| 变量 | 必填 | 说明 |
|------|------|------|
| `TG_API_ID` | 是 | Telegram API ID |
| `TG_API_HASH` | 是 | Telegram API Hash |
| `TG_PHONE` | 是 | 登录用手机号 |
| `TG_PROXY_TYPE` / `TG_PROXY_HOST` / `TG_PROXY_PORT` | 否 | 容器内 Telegram 访问失败时可配置代理（`socks5` / `socks4` / `http`） |
| `TARGET_CHANNELS` | 否 | 默认示例频道；逗号分隔多个 username |
| `DATABASE_URL` | Compose 已注入 | PostgreSQL 连接串 |
| `S3_*` | Compose 已注入 | MinIO 端点、密钥、桶名 |

### 同人去重（crawler）

去重在**写入 `messages` 与下载媒体之前**执行，命中则整条跳过（省库、省流量）。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DEDUP_LLM_ENABLED` | `false` | `true` / `1` / `yes` 开启大模型去重 |
| `DEDUP_LLM_API_URL` | `https://api.deepseek.com/v1/chat/completions` | DeepSeek 官方兼容接口；其它厂商请改成对应 Chat Completions URL |
| `DEDUP_LLM_API_KEY` | 空 | DeepSeek 平台创建的 API Key（`Bearer`） |
| `DEDUP_LLM_MODEL` | `deepseek-chat` | 去重建议用 `deepseek-chat`；`deepseek-reasoner` 更慢、一般不必 |
| `DEDUP_LLM_TIMEOUT_SEC` | `60` | 单次请求超时（秒） |
| `DEDUP_CANDIDATE_LIMIT` | `40` | 每个新帖只与**本频道最近 N 条已入库**消息比对 |
| `DEDUP_MAX_TEXT_CHARS` | `1200` | 送入模型的正文截断长度 |
| `DEDUP_MAX_FIELD_JSON_CHARS` | `800` | 送入模型的抽取字段 JSON 截断长度 |

**规则去重（不依赖 LLM）**：若抽取结果中存在 `code`（编号），且本频道已存在相同 `extracted_json->>'code'`，则直接视为同一条业务记录，**不调用模型**也会跳过。

**API 约定**：`POST` JSON，与 OpenAI 兼容的 `v1/chat/completions` 一致（DeepSeek 与此一致）；模型回复须为可解析 JSON，且包含字段：

```json
{"duplicate_of_db_id": 123}
```

无重复则为 `null`。若返回的 `id` 不在当次候选列表中，会被忽略并**正常入库**。

**故障策略**：请求失败、超时或响应无法解析时，**记录警告并照常入库**，避免采集中断。

### 管理后台（web）

| 变量 | 说明 |
|------|------|
| `DATABASE_URL` | 与爬虫共用同一库 |
| `S3_*` | 读取媒体元数据 / 拼接外链时与 MinIO 一致 |
| `ADMIN_SECRET` | JWT 签名密钥，**生产环境必须更换** |

---

## Local Development (without Docker for crawler) / 本地开发（不通过 Docker 跑爬虫）

**English:**

### One-click Local Scripts (recommended)

The repository provides Docker-free scripts supporting Windows / macOS / Linux. See `scripts/local/README.md`:

**Windows (PowerShell):**
```powershell
./scripts/local/setup-python.ps1
./scripts/local/init-db.ps1
Copy-Item .env.local.example .env.local
./scripts/local/run-web.ps1
```

**macOS / Linux (Bash):**
```bash
chmod +x scripts/local/*.sh
./scripts/local/setup-python.sh
./scripts/local/init-db.sh
cp .env.local.example .env.local
./scripts/local/run-web.sh
```

`setup-python.sh` will now also create a `.venv` for `desktop/` (for the Qt cross-platform desktop interface).

Then log into the web backend and use the page button **One-click start data pipeline** (automatically launches MinIO + crawler):

- Home: `http://localhost:8080/`
- System Console: `http://localhost:8080/ops`

### Manual Method

```bash
cd crawler
python3 -m venv .venv
source .venv/bin/activate    # macOS/Linux
# .venv\Scripts\activate     # Windows
pip install -r requirements.txt
# After configuring environment variables
python main.py
```

Web:

```bash
cd web
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
set DATABASE_URL=postgresql://tguser:tgpwd@localhost:5432/tg_crawler
uvicorn main:app --reload --port 8080
```

Desktop (Qt interface, experimental):

```bash
cd desktop
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

(Recommended to use `uv` for speed: `uv venv && uv pip install -r requirements.txt`)

If you are still connecting to Postgres inside Docker, change the port back to `5433`.

**中文：**

### 一键本地脚本（推荐）

仓库已提供无 Docker 的脚本，支持 Windows / macOS / Linux，见 `scripts/local/README.md`：

**Windows（PowerShell）：**
```powershell
./scripts/local/setup-python.ps1
./scripts/local/init-db.ps1
Copy-Item .env.local.example .env.local
./scripts/local/run-web.ps1
```

**macOS / Linux（Bash）：**
```bash
chmod +x scripts/local/*.sh
./scripts/local/setup-python.sh
./scripts/local/init-db.sh
cp .env.local.example .env.local
./scripts/local/run-web.sh
```

`setup-python.sh` 现在也会为 `desktop/` 创建 `.venv`（用于 Qt 跨平台桌面界面）。

然后登录 Web 后台，通过页面按钮 **一键启动采集链路**（自动拉起 MinIO + crawler）：

- 首页：`http://localhost:8080/`
- 系统控制台：`http://localhost:8080/ops`

### 手动方式

```bash
cd crawler
python3 -m venv .venv
source .venv/bin/activate    # macOS/Linux
# .venv\Scripts\activate     # Windows
pip install -r requirements.txt
# 配置环境变量后
python main.py
```

Web：

```bash
cd web
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
set DATABASE_URL=postgresql://tguser:tgpwd@localhost:5432/tg_crawler
uvicorn main:app --reload --port 8080
```

Desktop (Qt 界面，实验性)：

```bash
cd desktop
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

（推荐使用 `uv` 更快：`uv venv && uv pip install -r requirements.txt`）

若你仍连接的是 Docker 中的 Postgres，请把端口改回 `5433`。

---

## Database Initialization / 数据库初始化

**English:**

On the first start of the Postgres container, `init.sql` creates the tables and indexes. The default admin account is written by the **Web service on first start** (password hash is not hardcoded in SQL).

To reset the database, delete the Docker volume `pg_data` and run `docker compose up` again (**all data will be cleared**).

**中文：**

首次启动 Postgres 容器时，`init.sql` 会创建表与索引。默认管理员账号由 **Web 服务首次启动** 写入（不在 SQL 中硬编码密码哈希）。

若需重置数据库，删除 Docker 卷 `pg_data` 后重新 `docker compose up`（**会清空全部数据**）。

---

## Directory Structure (main parts) / 目录结构（主要部分）

**English:**

```
├── crawler/           # Crawling and extraction
│   ├── main.py        # Incremental crawling entry
│   ├── db.py          # Database access
│   ├── extractor.py   # Rule-based extraction
│   ├── uploader.py    # MinIO upload
│   └── dedupe_llm.py  # Numbering + LLM deduplication
├── web/               # FastAPI admin backend
│   ├── main.py
│   ├── auth.py
│   └── templates/
├── init.sql           # Table structure
├── docker-compose.yml
└── .env.example
```

**中文：**

```
├── crawler/           # 采集与抽取
│   ├── main.py        # 增量爬取入口
│   ├── db.py          # 数据库访问
│   ├── extractor.py   # 规则抽取
│   ├── uploader.py    # MinIO 上传
│   └── dedupe_llm.py  # 编号 + LLM 去重
├── web/               # FastAPI 管理端
│   ├── main.py
│   ├── auth.py
│   └── templates/
├── init.sql           # 表结构
├── docker-compose.yml
└── .env.example
```

---

## Engineering Standards and Update Log / 工程规范与更新记录

**English:**

- Python Google Style baseline: `docs/PYTHON_GOOGLE_STYLE_BASELINE.md`
- Continuous update log: `docs/ENGINEERING_UPDATES.md`
- Global optimization plan and roadmap: `docs/OPTIMIZATION_ROADMAP.md` (includes phased deliverables, verification criteria, and risk rollback)
- Qt cross-platform desktop interface (experimental): `desktop/` (PySide6, reuses common/ + same DB, supports message list, quick review, Ops control and other core features)

**中文：**

- Python Google 风格基线：`docs/PYTHON_GOOGLE_STYLE_BASELINE.md`
- 持续更新日志：`docs/ENGINEERING_UPDATES.md`
- 全局优化计划与路线图：`docs/OPTIMIZATION_ROADMAP.md`（含分阶段交付、验证标准与风险回滚）
- Qt 跨平台桌面界面（实验性）：`desktop/` （PySide6，复用 common/ + 同一 DB，支持消息列表、快速审核、Ops 控制等核心功能）

---

## Security and Compliance Notes / 安全与合规提示

**English:**

1. **Credentials**: The default database, MinIO, and admin passwords in the compose file are for demonstration purposes only. **Do not use them directly on the public internet.**
2. **MinIO**: The default bucket policy allows anonymous `GetObject`; production recommends using pre-signed URLs or gateway authentication.
3. **Legal and platform terms**: Collecting, storing, and displaying user-generated content must comply with local laws and Telegram's terms of use; this repository is for technical demonstration only.

**中文：**

1. **凭据**：仓库内 compose 默认数据库、MinIO、管理员口令均为演示用途，**切勿直接用于公网**。
2. **MinIO**：默认桶策略允许匿名 `GetObject`；生产建议使用预签名 URL 或网关鉴权。
3. **法律与平台条款**：采集、存储与展示用户生成内容须遵守当地法律及 Telegram 使用条款；本仓库仅为技术示例。

---

## FAQ / 常见问题

**English:**

**Image pull stuck at `auth.docker.io` / `docker.io` timeout**  
`docker-compose.yml` has been changed to pull from **AWS Public ECR**: `python:3.11-slim` base images for `postgres`, `web`/`crawler`, and **Bitnami MinIO** (`public.ecr.aws/bitnami/minio`). Run first:

```bash
docker compose pull
docker compose build --no-cache web
docker compose up -d postgres minio web
```

If you previously used the official MinIO image, the volume path changed from `/data` to `/bitnami/minio/data`; old bucket data will not migrate automatically. For development, you can delete volumes and start over: `docker compose down -v` (will clear MinIO and Postgres volumes).

**Crawler keeps asking for verification code**  
Session files need to be persisted: Compose already uses the named volume `tg_session`; if you switch to a local directory mount, ensure `session/` is writable and not accidentally deleted.

**Host can access Telegram, but container cannot**  
Docker containers do not automatically inherit the system's "global proxy". Set `TG_PROXY_TYPE/TG_PROXY_HOST/TG_PROXY_PORT` (and optionally username/password) in `.env`, then run `docker compose run --rm crawler`.

**Media cannot be opened in browser**  
Check if `S3_PUBLIC_ENDPOINT` is an address accessible from the host (inside container it is `http://minio:9000`; browser needs `http://localhost:9000` etc.).

**Deduplication too strict or too loose**  
- Too strict: Lower `DEDUP_CANDIDATE_LIMIT` or disable `DEDUP_LLM_ENABLED`, keep only numbering rules.  
- Too loose: Appropriately increase `DEDUP_CANDIDATE_LIMIT`; long-term solution can use vector retrieval on historical messages before handing to the model.

**Web cannot connect to database**  
Confirm `DATABASE_URL`: inside container the service name is `postgres`, port `5432`; if running crawler/web on host connecting to Compose DB, use `localhost:5433`.

**`dockerDesktopLinuxEngine` / `cannot find the file specified`**  
Docker Desktop not started or engine crashed: **Completely exit and reopen Docker Desktop**, wait for the tray icon to be ready before running `docker compose`.

**Still prompts binding `0.0.0.0:5432` failure**  
This means the `docker-compose.yml` in the current directory is an old version (or you are executing the command in another clone). Confirm in the root of this repository that the Postgres port is **`5433:5432`**, or keep consistent with the project directory opened in Cursor.

**中文：**

**拉镜像卡在 `auth.docker.io` / `docker.io` 超时**  
`docker-compose.yml` 已改为从 **AWS Public ECR** 拉取：`postgres`、`web`/`crawler` 的基础镜像 `python:3.11-slim`，以及 **Bitnami MinIO**（`public.ecr.aws/bitnami/minio`）。请先执行：

```bash
docker compose pull
docker compose build --no-cache web
docker compose up -d postgres minio web
```

若曾用过官方 MinIO 镜像，卷路径从 `/data` 改为 `/bitnami/minio/data`，旧桶数据不会自动迁移；开发环境可删卷重来：`docker compose down -v`（会清空 MinIO 与 Postgres 卷）。

**爬虫一直要验证码**  
会话文件需持久化：Compose 已使用命名卷 `tg_session`；若改成本地目录挂载，请保证 `session/` 可写且不被误删。

**宿主机能上 Telegram，但容器里连不上**  
Docker 容器不会自动继承系统“全局代理”。请在 `.env` 里设置 `TG_PROXY_TYPE/TG_PROXY_HOST/TG_PROXY_PORT`（以及可选用户名密码），再执行 `docker compose run --rm crawler`。

**媒体无法在浏览器打开**  
检查 `S3_PUBLIC_ENDPOINT` 是否为主机能访问的地址（容器内为 `http://minio:9000`，浏览器需 `http://localhost:9000` 等）。

**去重过严或过松**  
- 过严：调低 `DEDUP_CANDIDATE_LIMIT` 或关闭 `DEDUP_LLM_ENABLED`，仅保留编号规则。  
- 过松：适当增大 `DEDUP_CANDIDATE_LIMIT`；长期方案可对历史消息做向量检索再交给模型判断。

**Web 无法连接数据库**  
确认 `DATABASE_URL`：容器内服务名为 `postgres`、端口 `5432`；若在宿主机跑爬虫/Web 连 Compose 里的库，用 `localhost:5433`。

**`dockerDesktopLinuxEngine` / `cannot find the file specified`**  
Docker Desktop 未启动或引擎崩溃：**完全退出并重新打开 Docker Desktop**，等待托盘图标就绪后再执行 `docker compose`。

**仍提示绑定 `0.0.0.0:5432` 失败**  
说明当前目录下的 `docker-compose.yml` 仍是旧版（或你在另一份克隆里执行命令）。请在本仓库根目录确认 Postgres 端口为 **`5433:5432`**，或与 Cursor 打开的项目目录保持一致。

---

## License / 许可证

**English:**  
Unless a separate license file is provided in the repository, it defaults to the project owner's declaration; third-party libraries must comply with their respective licenses.

**中文：**  
若未在仓库中单独提供许可证文件，默认以项目所有者声明为准；使用第三方库时须遵守其各自许可证。