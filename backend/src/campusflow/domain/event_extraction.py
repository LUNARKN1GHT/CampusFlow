"""固定活动原文的保守识别，不计算日期、不生成日程。"""

import re
import unicodedata

from campusflow.domain.candidates import ReviewReason
from campusflow.domain.errors import DomainError

# 截止类动词与活动类动词分开匹配：同句同时命中说明是混合要求，交回人工拆分。
_DEADLINE = re.compile(r"截止|截至|提交|上交|交付|交作业|交报告|报名|预约|完成|(?:之?前|以前)\s*交")
_EVENT = re.compile(
    r"举行|举办|召开|开考|开讲|开课|上课|考试|测验|讲座|研讨会|答辩|"
    r"会议|实验课|面试|培训|典礼|运动会|比赛|活动"
)
_CLOCK = re.compile(r"(?<![0-9:])([0-9]{1,2}):([0-9]{2})(?![0-9:])")
_CHINESE_CLOCK = re.compile(r"(?<![0-9])([0-9]{1,2})[点时](?:([0-9]{1,2})分|半)?(?![0-9])")


def require_event_statement(text: str) -> None:
    """拒绝提交截止要求或无法识别的活动描述，不静默丢掉混合通知中的事项。"""
    value = unicodedata.normalize("NFKC", text)
    if _DEADLINE.search(value):
        raise DomainError("活动描述混入提交、完成或报名截止要求，请拆分原文后核对")
    if not _EVENT.search(value):
        raise DomainError("原文缺少可识别的固定活动描述，不能把截止要求当作活动")


def require_event_context(statement: str, sources: list[str]) -> None:
    """检查完整原文语句，防止只截取活动名而隐藏同句的提交要求。"""
    separators = r"[。！？；;\n]"
    for source in sources:
        for match in re.finditer(re.escape(statement), source):
            before = re.split(separators, source[: match.start()])[-1]
            after = (
                ""
                if re.search(separators + r"$", statement)
                else re.split(separators, source[match.end() :])[0]
            )
            try:
                require_event_statement(before + statement + after)
            except DomainError:
                continue
            return
    raise DomainError("活动证据的完整语句无法排除提交截止或混合要求，请拆分后核对")


def event_time_reasons(value: str, field: str) -> tuple[ReviewReason, ...]:
    """保留原文；只有时刻也不自动继承另一端日期，复杂写法提示核对。"""
    text = unicodedata.normalize("NFKC", value)
    clocks = list(_CLOCK.finditer(text)) + list(_CHINESE_CLOCK.finditer(text))
    valid = [m for m in clocks if int(m[1]) < 24 and int(m[2] or 0) < 60]
    reasons = []
    if not valid or len(valid) != len(clocks) or len(clocks) != 1:
        reasons.append(
            ReviewReason("uncertain_event_time", "时间缺少明确单一时刻或写法无效，需核对", field)
        )
    # 不转换日期，不使用发布年份，不给结束端偷偷补入开始端的日期。
    if not re.search(r"[0-9]{4}\s*(?:年|[-/.])", text):
        reasons.append(
            ReviewReason("event_date_context", "时间缺少明确年份，需核对日期上下文", field)
        )
    if re.search(r"明天|下周|本周|周[一二三四五六日天]|第.+周|上午|下午|晚上|左右|暂定", text):
        reasons.append(
            ReviewReason("event_time_context", "相对或限定时间保留原文，需核对依据", field)
        )
    return tuple(reasons)
