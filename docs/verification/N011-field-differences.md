# N011：候选字段差异与新旧来源

任务 #155，父任务 #19。作者：金嘉睿。用户选择显式指定两个候选进行比较，“明确删除”按可验证原文证据区分。分支 `issue/#155` 基于父分支 `issue/#19`（`95dfe15`），PR 目标为 `issue/#19`，不直接合并到 `develop`。

## 已实现的范围

- 只读比较两个同类型候选（任务、日程或规则），任意核对状态均可比较。
- 调用方显式给出 `left`（基准/旧）与 `right`（待比较/新）；系统不按标题、课程、版本号或导入时间自动配对。
- 每个字段差异同时返回两侧候选编号、来源版本、字段是否存在、原始提取值、叠加修正后的生效值、出处、缺失原因、逐条原文摘录与定位、引用是否仍可验证。
- 外层返回两侧资料编号/标题/版本号/归档标记、候选类型、核对状态与结构化适用范围。
- 结构化适用范围逐项比较学期、课程、班级；名称只在属于当前工作空间时展示。
- 不判定哪个版本生效，不读取 N010 的替代声明来下结论，不比较已转换的正式任务/日程/规则。

接口没有数据库迁移、没有新增依赖、没有模型调用、没有前端改动。

## 验收对照

### 1. 每个差异指向新旧来源

- 顶层 `left`/`right` 给出各自的候选、来源版本、资料标题、版本号与归档状态。
- 每个字段差异的左右侧都带 `candidate_id`、`source_version_id`，即使该侧没有这个字段也能指回来源。
- 每条证据带 `chunk_id`、逐字摘录 `quote`、定位方式与定位值、`verifiable`。
- `tests/test_candidate_diff.py::test_diff_points_each_change_to_its_own_source` 断言截止时间与地点变化分别来自旧、新两份资料的片段，定位值非空且可验证；归档来源仍可读的用例也保留证据。

### 2. 字段缺失与明确删除分开显示

状态值：

| 状态 | 含义 |
|---|---|
| `unchanged` | 两侧生效值相同，包含两侧都为空 |
| `changed` | 两侧都有值且不同 |
| `added` | 旧侧无值、新侧有值 |
| `missing` | 新侧没有该字段记录，或新侧为空但没有可验证的原文证据 |
| `explicitly_removed` | 新侧原文明确为空，并且仍有可验证证据 |

“明确删除”必须同时满足：

```text
新侧存在该字段记录
原始提取值为 null
叠加用户修正后的生效值仍为 null
至少一条证据仍能在当前原文片段中逐字找到
```

因此以下情况都是 `missing`，不会显示成“明确删除”：

- 新候选没有保存这个字段；
- 保存了空值但没有附证据；
- 附了证据，但原文片段后来被改写、摘录已经找不到；
- 用户通过修正接口清空字段（原始提取值不是 null）。

`missing_reason` 只是说明文字，不作为“明确删除”的依据。字段级 `verifiable` 在“空值且无证据”时可能因空集合而显示为真，所以“明确删除”单独统计可验证证据条数，不直接复用该字段。

对应测试：

- `test_missing_and_explicit_removal_are_distinct`：同一对新旧候选里，原文写“已取消”的截止时间为 `explicitly_removed`；没有地点/提交方式/交付物证据的字段为 `missing`。
- `test_unverifiable_evidence_degrades_explicit_removal_to_missing`：先得到 `explicitly_removed`，改写原文片段使摘录失效后变为 `missing`。
- `test_effective_values_drive_comparison_but_raw_value_and_evidence_stay`：修正值参与比较，原始值与原始证据保留；修正清空为 `missing`。
- 领域参数化测试覆盖空值无证据、原始有值但生效值为空、两侧都为空、`1` 与 `true`、空字符串与 `0` 等边界。

## 字段类别与排序

- `date`：`due_text`、`due_date`、`due_time`、`start_text`、`end_text`、`effective_text`
- `location`：`location`
- `requirement`：`submission_method`、`submission`、`deliverable`、`participation_conditions`、`conditions_text`、`rule_text`
- `scope`：`scope_text`
- `other`：标题、活动内容、耗时建议及所有未识别字段

字段取两侧并集，包括 `unchanged`；按类别顺序（date、location、requirement、scope、other）再按字段名排序，重复请求顺序稳定。未识别字段归 `other`，不会丢失。

## 接口

```http
GET /api/v1/candidates/{candidate_id}/diff/{other_candidate_id}
```

- 第一个编号为 `left`（基准/旧），第二个为 `right`（待比较/新）；方向由调用方负责，响应中的 `left_role=baseline`、`right_role=comparison` 再次标明。
- 要求登录，按当前工作空间隔离。
- 两个编号相同返回 400“不能比较同一个候选事项”；候选类型不同返回 400“只能比较同一类型的候选事项（任务、日程或规则）”。
- 候选不存在、属于其他空间，或候选来源资料不存在，返回 404，不泄露是否存在。
- 无请求体，成功返回 200；不写库、不提交事务。

