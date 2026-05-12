# TG Crawler MVP

Telegram 频道增量采集、结构化抽取、媒体对象存储（MinIO）、PostgreSQL 持久化，以及基于 FastAPI 的审核后台。支持**同一人重复发帖去重**（规则编号 + 可选大模型）。

---

## 功能概览

| 模块 | 说明 |
|------|------|
| **crawler** | Telethon 登录频道，按 `last_crawled_msg_id` 增量拉取消息；文本规则抽取；图片下载后上传 MinIO，可选中文 OCR；支持同一 `media_group_id` 图集的字段继承与历史回填。 |
| **web** | 管理端：列表筛选、详情、审核状态与审计日志；会话 Cookie + JWT；支持管理员/普通用户角色与账户管理。 |
| **postgres** | 频道、消息、规范化档案、媒体元数据、审核员与审计表（见 `init.sql`）。 |
| **minio** | 媒体文件与缩略图；默认桶策略允许匿名读对象（便于直链，生产请收紧）。 |

---

## 架构与数据流

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

## 快速开始（Docker Compose）

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

## 端口与访问

| 服务 | 端口 | 说明 |
|------|------|------|
| Web | 8080 | 管理界面 |
| PostgreSQL | **5433**（主机） | 映射到容器内 `5432`；本机工具连接 `localhost:5433`，用户名库名见 compose |
| MinIO S3 API | 9000 | 爬虫/Web 容器内使用 `http://minio:9000` |
| MinIO 控制台 | 9001 | 默认账号见 compose（生产必须修改） |

---

## 环境变量说明

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

## 本地开发（不通过 Docker 跑爬虫）

### 一键本地脚本（推荐）

仓库已提供无 Docker 的 PowerShell 脚本，见 `scripts/local/README.md`：

```bash
./scripts/local/setup-python.ps1
./scripts/local/init-db.ps1
copy .env.local.example .env.local
./scripts/local/run-web.ps1
```

然后登录 Web 后台，通过页面按钮 **一键启动采集链路**（自动拉起 MinIO + crawler）：

- 首页：`http://localhost:8080/`
- 系统控制台：`http://localhost:8080/ops`

### 手动方式

```bash
cd crawler
python -m venv .venv
.venv\Scripts\activate   # Windows
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

若你仍连接的是 Docker 中的 Postgres，请把端口改回 `5433`。

---

## 数据库初始化

首次启动 Postgres 容器时，`init.sql` 会创建表与索引。默认管理员账号由 **Web 服务首次启动** 写入（不在 SQL 中硬编码密码哈希）。

若需重置数据库，删除 Docker 卷 `pg_data` 后重新 `docker compose up`（**会清空全部数据**）。

---

## 目录结构（主要部分）

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

## 工程规范与更新记录

- Python Google 风格基线：`docs/PYTHON_GOOGLE_STYLE_BASELINE.md`
- 持续更新日志：`docs/ENGINEERING_UPDATES.md`

---

## 安全与合规提示

1. **凭据**：仓库内 compose 默认数据库、MinIO、管理员口令均为演示用途，**切勿直接用于公网**。
2. **MinIO**：默认桶策略允许匿名 `GetObject`；生产建议使用预签名 URL 或网关鉴权。
3. **法律与平台条款**：采集、存储与展示用户生成内容须遵守当地法律及 Telegram 使用条款；本仓库仅为技术示例。

---

## 常见问题

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

## 许可证

若未在仓库中单独提供许可证文件，默认以项目所有者声明为准；使用第三方库时须遵守其各自许可证。
