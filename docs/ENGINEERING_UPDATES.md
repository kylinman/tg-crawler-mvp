# Engineering Updates

持续记录系统鲁棒性、代码规范化与运维体验改造。

## 2026-05-12

### 目标

- 将“本地仅启动 Web，其他服务在页面一键拉起”能力做成默认工作流。
- 按 Google Python 风格补齐关键控制逻辑的可维护性。

### 代码改造

- `web/main.py`
  - 增加系统控制鲁棒性：
    - 启停操作并发锁（避免并发点击导致竞态）。
    - 启停后轮询校验（超时后给出明确失败原因）。
    - 启动前 crawler 配置预检（`TG_API_ID/TG_API_HASH/TG_PHONE`）。
    - 启动脚本输出统一落盘到 `.local/runtime-logs/*-launcher.log`。
  - 路由增强：
    - `POST /api/system/start-all` 返回 `launch_logs` 与细化错误。
    - `POST /api/system/{service}/start` / `stop` 返回更明确状态和错误。
  - 代码规范化：
    - 新增模块/函数 docstring。
    - 关键函数补充类型标注与命名标准化。

- `web/templates/list.html`
  - 首页保留“一键启动采集链路”快捷操作。

- `web/templates/ops.html`
  - 系统控制台支持状态查看、单服务启停、一键拉起。

- `scripts/local/README.md`
  - 更新为 Web-first 流程：先启动 Web，再在页面一键启动链路。

- `README.md`
  - 增补系统控制台入口和文档索引。

### 验证

- 语法校验：`python -m py_compile web/main.py` 通过。
- 路由 smoke test：`/ops`、`/api/system/status`、`start-all` 逻辑通过。

### 风险与回滚

- 风险：若 PowerShell 或脚本路径异常，启动接口会返回 `ok=false`，并给出日志路径。
- 回滚：还原 `web/main.py` 中系统控制 helper 与 API 变更，前端入口可保持不影响主审核功能。

## 2026-05-12（消息采集关联修复）

### 目标

- 修复图集帖子（同一 `media_group_id`）里“只有一条带文案、其余纯图片”导致的人物字段断裂。
- 让单张图片消息自动继承同组人物信息，避免后台出现大量 `-/-/-` 的孤立人物行。

### 代码改造

- `crawler/db.py`
  - 增加 `has_meaningful_extracted()`，只在抽取结果包含有效人物字段时才 upsert profile。
  - `upsert_profile_from_extracted()` 返回布尔值，便于回填统计。

- `crawler/main.py`
  - 新增图集内字段继承：`_apply_media_group_extracted()`，在批量落库前把最佳抽取结果扩散到同组 media-only 消息。
  - 新增跨批次图集缓存（`media_group_cache`），避免 100 条 flush 边界导致图集字段断裂。
  - 新增批次内定向修复：每次 `_flush_batch` 后按本批 `media_group_id` 执行小范围回填，确保 profile 不漏建。
  - 新增历史回填：`_backfill_media_group_profiles()`，按 `media_group_id` 修复旧数据（补 `messages.extracted_json` 与 `profiles`）。
  - 移除“每遇到媒体立刻 flush”行为，改为批量 flush，提升同图集聚合成功率。

- `web/main.py`
  - 人物归并 key 增强：无编号时优先按 `album:<channel_id>:<media_group_id>` 归并，再回退 `msg:<id>`。
  - `/persons/group` 新增 `album:` key 解析，支持图集级别聚合详情。

### 验证

- 语法校验：
  - `python -m py_compile crawler/main.py crawler/db.py`
  - `python -m py_compile web/main.py`
- 实测回填：对当前频道执行一次图集回填，修复 `325` 条 media-group 关联 profile。
- 抽样验证：同一 `media_group_id` 内多条消息已共享相同 `internal_code`（如 `R3435`）。
- 数据指标：`group has code but member missing code` 当前检查结果为 `0`（修复窗口内）。

### 风险与回滚

- 风险：极少数图集若文案本身错误，继承后会把错误字段传播到同组消息。
- 回滚：回退 `crawler/main.py` 的图集继承与回填逻辑；`web/main.py` 归并 key 回退到 `code/msg` 二级策略。

