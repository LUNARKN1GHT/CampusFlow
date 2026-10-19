"""提醒幂等键（A012）。

键由事项、规则版本和触发窗口组成；同一身份在重试、重复调度周期或并发
同步中只对应一条触发记录。键长度受限，超长时附加内容哈希而不是简单截断。
"""

from __future__ import annotations

from datetime import date, datetime, time
from hashlib import sha256

MAX_DEDUPE_KEY_LENGTH = 200


def _bounded(value: str) -> str:
    """限制键长度；超长时保留可读前缀并追加内容哈希，避免截断造成碰撞。"""
    if len(value) <= MAX_DEDUPE_KEY_LENGTH:
        return value
    digest = sha256(value.encode("utf-8")).hexdigest()[:32]
    return f"{value[: MAX_DEDUPE_KEY_LENGTH - 33]}:{digest}"


def deadline_dedupe_key(
    *,
    task_id: int,
    rule_id: int | None,
    rule_version: int,
    due_date: date,
    due_time: time | None,
    window_start: datetime,
) -> str:
    """截止提醒的幂等键：同一任务、规则（含版本）、截止精度与触发窗口只保留一条。

    window_start 换算成秒级时间戳，同一瞬间的不同时区写法得到同一个键。
    rule_id 为 None 表示尚未保存默认规则、使用内置默认值；只有日期的截止
    用 ``date`` 而不是补 09:00，保持日期精度且不与真实时刻混淆。
    """
    rule = rule_id if rule_id is not None else "default"
    precision = due_time.isoformat() if due_time is not None else "date"
    return _bounded(
        f"deadline:{task_id}:{rule}:{rule_version}:"
        f"{due_date.isoformat()}:{precision}:{int(window_start.timestamp())}"
    )


def task_review_dedupe_key(*, task_id: int, due_date: date, rule_version: int) -> str:
    """来源失效的待核对提醒键；不含触发窗口，同一规则版本只留一条。"""
    return _bounded(f"review-task:{task_id}:{due_date.isoformat()}:{rule_version}")


def workspace_review_dedupe_key(*, day: date) -> str:
    """工作空间级待核对提醒键；按天去重，重复同步不会新增第二条。"""
    return _bounded(f"review:pending:{day.isoformat()}")
