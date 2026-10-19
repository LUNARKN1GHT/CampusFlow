# A019 日历草案 ICS 导出

作者：李亦凡；任务 #210；父任务 #22。前置 #205（A017）、#209（A018）。

## 范围

- **任务模式**：每个有截止的所选任务一个 `VEVENT`。仅有日期的 DDL 输出 `DTSTART;VALUE=DATE`／次日 `DTEND`，不补 00:00 或 23:59；带时刻的 DDL 按工作空间时区解释后换算 UTC，`DTEND` 为 15 分钟显示窗口，不表示耗时。
- **计划模式**：只接受请求显式给出的 `plan_id`，服务端校验 `status=accepted` 且仍是该学期当前已接受计划；草案、被替代计划或缺失计划返回中文 400，不自动接受草案。
- 计划只导出 `kind=task` 时间块；`buffer`、无任务块与已取消任务块排除并计数，未排入任务给出警告。固定日程默认不导出，显式勾选后才展开为独立发生项，不写 `RRULE`。
- UID 稳定：任务 `campusflow-task-{id}-due@campusflow.local`；计划块 `campusflow-plan-{draft}-block-{block}@campusflow.local`；固定日程发生项以开始时间区分。重复导出同一范围不改变 UID。
- 序列化：CRLF、75 字节 UTF-8 安全折行、文本转义、`DTSTAMP`；时间点写 UTC，仅日期保持浮动 `DATE`，因此不生成 `VTIMEZONE`。不写 `DESCRIPTION`、`VALARM` 或任何原文摘录。
- 预览新增 `event_count` 与计划范围字段；范围指纹绑定模式、格式、计划版本、任务修订、固定日程发生项与选项。下载指纹不一致返回 409，文件名只含日期。

## 验证

```text
backend$ uv run --locked ruff check .
All checks passed!
backend$ uv run --locked ruff format --check .
... clean
backend$ uv run --locked pytest -q tests/test_exports.py
17 passed
backend$ uv run --locked pytest -q
1421 passed, 6 skipped
```

使用独立测试库 `campusflow_a019_test`；未修改共享测试库。覆盖：日期型与时刻型区分、UTC 换算、重复导出 UID 与时间不变、特殊字符／长标题折行、计划块导出、草案／替代计划拒绝、缓冲与取消排除、未排入警告、固定日程显式勾选、指纹失效、无描述与原文泄漏。

## 未覆盖与限制

- 计划模式 CSV 未提供，按 A017 明确拒绝。
- 接受新草案会生成新的 `PlanBlock.id`，UID 随之变化；再次导入可能与旧计划事件重复，界面需提示先删除旧事件（A020）。
- 固定日程发生项时间修改会生成新 UID；旧 UID 不自动撤销。
- 真实日历客户端导入与跨时区展示由 A021 验证。
