"""N010 记录关系和发布信息快照；不改变任务、规则、版本或索引的生效状态。"""

from dataclasses import dataclass

from campusflow.application.ports.repositories import Repositories
from campusflow.application.ports.version_relations import (
    VersionContext,
    VersionRelationData,
    VersionRelations,
)
from campusflow.domain.errors import DomainError, NotFoundError
from campusflow.domain.version_relations import (
    VersionRelationKind,
    compare_scope,
    relation_key,
    validate_statement,
)


@dataclass(frozen=True)
class RelationView:
    relation: VersionRelationData
    current_scope_comparison: str
    metadata_changed: bool
    source_archived: bool
    target_archived: bool
    # 此阶段只记录用户声明；不同范围和未知范围绝不视为直接替代。
    replacement_effective: bool = False
    review_required: bool = True
    origin: str = "user"


def snapshot(context: VersionContext) -> dict:
    material, version = context.material, context.version
    # 版本原有发布字段单独保留；不把导入时间或 material 的后续修订冒充原发布信息。
    metadata = version.source_metadata
    publication = {
        key: metadata[key]
        for key in ("publisher", "published_at", "published_text")
        if key in metadata and (metadata[key] is None or isinstance(metadata[key], str))
    }
    return {
        "material_id": material.id,
        "version_id": version.id,
        "version_no": version.version_no,
        "title": material.title,
        "semester_id": material.semester_id,
        "course_id": material.course_id,
        "class_name": material.class_name,
        "publisher": material.publisher,
        "published_at": material.published_at.isoformat() if material.published_at else None,
        "version_publication": publication,
    }


def _view(
    relation: VersionRelationData, source: VersionContext, target: VersionContext
) -> RelationView:
    current_source, current_target = snapshot(source), snapshot(target)
    return RelationView(
        relation,
        compare_scope(current_source, current_target),
        current_source != relation.source_snapshot or current_target != relation.target_snapshot,
        source.material.archived,
        target.material.archived,
    )


def record_relation(
    repos: Repositories,
    store: VersionRelations,
    workspace_id: int,
    *,
    source_version_id: int,
    target_version_id: int,
    kind: VersionRelationKind,
    statement: str,
    actor: str,
) -> tuple[RelationView, bool]:
    source_id, target_id = relation_key(source_version_id, target_version_id, kind)
    statement = validate_statement(statement, actor)
    # 同空间创建串行化，避免重复消息并发产生重复关联；再按编号锁资料/版本。
    store.lock_workspace(workspace_id)
    contexts = {
        key: store.context(workspace_id, key, lock=True) for key in sorted((source_id, target_id))
    }
    source, target = contexts[source_id], contexts[target_id]
    if source is None or target is None:
        raise NotFoundError("资料版本不存在")
    if source.material.archived or target.material.archived:
        raise DomainError("资料已归档，请恢复后再建立关联")
    existing = store.find(workspace_id, source_id, target_id, kind)
    if existing is not None:
        if existing.statement != statement:
            raise DomainError("这两个版本已有不同的关联声明，不能覆盖原记录")
        result = _view(existing, source, target)
        repos.uow.commit()
        return result, False
    relation = store.create(
        workspace_id,
        source_id,
        target_id,
        kind,
        statement,
        actor,
        snapshot(source),
        snapshot(target),
    )
    result = _view(relation, source, target)
    repos.uow.commit()
    return result, True


def list_relations(
    store: VersionRelations, workspace_id: int, version_id: int
) -> list[RelationView]:
    if store.context(workspace_id, version_id) is None:
        raise NotFoundError("资料版本不存在")
    result = []
    for relation in store.list_for_version(workspace_id, version_id):
        source = store.context(workspace_id, relation.source_version_id)
        target = store.context(workspace_id, relation.target_version_id)
        # 即使数据被管理工具移动到其他空间，也不能泄露之前保存的快照。
        if source is not None and target is not None:
            result.append(_view(relation, source, target))
    return result
