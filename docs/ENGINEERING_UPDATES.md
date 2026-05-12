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
