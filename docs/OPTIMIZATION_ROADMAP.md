# TG Crawler MVP 全局优化文档与路线图

**文档版本**: v1.0  
**日期**: 2026-05  
**状态**: 计划中  
**维护者**: 项目核心开发者  

**关联文档**:
- [README.md](../README.md)
- [ENGINEERING_UPDATES.md](ENGINEERING_UPDATES.md)（每次改造后必须追加）
- [PYTHON_GOOGLE_STYLE_BASELINE.md](PYTHON_GOOGLE_STYLE_BASELINE.md)

---

## 1. 执行摘要

TG Crawler MVP 已完成从“能跑”到“较鲁棒”的演进（2026-05 系列改造包括：图集字段继承与回填、owner_user_id 多租户隔离、Web 控制台一键启停、跨平台脚本、advisory lock 等）。但随着数据量增长、用户增多和生产部署需求，**全局性技术债**开始制约可维护性、性能和可靠性。

### 核心问题域
- **重复代码与边界模糊**：normalize、extracted 解析、has_meaningful_extracted 等逻辑在 `crawler/db.py`、`web/main.py` 多处重复。
- **数据库层原始**：单连接、无池、关键 `media_group_id` 索引缺失、schema 靠启动时 ALTER。
- **配置碎片**：crawler/config.py + web 直接 getenv + scripts 各自加载 + DB user_crawler_settings，来源混乱。
- **性能瓶颈**：crawler 事件循环被同步媒体下载 + OCR + 上传阻塞；LLM 去重每次独立调用。
- **工程化缺失**：零测试、无 CI lint、无结构化日志、无迁移工具。
- **生产安全默认值**：MinIO 公开桶策略、ADMIN_SECRET 弱默认、缺少强制检查。

### 优化目标（可量化）
- 代码重复减少 30%+（提取 shared/common 后）
- 支持 10 倍数据规模（索引 + 池 + 异步化后，查询 < 200ms，crawl 吞吐提升）
- 生产就绪度：测试覆盖率 > 40%（核心模块）、关键路径有 graceful shutdown
- 安全：默认不再开放匿名读，启动强制强密钥检查
- 工程体验：`make lint && make test` 一键通过，新增功能遵循 Google 风格 + 本文档规范

本文档给出**分阶段、可执行的路线图**，每阶段均遵循项目现有规范（更新 ENGINEERING_UPDATES.md、py_compile 校验、风险回滚描述）。

---

## 2. 现状评估

### 2.1 值得保持的良好实践
- `CRAWLER_LOCK_KEY` + `pg_try_advisory_lock`（crawler/main.py:133）
- 复杂的 `_apply_media_group_extracted` + `_backfill_media_group_profiles`（crawler/main.py:233, 347）
- 全表 `owner_user_id` 隔离 + `_append_message_scope` / `_ensure_message_access`
- Web 平台分支进程探测与启停（web/main.py:371, 484）
- LLM 去重失败“记录警告 + 照常入库”的鲁棒策略（dedupe_llm.py:164）
- Google Python 风格基线 + 提交前 checklist

### 2.2 痛点速览（按影响面排序）

| 领域           | 典型位置                          | 问题描述                                      | 影响级别 |
|----------------|-----------------------------------|-----------------------------------------------|----------|
| 模块重复       | crawler/db.py, web/main.py        | _normalize_code、has_meaningful_extracted 等重复 | P0      |
| DB 索引        | init.sql                          | 缺少 media_group_id 索引                      | P0      |
| 连接管理       | crawler/db.py:99, web/main.py     | 单长连接 + 每请求新建，无池、无重连           | P0      |
| 配置统一       | config.py + getenv + scripts      | 4 套加载逻辑，user settings 覆盖不透明        | P1      |
| 同步阻塞       | crawler/main.py:652 (_handle_media) | download + pytesseract + Pillow + boto3 阻塞事件循环 | P1      |
| 安全默认       | docker-compose.yml, uploader.py:26 | 公开桶策略、弱 ADMIN_SECRET                   | P1      |
| 工程化         | 全项目                            | 无测试、无 ruff/black CI、无 lockfile         | P1      |
| 日志与异常     | 多处 except Exception + print     | 不一致格式、残留 print、宽泛捕获              | P2      |
| Schema 演进    | web/main.py:2001 (_ensure_identity_schema) | 运行时 ALTER + 回填，难以审计和回滚         | P2      |
| 可观测性       | 仅 crawl_logs + audit_logs        | 无结构化日志、无指标、无链路追踪              | P2      |

---

## 3. 优化领域详解

### 3.1 共享模块与代码复用（P0）
**问题**：
- `crawler/db.py:65` `_normalize_code`
- `web/main.py:95` `_normalize_code` + `_normalize_code_key`
- `db.py:86` `has_meaningful_extracted` + `EXTRACTED_PROFILE_KEYS`
- 解析 extracted、bool/number/money 的辅助函数散落

