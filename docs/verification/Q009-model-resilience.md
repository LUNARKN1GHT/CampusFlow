# Q009 验证记录：模型超时、重试上限与可见错误

日期：2026-10-17
分支：`issue/#136`（从父任务分支 `issue/#13` 新建，不包含未合并的 Q004 代码）
范围：Q009「先做底层契约」——错误分类、用户文案、超时与重试配置。

## 1. 本次交付与验收边界

Q009 的验收条件：

- [x] 失败时不伪造答案或引用
- [x] 用户能理解服务不可用与资料不足的区别（**文案契约层**）

本轮建立的是底层契约：

- 模型异常统一转换为三类可见失败，并给出固定中文文案；
- 失败路径整批中止，不写候选、不返回部分草稿、不生成确定事实；
- 视觉识别的超时、重试次数与退避可通过环境变量配置，并区分临时/永久失败。

**尚未完成（不属于本轮）**：问答 API 与页面展示（Q011），以及「资料不足」的判定规则（Q011/Q012）。因此第二条验收目前只到「两种提示语已定义、互不相同、有测试锁定」，还没有真实页面向用户展示。本文不把它写成端到端已完成。

## 2. 错误分类契约

新增 `domain/model_failures.py`：

| 异常 | 可见类别 | 可重试 | 用户文案要点 |
| --- | --- | --- | --- |
| `TransientError` | `service_unavailable` | 是 | 服务暂时不可用，稍后重试 |
| `ModelOutputError` | `invalid_output` | 否 | 返回内容无法使用，检查资料后重试 |
| 其他 `PermanentError` | `request_rejected` | 否 | 请求被拒绝，联系管理员检查账号/模型配置 |
| 其他 `DomainError` | 不分类（`None`） | — | 由调用方按业务语义处理 |
| 未预期异常 | `service_unavailable` | 是 | 宁可提示稍后重试，也不返回可能被误认成答案的内容 |

所有文案均为固定文本，不回显异常原文、HTTP 细节、密钥或资料正文；每类都明确「本次未生成回答或引用」。

`INSUFFICIENT_MATERIALS_MESSAGE` 与上述三类分别定义，文案指向「补充资料」，且不含「服务不可用」。本轮只定义、不触发。

## 3. 代码变更

- `domain/errors.py`：新增 `ModelOutputError(PermanentError)`，区分「模型输出不可用」与「请求被拒绝」。
- `application/candidate_generation.py`：`InvalidModelOutput` 改继承 `ModelOutputError`，既有 `except PermanentError` 行为不变。
- `infrastructure/llm/text.py`、`embeddings.py`：响应超限、结构无效、截断或维度不一致改抛 `ModelOutputError`；HTTP 4xx（非 408/429）仍是 `PermanentError`。
- `infrastructure/llm/errors.py`：视觉错误分层
  - `VisionRecognitionFailure`：统一捕获基类；
  - `VisionRecognitionError`：临时故障（408/429/5xx/网络/超时）；
  - `VisionRequestRejected`：请求被拒绝（其他 4xx）；
  - `VisionInvalidOutput`：响应无法解析或超限。
- `infrastructure/llm/vision.py`：
  - 增加 1 MiB 响应读取上限和禁止跳转；
  - 只重试临时错误，永久错误立即上抛；
  - 构造时校验超时（0–120 秒）、重试次数（0–3 次）、退避（0–10 秒）。
- `infrastructure/parsers/pdf_with_vision.py`：单页回退统一捕获 `VisionRecognitionFailure`，永久失败标记 `retryable=false`，其他页结果保留。
- `core/config.py`、`workers/main.py`、`backend/.env.example`：新增并接通视觉配置。

## 4. 配置

| 环境变量 | 默认值 | 允许范围 | 说明 |
| --- | --- | --- | --- |
| `CAMPUSFLOW_VISION_TIMEOUT_SECONDS` | 60 | 大于 0、不超过 120 | 单次调用的连接/读取等待上限 |
| `CAMPUSFLOW_VISION_MAX_RETRIES` | 2 | 0–3 | 首次失败后的额外重试次数；0 表示只调用一次 |
| `CAMPUSFLOW_VISION_RETRY_BACKOFF_SECONDS` | 1 | 0–10 | 线性退避基数：第 n 次重试等待 n × 该值 |

