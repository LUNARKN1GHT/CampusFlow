"""M8：培养方案、课程关系与修读路径的纯业务规则。

本模块不依赖 Web 框架或数据库。关系分析只接受当前方案内已确认的数据；
缺失信息必须显式列出，不能用推测补造完整顺序。
"""

from enum import StrEnum

from campusflow.domain.errors import DomainError


class RelationType(StrEnum):
    """三类课程关系分开建模；只有强制先修参与拓扑排序。"""

    PREREQUISITE = "prerequisite"  # 强制先修
    COREQUISITE = "corequisite"  # 并修
    RECOMMENDED = "recommended"  # 建议先学


class StudyStatus(StrEnum):
    """个人修读状态；未知状态由“没有记录”表达，不落库成猜测值。"""

    COMPLETED = "completed"
    IN_PROGRESS = "in_progress"
    PLANNED = "planned"


class OfferedTerm(StrEnum):
    """培养方案给出的开课学期；未知时使用空列表，不假定每学期都开。"""

    FALL = "fall"
    SPRING = "spring"
    SUMMER = "summer"
    WINTER = "winter"


OFFERED_TERM_LABELS = {
    OfferedTerm.FALL: "秋季",
    OfferedTerm.SPRING: "春季",
    OfferedTerm.SUMMER: "夏季",
    OfferedTerm.WINTER: "冬季",
}


def _text(value: str | None, limit: int, label: str, *, required: bool = False) -> None:
    if value is None:
        if required:
            raise DomainError(f"{label}不能为空")
        return
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise DomainError(f"{label}为空、过长或含无效字符")


def validate_grade_year(value: int | None) -> None:
    if value is not None and (type(value) is not int or not 1900 <= value <= 2100):
        raise DomainError("入学年级必须是 1900 至 2100 之间的年份")


def validate_plan_fields(
    *,
    title: str,
    major: str,
    grade_year: int,
    version_label: str,
    credit_limit_per_term: int | None,
) -> None:
    _text(title, 200, "方案名称", required=True)
    _text(major, 100, "专业", required=True)
    validate_grade_year(grade_year)
    _text(version_label, 100, "方案版本", required=True)
    if credit_limit_per_term is not None and (
        type(credit_limit_per_term) is not int or credit_limit_per_term <= 0
    ):
        raise DomainError("每学期学分上限必须是正整数")


def validate_profile_fields(
    *,
    major: str | None,
    grade_year: int | None,
    plan_major: str | None,
    plan_grade_year: int | None,
) -> None:
    _text(major, 100, "专业")
    validate_grade_year(grade_year)
    # 专业与年级允许暂缺，但若与方案声明明显不同则拒绝，避免混用不同年级规则。
    if major is not None and plan_major is not None and major.strip() != plan_major.strip():
        raise DomainError("个人专业与所选方案专业不一致，请核对后保存")
    if (
        grade_year is not None
        and plan_grade_year is not None
        and grade_year != plan_grade_year
    ):
        raise DomainError("个人年级与所选方案年级不一致，请核对后保存")


def validate_course_fields(
    *, name: str, code: str | None, credits: float | None, offered_terms: list[str]
) -> list[str]:
    _text(name, 200, "课程名称", required=True)
    _text(code, 50, "课程代码")
    if credits is not None and (
        type(credits) not in (int, float) or credits < 0 or credits > 100
    ):
        raise DomainError("学分必须是非负数")
    normalized: list[str] = []
    for term in offered_terms:
        try:
            value = OfferedTerm(term)
        except ValueError:
            raise DomainError("开课学期只能是 fall、spring、summer 或 winter") from None
        if value.value not in normalized:
            normalized.append(value.value)
    return normalized


def validate_relation_type(value: str) -> RelationType:
    try:
        return RelationType(value)
    except ValueError:
        raise DomainError("关系类型只能是强制先修、并修或建议先学") from None


def validate_relation_fields(*, relation_type: str, condition_text: str | None) -> RelationType:
    parsed = validate_relation_type(relation_type)
    _text(condition_text, 1000, "关系说明")
    return parsed


def profile_missing_fields(
    *, major: str | None, grade_year: int | None, plan_id: int | None
) -> tuple[str, ...]:
    """缺失项按稳定顺序返回；空元组表示个人方案信息完整。"""
    missing = []
    if major is None:
        missing.append("major")
    if grade_year is None:
        missing.append("grade_year")
    if plan_id is None:
        missing.append("curriculum_plan_id")
    return tuple(missing)