**建议**：
1. 新建 `common/` 目录（或 `src/common/`），包含：
   - `common/normalize.py`
   - `common/extracted.py`（has_meaningful_extracted、_extracted_score、_merge_extracted 等）
   - `common/constants.py`（EXTRACTED_PROFILE_KEYS）
2. crawler 和 web 都 `from common.xxx import ...`
3. scripts/local 里需要时可直接 import（或复制薄 wrapper）

**验收**：grep 全局只剩 1 处定义；py_compile 通过；单元测试覆盖 normalize。

### 3.2 数据库访问层强化（P0）
**问题**：
- `init.sql` 无 `media_group_id` 索引，但 crawler/main.py:360、396 大量 GROUP BY / WHERE + web persons 页面。
- `Database` 类持有一个连接（crawler/db.py:99）
- web 依赖 `db_util.db_execute` + 每次 `psycopg2.connect`
- schema 变更靠启动时 ALTER（web/main.py:2001）

**建议**：
1. 立即在 `init.sql` 增加：
   ```sql
   CREATE INDEX IF NOT EXISTS idx_messages_media_group ON messages (channel_id, media_group_id);
   CREATE INDEX IF NOT EXISTS idx_messages_channel_group ON messages (channel_id, media_group_id, telegram_date DESC);
   ```
2. 引入连接池：
   - crawler：`psycopg2.pool.ThreadedConnectionPool` 或 `SimpleConnectionPool`，Database 类改成从池取/还。
   - web：推荐升级到 `psycopg[pool]` 或保持 per-request 但加 `pool_pre_ping`。
3. 增加重连逻辑（OperationalError 时重建连接）。
4. 引入 Alembic（推荐）或轻量 migration 脚本，逐步把 `_ensure_identity_schema` 和 owner backfill 迁移出去。

**验收**：`EXPLAIN ANALYZE` 显示索引使用；长时运行不丢连接；新增表/列走 migration。