最坏等待：默认配置下 3 次调用约 180 秒 + 3 秒退避；即使全部取上限（4 次 × 120 秒 + 6 秒退避），仍低于作业超时默认 900 秒。

文字与向量适配器保持原有的单次调用语义：不在适配器内暗中重试，队列层按 J005 的 `retryable` 判定决定是否重投。

## 5. 验收证据

### 5.1 失败时不伪造答案或引用

| 场景 | 测试 | 结果 |
| --- | --- | --- |
| 文字模型临时故障 | `test_candidate_generation.py::test_model_failure_returns_no_drafts_and_writes_nothing[transient]` | 整批抛出，`ExtractedItem` 计数为 0，任务列表为空 |
| 模型输出无效 | 同上 `[invalid-output]` | 整批抛出，不写库、不返回草稿 |
| 视觉响应结构无效 | `test_vision_parser.py::test_invalid_response_shapes_raise_error` 等 | 抛 `VisionInvalidOutput`，不产生任何片段 |
| 扫描页识别失败 | `test_pdf_vision_fallback.py::test_vision_failure_retryability_is_preserved` | 该页标记失败，文本页结果保留，不补造内容 |

### 5.2 服务不可用与资料不足可区分

- `test_model_failures.py::test_messages_are_distinct_and_promise_no_answer`：四类文案互不相同，均声明未生成回答或引用。
- `test_model_failures.py::test_insufficient_materials_is_clearly_different`：资料不足文案包含「补充资料」、不含「不可用」，与服务不可用文案不同。
- `test_model_failures.py::test_classified_message_does_not_echo_exception_detail`：分类结果不回显异常原文或密钥。

### 5.3 重试与配置

- `test_vision_parser.py`：401 只调用一次；429/5xx 按上限重试；429 后成功可恢复；超限响应不重试；网络错误按临时故障处理；构造参数越界被拒绝。
- `test_worker_operations.py::test_real_worker_passes_vision_limits`：三个环境变量值确实传入适配器。
- `test_job_retry_timeout.py::test_is_retryable_distinguishes_error_kinds`：永久视觉错误不可重试，临时错误可重试。

## 6. 执行命令与结果

均在 `backend/` 下执行：

```bash
CAMPUSFLOW_TEST_DATABASE_URL="postgresql+psycopg://campusflow:campusflow@127.0.0.1:5432/campusflow_q009_test" uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
```

结果：

- 完整后端回归：**1077 passed**，80.42 秒，4 个既有警告（Starlette/httpx 弃用、未知 `integration` 标记、Alembic `path_separator`）。
- 专项测试：模型适配器与契约 127 passed；数据库相关 67 passed。
- `ruff check`：All checks passed。
- `ruff format --check`：211 files already formatted。

**为什么使用专用测试库**：Q004 分支已把共享的 `campusflow_test` 升级到 `q004_index_hnsw`，该迁移不在本分支，Alembic 无法回退。为避免改动其他工作分支共用的测试库，本轮新建 `campusflow_q009_test` 并在此完成迁移与回归；开发库 `campusflow` 未触碰。

## 7. 已知限制

- 本轮没有问答 API 或页面，错误文案尚未展示给真实用户；展示由 Q011 接入。
- 「资料不足」只有文案常量，没有判定逻辑；判定由 Q011/Q012 完成。
- 未调用真实模型，未产生费用；真实供应商的 4xx 分类与超时行为需在联调时抽查。
- 视觉适配器的内部重试与队列级重试是两层机制：适配器只负责一次作业内的有限重试，作业失败后的重投仍由 J005 控制。
- 本分支不包含 Q004 的索引流水线，因此本轮没有验证「索引作业遇到模型失败」的场景；Q004 合并后由后续任务覆盖。
