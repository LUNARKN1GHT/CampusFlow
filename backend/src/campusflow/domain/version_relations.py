"""N010 用户声明的同源/替代关系；记录声明不等于接受替代。

本模块不看导入时间或版本号大小，声明本身也不判定哪一版生效；
生效判断还需替代说明、发布时间与适用范围，留待接受流程处理。
"""

from enum import StrEnum

from campusflow.domain.errors import DomainError


class VersionRelationKind(StrEnum):
    """用户声明的两种版本关系；声明只表示关联意图，不等于已接受替代或判定生效。"""

    SAME_SOURCE = "same_source"
    REPLACEMENT_CLAIM = "replacement_claim"


def relation_key(source_id: int, target_id: int, kind: VersionRelationKind) -> tuple[int, int]:
    """生成去重键；同源关系无方向，替代声明保留 source → target 的方向。"""
    if any(type(value) is not int or value < 1 for value in (source_id, target_id)):
        raise DomainError("版本编号无效")
    if source_id == target_id:
        raise DomainError("不能把同一个版本关联为自己的新旧版本")
    if not isinstance(kind, VersionRelationKind):
        raise DomainError("版本关联类型无效")
    # 同源关系不区分方向；替代声明由 source 指向它声明替代的 target。
    return (
        tuple(sorted((source_id, target_id)))
        if kind == VersionRelationKind.SAME_SOURCE
        else (source_id, target_id)
    )


def validate_statement(statement: str, actor: str) -> str:
    """关联声明必须有可读依据与操作者；两者都只去首尾空白，不改写内容。"""
    if not isinstance(statement, str) or not 1 <= len(statement.strip()) <= 1000:
        raise DomainError("请填写 1 至 1000 字的同源或替代依据说明")
    if not isinstance(actor, str) or not 1 <= len(actor.strip()) <= 200:
        raise DomainError("必须记录关联声明的操作者")
    return statement.strip()


def compare_scope(source: dict, target: dict) -> str:
    """缺失不是通配范围；已知字段有差异优先报告不同，其余缺失报告未知。"""
    keys = ("semester_id", "course_id", "class_name")

    def known(value: object) -> bool:
        return value is not None and (not isinstance(value, str) or bool(value.strip()))

    if any(
        known(source.get(key)) and known(target.get(key)) and source[key] != target[key]
        for key in keys
    ):
        return "different"
    if any(not known(source.get(key)) or not known(target.get(key)) for key in keys):
        return "unknown"
    return "same"