### 3.3 配置与环境管理统一（P1）
**问题**：
- `crawler/config.py` 用 dotenv 加载 `.env` / `../.env`
- web 直接 `os.getenv` + `_load_env_file` + `_effective_env_map`
- scripts/local/*.sh 各自 source env
- Docker Compose 环境变量 + user_crawler_settings 运行时覆盖

**建议**：
1. 引入 `pydantic-settings`（v2），定义 `Settings` 类，区分：
   - `BaseSettings`（env）
   - `CrawlerSettings`（可被 user_crawler_settings 覆盖）
   - `WebSettings`
2. 提供 `common/config.py` 作为唯一入口。
3. 启动时打印“生效配置来源”（不打印密钥）。
4. 保留对现有 `.env` / `.env.local` / 环境变量的兼容。

**验收**：单处定义所有配置；启动日志显示来源；CRAWLER_OWNER_USER_ID 模式下 DB 配置优先。

### 3.4 性能与异步化（P1）
**关键热点**：
- `crawler/main.py:652` `_handle_media`：同步下载 + OCR + 缩略图 + 上传 + 删除。
- 每条消息都可能触发 LLM（dedupe_llm.py）。
- 批处理 flush 固定 100 条（main.py:614），无动态 backpressure。

**建议**：
1. 用 `asyncio.to_thread` / `concurrent.futures.ThreadPoolExecutor` 包装媒体 pipeline。
2. OCR 做成可选 + 后台任务（或完全移出主流程）。
3. 考虑把 `uploader.py` 改成异步（aioboto3）或保持线程池。
4. LLM 去重增加：
   - 短路（code 规则已命中）
   - 候选时间窗口衰减
   - 可选本地 embedding 预筛（pgvector）

**验收**：单条消息处理耗时日志；1000 条媒体 crawl 不阻塞；CPU 使用率下降。

### 3.5 安全性与默认值加固（P1）
**问题**：
- `docker-compose.yml` MinIO 桶策略允许 `*` GetObject（uploader.py:26 自动创建）。
- `ADMIN_SECRET=change-me-in-production`
- 密码策略仅 >=6 位
- 无 CSRF、rate limit

**建议**：
1. MinIO：
   - 默认**不**设置公开策略（提供 `S3_PUBLIC_READ=false` 开关）。
   - 新增 `common/s3.py` 预签名 URL 生成器（Web 列表/详情使用预签名而非公开直链）。
2. 启动时强制检查：
   - `ADMIN_SECRET` 长度 >=32 且非默认
   - 生产环境拒绝弱数据库密码
3. Web：
   - 表单增加 CSRF（FastAPI `Depends` + token）
   - 引入 `slowapi` 做 rate limit（登录、review API）
   - 密码策略升级（长度 + 复杂度）

**验收**：新部署 MinIO 桶无匿名策略；启动报错提示改 ADMIN_SECRET；预签名 URL 工作正常。

### 3.6 工程化与可测试性（P1）
**当前**：无 `tests/`、无 pytest、requirements 无 lock、无 lint 配置。

**建议**（按顺序）：
1. `pyproject.toml` 引入：
   - `[tool.ruff]`, `[tool.black]`
   - `pytest`, `pytest-asyncio`, `pytest-mock`
2. 新建 `tests/`：
   - `tests/test_extractor.py`（LooseExtractor 规则）
   - `tests/test_normalize.py`
   - `tests/test_dedupe.py`（mock httpx）
   - `tests/test_db_helpers.py`
3. 添加 Makefile 或 `scripts/dev.sh`：
   ```bash
   make lint
   make test
   make typecheck   # 可选 mypy
   ```
4. GitHub Actions（`.github/workflows/ci.yml`）：py_compile + lint + test + docker build。
5. 使用 `pip-tools` 或 `uv` 生成 `requirements.lock` / `crawler/requirements.lock`。

**验收**：CI 绿；新增功能必须有对应测试；`python -m pytest --cov=common,crawler,web` 报告。

### 3.7 日志、异常、资源管理（P2）
- 统一 logging 配置（crawler 用 basicConfig，web 用 logging.getLogger）。
- 所有 logger 调用统一使用 `%s` + `extra` 或 `logging.Formatter` 带 context（request_id / channel / msg_id）。
- 替换所有 `print`（uploader.py:64、web/main.py:2098、scripts）。
- 清理 extractor.py:4 未使用的 `datetime` import。
- crawler 增加 `signal` 处理 SIGTERM/SIGINT：flush batch、release lock、close client。
- 媒体临时文件统一用 `tempfile.TemporaryDirectory` context。
- uploader.py:41 去掉 `__import__('datetime')`，改成顶层 `from datetime import datetime`。

### 3.8 可观测性与运维（P2）
- 丰富 `crawl_logs`（增加阶段耗时、LLM 调用次数/耗时/成本估算）。
- Web 增加 `/metrics`（Prometheus 格式）：消息总量、待审核数、最近 crawl 成功率、活跃用户数。
- 结构化日志（json 格式，可选 structlog）。
- 长期：分布式链路追踪（OpenTelemetry）。

### 3.9 部署与容器加固（P2）
- docker-compose.yml 增加：
  - 资源限制（deploy.resources）
  - healthcheck（crawler 可通过 HTTP 暴露简易 /health 或检查 DB lock）
  - restart: unless-stopped
- Dockerfile：
  - 多阶段构建（builder + runtime）
  - 非 root 用户
  - 清理 apt 缓存更彻底
- 支持 `.env.production` 模板 + 启动校验脚本。

---

## 4. 路线图（Roadmap）

### Phase 0：基础卫生与快速止血（P0，预计 1-3 天）
**目标**：不引入新依赖，消除最明显的阻塞点和重复。

**交付物**：
- `init.sql` 补 `media_group_id` 相关索引
- 新建 `common/normalize.py` + `common/extracted.py`，crawler/web 迁移使用
- 清理所有 `print()` + 死 import
- 替换 5 处以上宽泛 `except Exception` 为更具体异常 + 日志
- 在 `docs/ENGINEERING_UPDATES.md` 追加本阶段记录

**验证**：
- `python -m py_compile crawler/main.py web/main.py`
- 对有 media_group 的频道执行一次 backfill，确认走索引（EXPLAIN）
- grep -r "print(" --include="*.py" | wc -l == 0（排除测试）

**风险与回滚**：索引添加安全；common 迁移用别名兼容 1 周后删除旧函数。

### Phase 1：基础设施与安全加固（P0-P1，预计 1-2 周）
**目标**：配置统一、连接池、默认安全收紧。

**交付物**：
- `pydantic-settings` 统一配置（`common/settings.py`）
- crawler Database 改用连接池 + 重连
- MinIO 默认无公开策略 + `common/s3.py` 预签名 URL 支持（Web 逐步切换）
- 启动时 ADMIN_SECRET / 关键密钥强度检查
- 更新所有 Dockerfile / compose 文档
- 至少 1 个集成测试验证配置加载

**验证**：
- 本地 + Docker 两种方式启动，日志显示“配置来源”
- 新建 MinIO 桶策略为 private
- 弱 ADMIN_SECRET 启动直接报错

**风险与回滚**：预签名 URL 影响外链访问，需同步更新 README 和前端模板中的直链假设。

### Phase 2：性能、测试与可靠性（P1，预计 2-4 周）
**目标**：核心路径异步化 + 测试骨架 + 优雅退出。

**交付物**：
- `crawler/main.py` 媒体处理 + OCR 上 ThreadPoolExecutor
- `tests/` 目录 + pytest 配置，覆盖 extractor、normalize、dedupe（mock）、db 辅助函数
- Makefile / scripts：`make test`, `make lint`
- crawler 信号处理 + 资源清理
- 统一日志配置模块
- `.github/workflows/ci.yml`（至少 lint + test + py_compile）

**验证**：
- 模拟 500 条带图消息 crawl，事件循环不被长时间阻塞（可加日志计时）
- `pytest tests/ -q --cov` 通过，覆盖率报告
- `docker compose down` 后 crawler 优雅释放 lock，无 stale running 日志
- CI pipeline 在 PR 上绿

**风险与回滚**：异步化改动范围大，先在 feature 分支做，保留同步路径开关 1 周。

### Phase 3：工程化成熟与架构演进（P2，预计 1-3 月）
**目标**：可维护的生产级系统，支持后续 SaaS 功能。

**交付物**：
- Alembic 迁移（逐步替代 runtime ALTER 和 owner backfill）
- 完整 ruff + black + isort 配置 + pre-commit hooks
- 结构化日志 + 可选 Prometheus `/metrics`
- 可选：引入 RQ/Redis，把媒体 pipeline 和 LLM 做成独立 worker
- 可选：pgvector 表 + 快速向量候选筛选（LLM 只做终判）
- 多实例 crawler 准备（当前 advisory lock 是单实例设计，未来可做 per-channel 锁或调度器）

**验证**：
- `alembic upgrade head` 能干净应用所有变更
- 新增功能必须带测试 + 通过 lint
- 能观测到 LLM 调用次数、平均延迟、媒体处理队列长度
- 文档更新完整（本路线图状态改为“进行中/已完成”）

---

## 5. 实施原则与规范

1. **每阶段必须更新 ENGINEERING_UPDATES.md**：
   - 目标
   - 代码改造清单（文件 + 关键函数）
   - 验证结果（py_compile、smoke、性能数字）
   - 风险与回滚策略

2. **代码规范**：
   - 严格遵循 `PYTHON_GOOGLE_STYLE_BASELINE.md`
   - 新模块/关键函数必须有 Google 风格 docstring（Args/Returns/Raises）
   - 类型标注优先（外部接口必须）

3. **变更节奏**：
   - Phase 0/1 建议小 PR（每个 PR 聚焦 1-2 个点，便于 review）
   - Phase 2 建议 feature branch + 完整测试后再合
   - 重大架构变更（Alembic、worker）先写设计小文档放 `docs/design/`

4. **回滚友好**：
   - 所有 DB 变更用 `IF NOT EXISTS` 或 migration 可逆
   - 配置新增项必须有默认值
   - 功能开关（`FEATURE_ASYNC_MEDIA=true`）用于高风险改造

5. **度量与验收**：
   - 性能：crawl 1000 条消息耗时、list 页面 p95 查询时间
   - 质量：lint 错误数、测试覆盖率、stale crawl_logs 数量
   - 安全：公开对象数量（应为 0）、弱密钥启动失败率

---

## 6. 附录

### 6.1 推荐技术栈增量
- 配置：pydantic-settings
- DB 池：psycopg2.pool / psycopg[pool]
- 测试：pytest + pytest-mock + pytest-asyncio
- Lint：ruff + black
- 迁移：alembic
- 异步媒体（可选）：aioboto3 + anyio
- 队列（可选）：rq + redis
- 向量（可选）：pgvector

### 6.2 参考代码位置（高频修改区）
- 配置相关：`crawler/config.py`, `web/main.py:230` (`_load_env_file`), scripts/local/*.sh
- DB 相关：`crawler/db.py`, `web/db_util.py`, `init.sql`
- 媒体流水线：`crawler/main.py:652` (`_handle_media`), `uploader.py`
- 去重：`crawler/dedupe_llm.py`
- 进程控制：`web/main.py:333` (`_collect_unix_process_status` 等)
- Schema 修补：`web/main.py:2001` (`_ensure_identity_schema`)

### 6.3 后续演进方向（超出本路线图范围）
- 多 crawler 实例 + 中心调度（基于 user_crawler_settings）
- 实时 webhook / 增量推送
- 更强的抽取（LLM 抽取替代规则 + 人工纠错闭环）
- 数据导出 / API 开放

---

**文档结束**

> 下一步行动：挑选 Phase 0 的 1-2 项立即开始实施，并在完成后按规范更新 `ENGINEERING_UPDATES.md`。
>
> 如需针对某个 Phase 生成详细 PR 计划或直接开始实现，请提出具体需求。

---

*本路线图需随每次重大改造同步修订，保持真实可执行。*