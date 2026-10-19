"""X007：课程名/教学班严格匹配，不做简称、模糊或跨学期猜测。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CourseOption:
    id: int
    name: str
    class_name: str | None
    code: str | None = None
    teacher: str | None = None


@dataclass(frozen=True)
class CourseMatch:
    """匹配结果；status 为 missing_name/unmatched/class_mismatch/ambiguous/unique_candidate。

    只有 unique_candidate 带建议课程，且仍只是待核对建议，不直接改写候选范围。
    """

    status: str
    suggested_course_id: int | None
    matches: tuple[CourseOption, ...]
    options: tuple[CourseOption, ...]
    reason: str


def match_course(
    courses: list[CourseOption], course_name: str | None, class_name: str | None
) -> CourseMatch:
    """调用方先筛选授权学期。本函数只忽略首尾空白，保留大小写和内部字符。"""
    catalog = tuple(sorted(courses, key=lambda course: course.id))
    name = (course_name or "").strip()
    section = (class_name or "").strip()
    if not name:
        return CourseMatch("missing_name", None, (), catalog, "缺少课程名，不能仅凭班级推定课程")
    same_name = tuple(course for course in catalog if course.name.strip() == name)
    if not same_name:
        return CourseMatch("unmatched", None, (), catalog, "所选学期没有同名课程，请核对或手动选择")
    matches = (
        tuple(course for course in same_name if (course.class_name or "").strip() == section)
        if section
        else same_name
    )
    if not matches:
        return CourseMatch(
            "class_mismatch",
            None,
            (),
            same_name,
            "同名课程的教学班不符或尚未记录，不能忽略班级要求",
        )
    if len(matches) > 1:
        return CourseMatch(
            "ambiguous", None, matches, matches, "有多个符合条件的课程记录，请保留原文并人工选择"
        )
    reason = "仅一个符合条件的课程记录，仍须核对原文及适用范围"
    if not section and matches[0].class_name:
        reason += "；原文未明确教学班，不能把现有记录当作班级依据"
    return CourseMatch("unique_candidate", matches[0].id, matches, matches, reason)
