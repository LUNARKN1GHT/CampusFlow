# C001 培养方案与课程依赖验收样本

## 交付物

- [curriculum-2024.md](samples/course-paths/curriculum-2024.md)：合法强制先修图的 2024 版样本。
- [curriculum-2025.md](samples/course-paths/curriculum-2025.md)：包含环、自环、缺项与来源矛盾的 2025 修订版样本。
- [sample-plan.json](samples/course-paths/sample-plan.json)：两版结构化样本，含课程、关系、缺项、冲突与预期结果。
- [relation-annotations.md](samples/course-paths/relation-annotations.md)：逐条关系的人工标注索引与预期处理。

## 覆盖范围

| 场景 | 样本位置 | 预期 |
| --- | --- | --- |
| 强制先修 | 两版 R-*-0xx 基础链 | 参与拓扑排序 |
| 并修 | R-2024-016 / R-2025-016 | 只提示同学期，不产生顺序 |
| 建议先学 | R-2024-017～018 / R-2025-024 | 软提示，不阻塞 |
| 三节点环 | R-2025-019～021 | 返回 `CS401 → CS402 → CS403 → CS401` |
| 自环 | R-2025-022 | 返回 `CS404 → CS404` |
| 缺项 | CS499 / R-2025-023 | 显示缺失节点与来源，不补课程 |
| 来源矛盾 | CG-2025-001 | 两条断言并列，核对前不宣称完整 |
| 版本差异 | CS102、CS201、CS205、CS209–CS211、关系类型变化 | 两版分别读取，不混接 |
| 未知学分/学期 | CS209、CS210、CS211、CS215 | 保留未知，不影响依赖顺序 |

## 验收对应

| 验收条件 | 证据 |
| --- | --- |
| 每条关系有人工标注来源 | 两版 Markdown 关系表均含“来源标注”列；JSON 每个 relation 含 `source`；`relation-annotations.md` 汇总 |
| 包含不能得出完整顺序的反例 | 2025 修订版明确 `complete_order_expected=false`，并列出环、自环、缺项、冲突四类阻塞 |

## 验证记录

- `python -m json.tool docs/verification/samples/course-paths/sample-plan.json > /dev/null`：通过。
- 本任务为样本与文档交付，不包含代码测试；C017 将使用该样本执行端到端验收。

## 使用方式

C017 按 `sample-plan.json` 建立 2024/2025 两个方案版本，断言 2024 版可排序且每条强制先修满足；2025 版返回具体环路径、自环、缺项与冲突，且不输出完整顺序。样本不用于任何真实教务结论。
