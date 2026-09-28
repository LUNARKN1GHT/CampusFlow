"""日期与截止时间规则。

只有日期、没有时刻的 DDL 保持日期精度：展示和排期都不得把
`2026-10-15` 自动补成 `00:00` 或 `23:59` 的确定时刻，只能按"当天内完成"处理。
"""

from datetime import date, time


def validate_due(due_date: date | None, due_time: time | None) -> str | None:
    """校验截止时间组合，合法返回 None，否则返回中文错误信息。"""
    if due_time is not None and due_date is None:
        return "设定截止时刻时必须同时提供截止日期"
    return None
