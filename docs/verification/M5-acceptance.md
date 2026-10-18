# M5 MVP 验收检查表（进行中）

日期：2026-10-18
结论：**未通过完整验收。** 计划功能实现与自动化／浏览器走查已完成；V001、V002、V004、V005、V006、V007 仍缺少真实模型复跑、全新环境验证或独立走查，不能把代码完成等同于阶段验收。

## V001 八项 MVP 端到端验收

| # | 验收项 | 证据 | 状态 |
| --- | --- | --- | --- |
| 1 | DDL 答案与引用一致 | Q018 确定性生成器与协议校验；真实模型语义复跑仍待 Q019 | 未完成 |
| 2 | 日期不完整时不补造事实 | X003、Q018 验证；日期精度与待核对字段测试 | 自动化通过，待真实模型复核 |
| 3 | 资料不足时明确说明 | Q011／Q012 固定文案与无候选路径测试 | 自动化通过 |
| 4 | 两周计划符合已知约束并列出未排任务 | `test_plans_api.py`、`test_planning_edges.py`；浏览器走查 | 自动化与走查通过 |
| 5 | 时间不足时展示缺口 | `test_capacity_gap_exposes_adjustment_options`、计划页面容量缺口与调整选项 | 自动化与走查通过 |
| 6 | 调整时间块不修改原始 DDL | `test_move_block_updates_schedule_but_not_task_ddl` | 自动化通过 |
| 7 | 部分解析失败可见 | X008、Q011 覆盖提示测试 | 自动化通过 |
| 8 | 删除后引用状态正确 | Q008、Q014 旧轮次引用复核测试 | 自动化通过 |

阻断项：真实模型语义复核（Q019 #392）尚未补跑；未做独立人工验收。V001 保持开放。

2026-10-18 复跑覆盖上述八项的自动化证据集合：

```bash
cd backend
uv run --locked pytest tests/test_qa_acceptance.py tests/test_qa_api.py \
  tests/test_deadline_completeness.py tests/test_plans_api.py \
  tests/test_planning_edges.py tests/test_planning_domain.py \
  tests/test_material_delete_invalidation.py tests/test_index_orchestration.py -q
```

结果 `120 passed`。该集合使用确定性生成器与协议校验，验证引用、日期完整性、资料不足、计划约束、容量缺口、移动不改 DDL、部分覆盖与删除后引用状态；不能替代真实模型语义复核与独立人工验收。


## V002 空间隔离与资料内指令边界

- 计划草案跨工作空间不可见：`test_plan_from_another_workspace_is_not_visible`。
- 意图协议只接受固定枚举与参数白名单；模型返回工具名被 502 拒绝：`test_intent_protocol_returns_boundaries_and_rejects_tools`。
- 资料内指令不会被当作执行授权：X 系列与 Q015 既有测试；本轮未新增真实样本复验。
- 2026-10-18 复跑空间隔离相关测试集合：

```bash
cd backend
uv run --locked pytest tests/test_workspace_scope.py tests/test_plans_api.py \
  tests/test_qa_api.py tests/test_jobs_api.py tests/test_source_read.py \
  tests/test_index_orchestration.py -q
```

结果 `68 passed`。覆盖猜 ID 读取／修改课程、学期、任务、日程与可用时段，跨空间关联创建，列表只返回当前空间数据，错误响应不泄露其他空间名称或编号；同时覆盖计划草案、问答会话、资料读取与索引编排的空间边界。结论：自动化部分通过；跨模块完整验收仍待 V002 负责人执行，V002 保持开放。


## V003 删除与运行中处理的端到端竞态

已有自动化覆盖，本轮未修改相关实现：

```bash
cd backend
uv run --locked pytest tests/test_job_write_guard.py tests/test_material_delete_invalidation.py tests/test_qa_api.py -q
```

相关用例：`test_conversation_keeps_scope_and_marks_deleted_citation_invalid`、`test_job_write_guard.py` 中删除后写入被拒绝的用例、`test_material_delete_invalidation.py` 中来源失效用例。

