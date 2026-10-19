# A021 验证导出文件的日期和时区互操作

作者：李亦凡；任务 #217；父任务 #22。前置 #214（A020）。

## 覆盖场景

1. **仅日期 DDL 无时间漂移**：同一任务在 `Asia/Shanghai` 与 `America/Los_Angeles` 两个工作空间时区下导出，`DTSTART;VALUE=DATE` 始终为截止日、`DTEND;VALUE=DATE` 为次日，且不存在带时刻的 `DTSTART`。
2. **带时刻 DDL 是同一时间点**：上海时区 `2026-11-02 23:30` 导出为 `20261102T153000Z`；在 `America/New_York` 显示为同日 10:30、在 `Asia/Tokyo` 显示为次日 00:30，均为同一 UTC 瞬间。`DTEND` 保持 15 分钟显示窗口。
3. **跨日计划块不裁剪**：插入 `23:30–01:00` 的 90 分钟计划块，导出后 `DTSTART=20261019T153000Z`、`DTEND=20261019T170000Z`，在计划时区结束于次日 01:00，未裁剪到当天。
4. **每周固定日程展开**：跨周计划范围导出两次发生项（10-19、10-26），每项以 UTC 开始时间区分 UID，跨日发生项保持次日结束；不写 `RRULE`，重复导出 UID 集合一致。
5. **清单隐私**：CSV 表头与 A017 固定列一致，不含 `description`、`due_note`、`teacher`、`class_name`、原文引文、发布方或链接列；任务描述、截止备注、教师与教学班不出现在文件正文。
6. **非法计划时区**：计划输入快照时区不可读时预览返回 400，明确提示时区问题，不回落 UTC。

## 验证

```text
backend$ uv run --locked ruff check .
All checks passed!
backend$ uv run --locked ruff format --check .
... clean
backend$ uv run --locked pytest -q tests/test_export_interop.py
6 passed
```

真实 PostgreSQL；无新增迁移。

## 未覆盖与限制

- 未在真实日历客户端（Calendar.app／Outlook 等）执行导入。验收要求的“客户端展示”以属性级解析验证替代：直读 `DTSTART`／`DTEND`／`UID`／`RRULE`，覆盖时区与跨日语义；真实客户端导入标记为未验证。
- 未覆盖部分客户端对 `X-CAMPUSFLOW-*` 提示属性的忽略行为；这些属性不参与事件语义。
- 计划块跨 DST 的时间点由存储的带时区时间换算 UTC，测试未单独构造 DST 切换当天的墙上歧义时间；相关规则见 `docs/export-scope.md` §6。
