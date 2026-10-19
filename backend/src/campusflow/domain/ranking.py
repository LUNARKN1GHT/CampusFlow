"""Q006：关键词、向量与混合排序基线。

排序只消费 Q005 已按空间和适用范围过滤的候选；输入相同则顺序稳定。
关键词分数用于课程名、课程编号与日期等精确线索；向量分数只在同一模型配置内比较。
混合排序使用倒数排名融合（RRF），不把两种不可比的分数量纲直接相加。
"""

import math
import re
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum

from campusflow.domain.embeddings import EmbeddingProfile, cosine_similarity
from campusflow.domain.errors import DomainError
from campusflow.domain.retrieval import RetrievalCandidate

DEFAULT_TOP_K = 8
MAX_TOP_K = 50
RRF_K = 60

_TOKEN = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+")


class RankingMode(StrEnum):
    KEYWORD = "keyword"
    VECTOR = "vector"
    HYBRID = "hybrid"


@dataclass(frozen=True)
class RankedCandidate:
    """排序结果；keyword/vector 分列保留便于评估，``score`` 只在本次排序模式下有意义。"""

    candidate: RetrievalCandidate
    keyword_score: float
    vector_score: float | None
    score: float
    rank: int


def _validate_query(query: str) -> str:
    if not isinstance(query, str) or not query.strip():
        raise DomainError("检索问题不能为空")
    if len(query) > 2000:
        raise DomainError("检索问题不能超过 2000 字")
    return query.strip()


def keyword_terms(text: str) -> tuple[str, ...]:
    """中文按二元字组、拉丁字母和数字按词切分，不依赖外部分词器。"""
    terms = []
    for token in _TOKEN.findall(text.casefold()):
        if token.isascii():
            if len(token) >= 2:
                terms.append(token)
        elif len(token) == 1:
            terms.append(token)
        else:
            terms.extend(token[index : index + 2] for index in range(len(token) - 1))
    return tuple(terms)


def keyword_score(query: str, text: str) -> float:
    """可复现的覆盖式打分：问题词命中、覆盖比例与整句命中各占一部分。"""
    query_terms = keyword_terms(_validate_query(query))
    if not query_terms:
        return 0.0
    text_terms = Counter(keyword_terms(text))
    unique_query = set(query_terms)
    matched = unique_query & set(text_terms)
    if not matched:
        return 0.0
    # 单词重复命中最多计 5 次，避免长文档靠堆词而不是覆盖问题要点拿高分。
    weighted_hits = sum(min(text_terms[term], 5) * (1 + 0.25 * (len(term) - 1)) for term in matched)
    coverage = len(matched) / len(unique_query)
    exact = 1.0 if query.casefold().strip() in text.casefold() else 0.0
    return weighted_hits / len(query_terms) + coverage + exact


def _candidate_key(candidate: RetrievalCandidate) -> tuple[int, int, str]:
    """同分时的兜底排序键：资料、版本、块编号，保证重复调用顺序一致。"""
    return (candidate.material_id, candidate.block.version_id, candidate.block.id)


def _rank_order(scores: dict[str, float], candidates: list[RetrievalCandidate]) -> list[str]:
    return [
        candidate.block.id
        for candidate in sorted(
            candidates,
            key=lambda item: (-scores[item.block.id], _candidate_key(item)),
        )
    ]


def rank_candidates(
    candidates: tuple[RetrievalCandidate, ...] | list[RetrievalCandidate],
    query: str,
    *,
    mode: RankingMode = RankingMode.HYBRID,
    limit: int = DEFAULT_TOP_K,
    query_embedding=None,
    profile: EmbeddingProfile | None = None,
) -> tuple[RankedCandidate, ...]:
    """返回排序后的前 ``limit`` 个候选；相同输入必须得到相同顺序。

    向量/混合模式必须显式提供同一配置的查询向量。没有候选时不调用模型，
    返回空元组，由回答层说明“现有资料中未发现”。
    """
    query = _validate_query(query)
    if not isinstance(mode, RankingMode):
        raise DomainError("检索排序方式无效")
    if type(limit) is not int or not 1 <= limit <= MAX_TOP_K:
        raise DomainError(f"检索返回数量必须为 1 至 {MAX_TOP_K}")
    items = list(candidates)
    if not items:
        return ()
    if mode in (RankingMode.VECTOR, RankingMode.HYBRID):
        if query_embedding is None or profile is None:
            raise DomainError("向量排序必须提供同一配置的查询向量")

    keyword_scores = {item.block.id: keyword_score(query, item.block.text) for item in items}
    vector_scores: dict[str, float] | None = None
    if mode in (RankingMode.VECTOR, RankingMode.HYBRID):
        vector_scores = {
            item.block.id: cosine_similarity(query_embedding, item.embedding, profile)
            for item in items
        }

    if mode == RankingMode.KEYWORD:
        scores = keyword_scores
    elif mode == RankingMode.VECTOR:
        scores = vector_scores
    else:
        # 混合模式用 RRF 比较名次而不是原始分：关键词分与余弦分不可通约，不能直接相加。
        keyword_order = _rank_order(keyword_scores, items)
        vector_order = _rank_order(vector_scores, items)
        keyword_rank = {block_id: rank for rank, block_id in enumerate(keyword_order, 1)}
        vector_rank = {block_id: rank for rank, block_id in enumerate(vector_order, 1)}
        scores = {
            block_id: 1 / (RRF_K + keyword_rank[block_id]) + 1 / (RRF_K + vector_rank[block_id])
            for block_id in keyword_rank
        }

    ordered = sorted(items, key=lambda item: (-scores[item.block.id], _candidate_key(item)))
    return tuple(
        RankedCandidate(
            candidate=item,
            keyword_score=keyword_scores[item.block.id],
            vector_score=None if vector_scores is None else vector_scores[item.block.id],
            score=scores[item.block.id],
            rank=rank,
        )
        for rank, item in enumerate(ordered[:limit], 1)
    )


def normalized_relevance(score: float, scores: list[float]) -> float:
    """把排序分数映射到 0..1，仅用于展示和评估，不跨模式比较。"""
    if not scores or not math.isfinite(score):
        return 0.0
    low, high = min(scores), max(scores)
    if high == low:
        return 1.0
    return (score - low) / (high - low)
