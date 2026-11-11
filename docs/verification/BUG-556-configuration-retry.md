# #556 缺模型配置的失败作业恢复

对应 Issue：#556。分支：`issue/#556`。

## 问题

缺少模型密钥时，解析／索引作业以 `PermanentError` 失败，`retryable=false`；用户补齐配置后仍无法重试，只能删除资料重传。

## 修复

- 新增领域错误 `ConfigurationError(PermanentError)`，表示“配置修正后可恢复”。桌面 Worker 的视觉／向量占位实现改抛该错误，提示指向「设置 → AI 服务」。
- `is_retryable` 对 `TransientError` 与 `ConfigurationError` 返回真：作业可人工重试；自动重投仍受既有上限约束，不会无限重试。损坏文件等 `PermanentError` 保持不可重试。
- 索引块新增失败码 `configuration_missing`：不参与块级自动重试；索引汇总新增 `configuration_failed` 计数，全部或部分块因此失败时作业标记为可人工重试，错误原因提示先补齐配置。
- `prepare_retry` 在人工重试索引作业时，仅把 `configuration_missing` 的块恢复为待处理并清零尝试次数；已成功块保持不动，重试只补失败部分。人工重试仍走原有条件更新，重复点击不会并发重复处理。

## 验证

- `uv run ruff check`（改动文件）：通过。
- `uv run pytest tests/test_index_orchestration.py tests/test_indexing_progress.py tests/test_jobs_api.py tests/test_job_retry_timeout.py -q`：72 passed。
- `uv run pytest tests/test_processing_jobs.py tests/test_job_fault_injection.py tests/test_desktop_worker.py -q`：20 passed。
- 新增用例覆盖：单块缺配置 → 部分成功、可人工重试、成功块不重做；全部块缺配置 → 失败可人工重试；重试后相同资料恢复完整可检索；永久失败仍不可重试（既有用例）。

## 未覆盖

- 未在真实桌面安装版中执行“缺密钥失败 → 保存新密钥 → 重启 → 重试”的完整人工链路；配置入口由 #553 提供。
- 自动重投仍可能对缺配置作业做有限次（受 `max_auto_retries` 约束）快速失败后才依赖人工重试；没有新增“配置类失败完全跳过自动重投”的数据库字段。
- 视觉解析失败后的资料级恢复依赖现有解析状态重置，本轮未增加新的资料状态展示。
