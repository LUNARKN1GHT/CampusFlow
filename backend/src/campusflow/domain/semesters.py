"""学期领域规则。"""

from datetime import date


def validate_semester_range(start: date, end: date) -> str | None:
    """学期日期范围校验，合法返回 None，否则返回中文错误信息。"""
    if end < start:
        return "学期结束日期不得早于开始日期"
    return None