## 2026-05-12（账户与权限基础）

### 目标

- 增加管理员与普通用户分级能力。
- 提供用户创建、禁用、重置密码能力。
- 提供用户个人采集参数配置存储（为后续 SaaS 多租户采集调度做基础）。

### 代码改造

- `web/auth.py`
  - 增加 `is_admin()`，统一管理员角色判定。
  - `get_current_user()` 扩展读取 `full_name/email/must_change_password`。

- `web/main.py`
  - 新增管理员页面：`/users`。
  - 新增个人页面：`/account`、`/settings`。
  - 新增用户管理 API：
    - `POST /api/users`
    - `POST /api/users/{id}/password`
    - `POST /api/users/{id}/status`
  - 新增个人 API：
    - `POST /api/account/password`
    - `POST /api/settings/crawler`
  - 系统控制 API 改为仅管理员可访问。
  - 启动阶段自动补齐身份与配置表结构（兼容旧库升级）。

- `web/templates`
  - 新增 `users.html`、`account.html`、`settings.html`。
  - 导航增加用户管理/账户/采集配置入口（按角色显示）。

- `init.sql`
  - reviewers 增加 `full_name/email/must_change_password/updated_at`。
  - 新增 `user_crawler_settings` 表。

### 验证

- 语法校验：`python -m py_compile web/main.py web/auth.py` 通过。
- 导航可见性验证：admin 可见用户管理与系统控制；普通用户仅见账户与配置入口。

### 风险与回滚

- 风险：当前仍是单实例 crawler，`/settings` 先作为配置存储，不等于多用户并发采集调度。
- 回滚：保留 reviewers 基础字段，回退新增路由与模板即可恢复旧登录模型。

---

## 2026-05-12（跨平台兼容）

### 目标

- 支持 Windows / macOS / Linux 三平台本地部署。
- 系统控制台（启停服务）在所有平台可用。

### 代码改造

- `scripts/local/`
  - 新增 Bash 脚本：`setup-python.sh`、`init-db.sh`、`run-web.sh`、`run-crawler.sh`、`run-minio.sh`。
  - Bash 脚本支持 `.env` / `.env.local` 自动加载。
  - 单实例检测：Linux 用 `/proc`，macOS 用 `ps` 回退。

- `web/main.py`
  - 新增 `PLATFORM_IS_WINDOWS` 全局标志，自动选择 `.ps1` 或 `.sh` 脚本。
  - 新增 `_collect_unix_process_status()`：通过 `ps -eo pid,command` 检测进程。
  - `_collect_process_status()` 统一入口，自动分流 Windows/Unix。
  - `_start_local_service_script()`：Windows 用 PowerShell detached，Unix 用 `bash` + `start_new_session`。
  - `_stop_local_service()`：拆分为 `_stop_local_service_windows()` 和 `_stop_local_service_unix()`。
  - Unix 停止：先 `SIGTERM`，超时后 `SIGKILL`。

- `README.md`
  - 增加 macOS / Linux 启动命令示例。

- `scripts/local/README.md`
  - 每个步骤同时给出 Windows 和 macOS/Linux 命令。

### 验证

- `py_compile web/main.py` 通过。
- Windows 环境下 smoke test 通过（保持原有行为不变）。

### 风险与回滚

- 风险：macOS/Linux 的 `ps` 解析可能在某些系统上行为差异。
- 回滚：回退 `web/main.py` 中 `_collect_unix_process_status` 和 `_stop_local_service_unix`，恢复 `os.name == 'nt'` 守卫。

---

## 2026-05（全局优化文档与路线图）

### 目标
- 系统梳理当前项目的全局优化机会（架构、性能、可靠性、安全、工程化）。
- 产出可执行的分阶段路线图，便于团队对齐优先级和跟踪进度。
- 保持与现有工程规范一致（Google 风格、更新日志模板、风险回滚描述）。

