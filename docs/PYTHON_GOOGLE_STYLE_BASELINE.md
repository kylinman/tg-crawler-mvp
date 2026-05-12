# Python Google Style Baseline

本项目采用 Google Python Style Guide 的核心约束，并结合当前 FastAPI + 爬虫工程做落地。

## 1) 命名与可读性

- 变量/函数使用 `snake_case`。
- 常量使用 `UPPER_SNAKE_CASE`。
- 函数名使用动词短语，避免缩写和含糊命名。

## 2) 类型标注

- 对新增/改造函数提供参数与返回值类型标注。
- 外部接口（路由、核心服务函数）必须标注返回结构或基础类型。

## 3) 文档字符串（Docstring）

- 核心模块与关键函数使用三引号 docstring。
- 推荐 Google 风格段落（`Args` / `Returns` / `Raises`），但保持简洁。
- 对“为什么这样做”优先解释，而不是重复代码字面含义。

## 4) 异常处理与鲁棒性

- 对可预期失败（进程探测、端口探测、配置缺失）返回明确错误信息。
- 避免裸 `except`；至少记录日志并降级。
- 控制型 API（启动/停止）增加并发锁，避免竞态冲突。

## 5) 副作用控制

- 启停类函数应显式返回状态与日志路径，便于审计。
- 耗时等待使用轮询 + 超时，而非固定 `sleep`。

## 6) 文档维护要求

- 每次鲁棒性改造后，追加更新 `docs/ENGINEERING_UPDATES.md`：
  - 变更内容
  - 风险与回滚策略
  - 验证结果

## 7) 建议检查项（提交前）

- `python -m py_compile web/main.py`
- 关键路由 smoke test（`/`, `/ops`, `/api/system/status`）
- 一键启动链路验证（MinIO、crawler 状态可达）
