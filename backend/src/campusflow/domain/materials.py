"""资料领域规则（D001）。"""


def next_version_no(existing: list[int]) -> int:
    """下一版本号：已有版本最大号 + 1，无版本时从 1 开始。"""
    return max(existing, default=0) + 1