响应主要结构：

```json
{
  "left_role": "baseline",
  "right_role": "comparison",
  "comparison_basis": "effective_value",
  "left": {
    "candidate_id": 1,
    "source_version_id": 1,
    "material_id": 1,
    "material_title": "旧通知",
    "version_no": 1,
    "material_archived": false,
    "kind": "task",
    "review_status": "pending",
    "scope": {
      "course_id": null,
      "semester_id": null,
      "class_name": null,
      "course_name": null,
      "semester_name": null
    }
  },
  "right": {},
  "scope": {"left": {}, "right": {}, "differences": []},
  "fields": [
    {
      "name": "due_text",
      "category": "date",
      "status": "changed",
      "left": {
        "candidate_id": 1,
        "source_version_id": 1,
        "present": true,
        "value": "下周五",
        "effective_value": "下周五",
        "origin": "document",
        "effective_origin": "document",
        "explanation": null,
        "missing_reason": null,
        "explicitly_removed": false,
        "verifiable": true,
        "evidence": [
          {
            "chunk_id": 1,
            "quote": "下周五",
            "locator_type": "paragraph",
            "locator_value": "p:1",
            "verifiable": true
          }
        ]
      },
      "right": {}
    }
  ],
  "summary": {
    "total": 1,
    "unchanged": 0,
    "changed": 1,
    "added": 0,
    "missing": 0,
    "explicitly_removed": 0
  }
}
```

字段不存在的一侧仍返回对象，但 `present=false`、值为空、证据为空，界面可据此区分“没有这个字段”和“字段存在但值为空”。

## 结构化适用范围

`semester`、`course`、`class_name` 逐项比较，状态只可能是 `unchanged`、`changed`、`added`、`missing`；适用范围没有逐字段证据，因此不会产生 `explicitly_removed`。`null` 表示未知，不当作“适用于所有人”。范围不同不阻止比较，也不推导生效结论。课程/学期名称只在本空间内解析，查不到就留空。

## 只读、空间隔离与来源状态

- `compare_candidates` 只调用候选、资料、版本、片段、课程与学期的读取方法，不提交事务，不写候选、字段、证据、修正或核对记录。专项测试把 `uow.commit` 替换为失败仍能完成比较，并核对五张表行数不变。
- 跨工作空间候选与来源资料返回同一个 404，不返回标题、字段或证据。
- 归档资料仍可比较，只把 `material_archived` 置为真；来源删除会级联删除候选，正常路径不会出现“候选还在但资料已删除”。
- 任意核对状态均可比较，包括已确认、已忽略和已失效。

## 明确不做

- 不自动配对，不按标题、课程、时间或相似度寻找“对应候选”，不支持批量比较。
- 不比较已转换的正式任务、固定日程或课程规则。
- 不判断哪个版本生效，不读取替代声明，不按发布时间、版本编号或导入时间排序。
- 不做合并、确认、转换或界面（N017 接）；不生成正式事项。
- 无迁移、无新依赖、无前端、无模型调用。

## 安装与验证

没有新增依赖或迁移，更新后不需要额外安装步骤。在 `backend/` 执行：

```text
uv sync --locked
```

专项测试使用独立 PostgreSQL 测试库 `campusflow_n011_test`（库名以 `_test` 结尾，未使用开发库）：

- `tests/test_candidate_diff.py`：**45 passed**，2.19 秒。覆盖五种字段状态、范围状态、修正后的生效值、明确删除降级、方向交换、同编号/异类型 400、跨空间与未登录、归档来源、任意核对状态、只读不提交、路由回归与稳定排序。
- 完整后端回归：**929 passed**，67.63 秒，4 个既有警告；未连接模型服务。
- `ruff check .`：All checks passed。
- `ruff format --check .`：206 个文件已格式化。
- `git diff --check`：通过。
- 前端未改动，未运行前端类型检查或构建。

## 已知限制

- 引用可验证仅表示摘录仍能在当前原文片段中逐字找到，不代表摘录在语义上一定表示“取消”；是否接受变更仍需人工核对。
- 比较基准是叠加用户修正后的生效值；原文证据只支持原始提取值，响应同时给出原始值/生效值与各自出处，界面不得把证据展示成修正值的证明。
- 只有 JSON `null` 算空值；空字符串、`0`、`false` 都是有效值。
- 响应不做分页；两个候选最多各 64 个字段、每字段最多 32 条证据，按单个对照请求可接受。
- 本任务的验收由后端接口和数据库测试覆盖，尚未接入界面；审核与合并交给非作者成员。