本轮执行结果：`27 passed`（2026-10-18）。结论：自动化通过；真实队列与独立复验仍按 V003 记录执行。

## V004 页面易用性与无障碍走查

已在独立端口完成真实浏览器走查（登录、生成、接受、锁定、移动、总览）。发现并修复：

- 计划时间按浏览器时区显示，而非工作空间时区；
- ≤760px 导航横向截断；
- 总览重复空状态；
- 尚无当前计划时把 404 显示为“加载失败”，现改为正常空状态。

新增 `frontend/tests/e2e/plan-keyboard.spec.ts`，仅用 Tab、Enter、方向键完成登录、导航到学习计划、生成草案、接受、锁定、调整时间与保存，并断言键盘焦点仍有可见轮廓；移动时间块使用原生日期时间控件的分段方向键编辑。`frontend/tests/e2e/plan.spec.ts` 同步改为精确匹配“当前计划”，避免与“查看当前计划”按钮歧义；另覆盖过期草案后的刷新与加载失败后的重试恢复。`frontend/tests/e2e/responsive.spec.ts` 在 390px 宽度下验证八个导航项完整可见、计划页可操作且页面无横向溢出。

在清空后的专用测试库上执行完整套件：`npm run build` 通过，`npx playwright test` 为 `10 passed, 1 skipped`（资料库真实队列用例按环境跳过）。

语义色 token 的 WCAG 对比度计算（浅色／深色）：正文 `ink/paper` 14.45／13.79，次要文字 `muted/paper` 4.94／6.69，品牌文字 `brand/paper` 9.36／9.32，按钮文字 `on-brand-fill/brand-fill` 9.45／4.71，风险与提示文字均不低于 5.78；侧栏禁用项 `sidebar-disabled/sidebar-bg` 为 3.94／4.58，属不可交互的“尚未开放”标签，按 WCAG 非活动组件豁免。未执行屏幕阅读器（VoiceOver／NVDA）实测与独立走查，V004 保持开放。


## V005 典型样本处理时延与模型成本

- 环境：2026-10-18，macOS arm64、Python 3.12、本地 PostgreSQL 17；解析使用仓库内合成样本，未调用外部模型。
- 两周计划为确定性本地计算，不调用模型。本机测试库、单任务样本，连续 5 次 `POST /plans/drafts`：14.2–23.9 ms。
- PDF 文本层（`output/pdf/b010-text-and-cross-page-table.pdf`，7.2 KiB，3 个片段）：5 次解析 6.3–7.2 ms。
- 普通 DOCX（`samples/synthetic/n001/word-notice-paragraph.docx`，36.3 KiB，9 个片段）：17.2–19.5 ms。
- 表格 DOCX（`samples/synthetic/n001/word-notice-table.docx`，36.3 KiB，19 个片段）：18.4–19.7 ms。
- 计划功能不产生模型费用；问答与嵌入成本仍以 Q001 账本为准。
- 未测量：视觉识别、嵌入与生成的端到端时延和费用（本机未配置模型密钥，Redis 队列未运行）；大文件与并发场景同样未覆盖。V005 的模型用量部分仍不完整，不能据此宣称典型样本全链路达标。


## V006 本地整套启动与故障恢复说明

已更新 [DEVELOPMENT.md](../DEVELOPMENT.md)：补充 M5 迁移、启动新代码前步骤、计划冒烟流程、专项测试命令、输入指纹与 409 语义、当前限制，并新增数据库、Redis 与模型服务不可用时的诊断和恢复路径。

本轮按文档在独立端口启动后端（8001，测试库）与前端（5174），完成登录、生成、接受、锁定、移动与总览走查，启动说明可用。2026-10-18 进一步以不可达端口模拟故障：诊断分别输出 `database_or_schema_unavailable` 与 `redis_unavailable`，Worker 在 Redis 不可达时以退出码 1 结束并提示运行诊断。

