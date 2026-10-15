# R006 保存确认规则并提供规则查询

任务：[R006 #123](https://github.com/LUNARKN1GHT/CampusFlow/issues/123)，父任务 #12（E31）。作者：李亦凡（LUNARKN1GHT）。日期：2026-10-15。

## 用户决定与实现

用户选择两项：条件、适用范围与生效声明只保留原文不换算、规则查询做成受保护的 HTTP 只读列表。

新增 `course_rules` 表（迁移 `r007_course_rules`）：`source_candidate_id`（唯一、可空、`ON DELETE SET NULL`）保证一个候选最多建一条正式规则，作为幂等依据；`source_invalidated_at` / `source_invalidated_note` 对齐任务的 D009 口径，记住「曾经有来源、来源已被删除」。条件、适用范围与生效声明只保存原文文本，不解析日期、不换算相对写法，缺少年份或依据不足时保留待核对，不把猜测当成已确认约束。

## 字段映射与校验

| 候选字段 | 课程规则 | 说明 |
| --- | --- | --- |
| title | title | 必填；为空或全空白时拒绝；超过 200 字符拒绝 |
| rule_text | rule_text | 必填；为空或全空白时拒绝 |
| conditions_text | conditions_text | 原文可选；空串归一为空，不补造内容 |
| scope_text | scope_text | 同上 |
| effective_text | effective_text | 只保留原文；不解析生效日期、不换算相对写法 |
| 来源版本 | source_material_id | 取候选绑定版本的资料 |
| — | source_candidate_id | 幂等键与来源关联 |
| 适用范围 | course_id、semester_id | 候选课程与学期，须仍属于当前空间 |

`domain/rule_conversion.py` 的 `build_rule_conversion` 只做字段层面的保守检查：缺标题或正文时拒绝转换，可选字段取字符串原值、空白归一为空，不对任何字段做语义判断。

应用入口：`application.conversion.convert_candidate_to_rule(repos, workspace_id, candidate_id)`。

- 要求候选已确认（复用 R003 的 `require_confirmed_for_conversion`）：待核对、已忽略、已失效的候选不能当作正式要求，拒绝转换。
- 已转换的候选再次调用返回既有规则，不新建、不改动既有规则。
- 生效字段取叠加 R002 修正后的取值，因此用户补齐的标题或正文会进入正式规则。
- 转换不修改候选核对状态。
- 非规则候选（任务、日程）拒绝转换；候选课程或学期已不可用时拒绝并提示重新核对适用范围。

## 规则查询接口

`GET /api/v1/rules` 返回当前工作空间的正式课程规则列表，可按 `course_id`、`semester_id` 过滤；过滤对象不属于当前空间时按 W006 口径返回 404。接口走本机会话与工作空间授权；只有经核对确认并转换的规则才会出现，未确认候选不当作正式要求。

## 资料删除后的行为

- 删除资料时按候选集合解除规则关联：`source_material_id` 与 `source_candidate_id` 置空，写入 `source_invalidated_at` 与说明（含原资料标题）。规则始终保留，不会静默消失，也不再声称来源可验证；`source_state` 口径与任务一致（available / invalidated / none）。
- 删除影响预览新增 `linked_rule_ids`，并计入删除确认指纹：关联规则集合变化时旧确认失效。

## 验证

测试命令：`cd backend && uv run pytest tests/test_candidate_rule_conversion.py`。使用合成通知、真实 PostgreSQL 测试库与既有候选保存入口，不调用真实模型。覆盖：

- 字段映射与校验：非规则候选、缺标题、缺正文、标题超长拒绝；可选字段空白归一为空。
- 生效声明只保留原文：相对写法与不完整日期不换算、不补造。
- 转换建立规则并写入 `source_material_id`、`source_candidate_id`，候选仍为已确认。
- 重复转换返回同一规则且规则数不增；数据库唯一约束拒绝为同一候选再建规则。
- 只有已确认候选可转换，待核对与已忽略被拒且不建规则。
- `GET /api/v1/rules` 按课程／学期过滤；跨空间访问与不属于本空间的过滤对象返回 404；未确认候选不出现在列表。
- 删除资料后规则保留并标记来源失效；预览中的关联规则编号出现并使确认指纹变化。

测试结果：专项 14 项通过（含 2 项纯领域、10 项应用用例、2 项 HTTP 接口）；完整后端 668 项通过。

```powershell
uv run --locked pytest tests/test_candidate_rule_conversion.py -q
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked alembic upgrade head
```

未执行：核对页面交互（R008、R009）；本轮不开放候选写入接口。

前端仅扩展删除影响预览的类型与提示文案，`npm run build`（含类型检查）通过。

## 未接入项

- 尚无候选 HTTP 接口与核对界面，由 R008、R009 完成；规则转换目前只经应用入口调用。
- 生效声明不做日期换算：相对写法与不完整日期原样保留，是否生效由用户核对判断。
- 不判断条件与适用范围的语义正确性，也不做跨来源的矛盾检查。

## 分支与前置

从父分支 `issue/#12`（已含 R002–R005、R007，`68088a7`）创建 `issue/#123`，本项 PR 的合并目标为 `issue/#12`。前置 R003 #120 已关闭；后续 R008、R009 提供候选 HTTP 接口与核对界面，R007 的来源详情口径同样适用于本项写入的来源关联。
