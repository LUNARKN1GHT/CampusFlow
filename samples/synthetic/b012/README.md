# B012 日期、范围与冲突样本

本目录的六份纯文本通知均为合成资料，对应人工预期见 `expected.json`。

| 场景 | 输入 | 核心预期 |
| --- | --- | --- |
| 有依据的相对日期 | `relative-date.txt` | 使用发布时间、星期和时区换算，并展示依据 |
| 年份与时刻缺失 | `missing-year-time.txt` | 保留月日，年份和时刻进入待核对 |
| 同名不同班 | `same-course-section-a.txt`、`same-course-section-b.txt` | 按教学班保持两条独立要求 |
| 来源冲突 | `conflict-notice-v1.txt`、`conflict-notice-v2.txt` | 并列展示差异；无替代声明时不选定生效版本 |

这些样本用于验证系统是否保持事实边界，不用于证明相应功能已经实现。