同日执行空库迁移闭环：在专用测试库删除全部业务表与 `alembic_version`（保留已存在的 vector 扩展）后，从零执行 `alembic upgrade head`，成功到达 `p001_planning_drafts`；在新端口 8002 启动后端，`/api/v1/ready` 返回数据库正常，并完成登录、学期、课程、可用时段、任务、草案生成、接受及当前计划／总览读取。

另在 8003 端口以不可达数据库启动后端，`/api/v1/ready` 返回 503 与 `{"detail":"数据库不可用"}`；切回可用数据库重启后返回 200 与 `database:"ok"`，验证了停机后的恢复路径。

限制：本机账号没有 CREATEDB 与创建 `vector` 扩展的权限，因此没有新建全新数据库，也未在全新机器或空 PostgreSQL 实例上验证；Worker／Redis 全链路未运行。V006 保持开放。



## V007 发布说明与候选版本验收

**状态：草案，未发布。** 不满足发布门槛，未创建 `v0.5.0` 或 `v0.5.0-rc.N` 标签。

### 候选能力

- 手动管理：学期、课程、任务、进度历史、固定日程与可用时间。
- 资料：PDF／图片／粘贴文本／DOCX／网页导入，原文定位、归档与确认删除，后台解析。
- 核对：候选列表、详情、逐字段修正与确认后生成正式任务／日程／规则。
- 问答：有范围检索、引用校验、来源失效、范围会话与问答页面。
- 计划：未来两周草案、容量缺口与调整选项、冲突与风险、版本化接受、时间块移动／锁定、自然语言入口与今日总览。
- 本机单用户会话与跨工作空间对象授权。

### 未完成项与发布阻塞

| 项目 | 状态 |
| --- | --- |
| V001 八项 MVP 验收 | 真实模型语义复核（Q019 #392）未补跑，未独立验收 |
| V002 空间隔离与资料内指令边界 | 自动化 68 passed；独立跨模块验收未执行 |
| V003 删除与运行中处理竞态 | 自动化 27 passed；真实队列复验未执行 |
| V004 易用性与无障碍 | 键盘全流程与对比度已检查；屏幕阅读器实测与独立走查未执行 |
| V005 时延与成本 | 本地解析／计划时延已测；模型与队列费用未测 |
| V006 启动与故障恢复 | 文档与诊断路径已验证；全新机器从零验证未执行 |
| V008 课程项目 PRE | 依赖 V001、V007，未开始 |

### 迁移

- 新增迁移 `p001_planning_drafts`，依赖 `q014_qa_conversations`，创建 `plan_drafts`、`plan_blocks`、`plan_versions`；不回填既有数据。
- 启动新代码前在 `backend/` 执行 `uv run --locked alembic upgrade head`；测试库由 pytest 自动迁移。
- 依赖未变化，无需更新锁文件；Worker 与 API 必须使用同一迁移版本。

### 回滚

1. 停止 API 与 Worker，避免旧代码读取新表。
2. 部署上一版本 `v0.4.0` 代码。
3. 如需回滚数据库结构，执行 `uv run --locked alembic downgrade q014_qa_conversations`，该操作删除计划草案、时间块与版本快照；回滚前先备份需要保留的计划数据。
4. 旧版本不会读取计划表，保留表结构通常不影响运行；数据回滚必须显式执行，不作为自动步骤。

### 候选版本检查（2026-10-18）

- `uv run --locked ruff check .` 通过；后端 CI 全量测试通过。
- `npm run build` 通过；完整 e2e 为 `7 passed, 1 skipped`（资料库真实队列用例按环境跳过）。
- PR #394–#396 CI 全绿；尚未合并到 `develop`／`main`。
- 结论：**不满足发布条件**，V007 保持开放。


## 本轮完成范围

P001–P018 的代码、测试、页面与文档见 [M5-planning.md](M5-planning.md)。M5 里程碑保持 open，等待 V 系列独立验收。
