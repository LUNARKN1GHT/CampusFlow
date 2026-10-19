"""候选读取必须先按空间及适用范围过滤，不能先全库排序后截取。"""

from typing import Protocol

from campusflow.domain.citations import Citation, RevalidatedCitation
from campusflow.domain.retrieval import RetrievalScope, ScopedCandidates


class RetrievalCandidates(Protocol):
    """问答候选来源；范围条件必须先进入查询，不能先全库排序再截取。"""

    def collect(
        self, workspace_id: int, scope: RetrievalScope, profile_id: str
    ) -> ScopedCandidates:
        """读取当前有效候选；只含非归档资料、当前版本与成功索引块。

        空结果表示“现有资料中没有可检索内容”，不等于资料未导入；
        该方法只读、不调用模型，也不得隐式创建索引或修改资料状态。
        """
        ...


class CitationSources(Protocol):
    """引用复核；每次读取都按当前资料状态重新判断，不能沿用旧结论。"""

    def revalidate(
        self, workspace_id: int, citations: tuple[Citation, ...] | list[Citation]
    ) -> tuple[RevalidatedCitation, ...]:
        """逐条返回删除、归档、版本被替代或原文改写状态；只读、不修复引用。"""
        ...
