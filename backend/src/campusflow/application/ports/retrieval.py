"""候选读取必须先按空间及适用范围过滤，不能先全库排序后截取。"""

from typing import Protocol

from campusflow.domain.retrieval import RetrievalScope, ScopedCandidates


class RetrievalCandidates(Protocol):
    def collect(
        self, workspace_id: int, scope: RetrievalScope, profile_id: str
    ) -> ScopedCandidates: ...