### 代码改造
- 新增 `docs/OPTIMIZATION_ROADMAP.md`（完整文档，含执行摘要、现状评估、10+ 优化领域详解、4 阶段路线图、实施原则、参考代码位置）。
- 在 `README.md`「工程规范与更新记录」章节增加路线图链接。
- 本条目作为首次记录追加到 ENGINEERING_UPDATES.md。

### 验证
- 文档内容覆盖此前代码审查发现的所有高影响点（重复代码、media_group 索引缺失、连接池、配置碎片、同步阻塞媒体处理、MinIO 公开策略、零测试等）。
- 文档结构与项目现有风格保持一致（中文、表格、文件行号引用、Phase 风险回滚描述）。
- `git status` 显示新增文档及 README/ENGINEERING_UPDATES 指向更新。

### 风险与回滚
- 风险：路线图内容随后续实现可能需要修订（正常）。
- 回滚：删除或重命名 OPTIMIZATION_ROADMAP.md，恢复 README 和本文件中的引用即可（低风险）。

---

## 2026-05（Phase 0 优化实施：共享模块、索引、去重、Docker 适配）

### 目标
- 启动 OPTIMIZATION_ROADMAP Phase 0：消除重复代码、添加关键索引、统一工具函数、修复 Docker 构建以支持 common/ 模块、清理残留 print/死 import、改进部分异常处理。
- 保持向后兼容（别名 + 最小调用站点变更）。
- 所有变更通过 py_compile 和基本 import 验证。

### 代码改造
- **init.sql**：新增 `idx_messages_media_group` 和 `idx_messages_channel_media_group` 索引（支持图集回填、查询）。
- **common/**（新建）：
  - `__init__.py`、`normalize.py`（normalize_code / normalize_code_key）
  - `extracted.py`（EXTRACTED_PROFILE_KEYS、has_meaningful_extracted、parse_int/parse_float/parse_bool 及别名、is_empty_value、parse_extracted_value、merge_extracted、extracted_score）
- **crawler/db.py**：删除本地重复定义，通过 from common 导入 + alias 保持兼容，移除 unused re import。
- **crawler/main.py**：调整 import；删除 3 个重复的静态方法（_parse_extracted_value、_merge_extracted、_extracted_score）；更新所有调用为直接函数调用；改进 proxy ImportError 和 disconnect/OCR 的异常处理 + logging。
- **web/main.py**：添加 common import；将 _parse_* 和 _normalize_* 替换为指向 common 的 alias（删除重复实现）；移除不再需要的 `import re`。
- **crawler/uploader.py**：修复 __import__('datetime') 为正常 import；将 thumb fail print 改为 logger.warning。
- **crawler/extractor.py**：移除未使用的 `from datetime import datetime`。
- **web/main.py**：将默认 admin print 改为 LOGGER.info。
- **docker-compose.yml + Dockerfiles**：将 build 改为 root context + 显式 dockerfile；更新 volumes/working_dir/command 使用 PYTHONPATH=/app 支持 common/ 导入；Dockerfile 内显式 COPY common + 子目录代码；调整 CMD 以 PYTHONPATH 运行。
- 同步更新了部分 except Exception 为更具体的 ImportError / 带日志的版本。

### 验证
- `python -m py_compile` 全部相关文件通过（common/*、crawler/*、web/main.py）。
- 基本 import 测试：`from common...` 成功，normalize/parse 行为正确。
- 重复代码大幅减少（db.py + main.py 净删 ~140 行逻辑）。
- Docker 配置现在支持共享模块（本地 volume .:/app + PYTHONPATH）。
- 符合 Google 风格 + 路线图规范。

### 风险与回滚
- 风险：Docker 变更可能影响某些本地 compose 工作流（已测试结构）；web 内部 _parse_* 仍通过 alias 暴露，调用站点无需立即改。
- 回滚：git revert 对应 commit；或临时把 common 逻辑复制回原位置并恢复 compose/Dockerfile 即可（低风险，Phase 0 设计时已考虑兼容）。

---

## 模板（后续追加）

```text
## YYYY-MM-DD
### 目标
- ...

### 代码改造
- ...

### 验证
- ...

### 风险与回滚
- ...
```
