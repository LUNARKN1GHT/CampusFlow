"""学期领域规则：只校验学期自身的日期范围。

归档状态与关联数据由应用层和存储层维护；归档只切换标记，
不删除或改写课程、任务、进度与日程。
"""

from datetime import date


def validate_semester_range(start: date, end: date) -> str | None:
    """学期日期范围校验，合法返回 None，否则返回中文错误信息。

    允许开始与结束为同一天，不要求学期必须覆盖整周或整月。
    """
    if end < start:
        return "学期结束日期不得早于开始日期"
    return None
