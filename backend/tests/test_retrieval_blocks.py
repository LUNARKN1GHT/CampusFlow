"""Q003：精确来源范围、UTF-8 上限、重叠身份和授权入口。"""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from campusflow.application.retrieval_blocks import build_version_blocks
from campusflow.domain.chunking import (
    ChunkingConfig,
    ChunkSource,
    SourceLocation,
    split_source,
    split_sources,
)
from campusflow.domain.errors import DomainError, NotFoundError


def source(text, **changes):
    return replace(
        ChunkSource(1, 10, 0, text, SourceLocation("paragraph", "1", paragraph=1)), **changes
    )


def assert_exact_coverage(original, blocks, config):
    covered = [False] * len(original.text)
    for block in blocks:
        block.validate_against(original)
        assert 0 < len(block.text.encode("utf-8")) <= config.max_bytes
        for i in range(block.start, block.end):
            covered[i] = True
    assert all(covered)
    assert len({block.id for block in blocks}) == len(blocks)


@pytest.mark.parametrize(
    "text",
    [
        "短句。",
        "\n  空白与换行\r\n不能丢。\t",
        "中" * 4000,
        "abc" * 4000,
        "😀e\u0301中文。\n" * 1200,
        "a.\nb!c?d。中文；" * 800,
        " " * 4000,
        "",
    ],
    ids=["short", "whitespace", "chinese", "ascii", "unicode", "sentences", "spaces", "empty"],
)
@pytest.mark.parametrize("config", [ChunkingConfig(), ChunkingConfig(40, 3), ChunkingConfig(4)])
def test_split_covers_every_character_without_exceeding_bytes(text, config):
    original = source(text)
    blocks = split_source(original, config)
    assert_exact_coverage(original, blocks, config)
    assert blocks == split_source(original, config)
    if not config.overlap_chars:
        assert "".join(block.text for block in blocks) == text


def test_long_source_prefers_sentence_boundary_but_short_source_stays_whole():
    original = source("第一句。第二句非常长，超过本次限制。")
    blocks = split_source(original, ChunkingConfig(20))
    assert blocks[0].text == "第一句。"
    assert split_source(original)[0].text == original.text


def test_overlap_points_to_same_original_range_and_text():
    original = source("abcdefghijklmnopqrstuvxyz")
    blocks = split_source(original, ChunkingConfig(16, 2))
    assert blocks[0].end - blocks[1].start == 2
    assert blocks[0].text[-2:] == blocks[1].text[:2]
    assert blocks[0].source_chunk_id == blocks[1].source_chunk_id
    assert blocks[0].source_fingerprint == blocks[1].source_fingerprint
    assert blocks[0].id != blocks[1].id
    assert_exact_coverage(original, blocks, ChunkingConfig(16, 2))


@pytest.mark.parametrize(
    "location",
    [
        SourceLocation("page", "3", page=3),
        SourceLocation("region", "0.1,0.2,0.8,0.9", page=2, bbox=(0.1, 0.2, 0.8, 0.9)),
        SourceLocation("table_cell", "t1:r2:c3:p1", table_group="table-1"),
        SourceLocation("web", "table-3:r1:c2"),
        SourceLocation("document", "body"),
    ],
)
def test_locator_is_copied_without_inventing_pages_or_merging_cells(location):
    first = source("原文一", location=location)
    second = source("原文二", id=2, seq=1, location=replace(location, value="second"))
    blocks = split_sources([second, first])
    assert len(blocks) == 2
    assert blocks[0].location == first.location and blocks[1].location == second.location
    assert blocks[0].text == "原文一" and blocks[1].text == "原文二"


def test_identity_changes_on_version_text_or_location_but_not_repeat_or_same_span_config():
    original = source("短段落")
    block = split_source(original)[0]
    assert block.id == split_source(original, ChunkingConfig(100))[0].id
    for updated in (
        replace(original, version_id=11),
        replace(original, text="新段落"),
        replace(original, location=SourceLocation("paragraph", "2", paragraph=2)),
    ):
        assert split_source(updated)[0].id != block.id
        with pytest.raises(DomainError):
            block.validate_against(updated)


def test_duplicate_source_is_deduplicated_but_conflicting_identity_rejected():
    original = source("原文")
    assert split_sources([original, original]) == split_sources([original])
    for conflicting in (
        replace(original, text="不同原文"),
        replace(original, version_id=20),
        replace(original, id=2),
    ):
        with pytest.raises(DomainError):
            split_sources([original, conflicting])


@pytest.mark.parametrize(
    "changes",
    [
        {"start": -1},
        {"end": 100},
        {"text": "伪造"},
        {"id": "wrong"},
        {"source_fingerprint": "wrong"},
        {"chunker_version": "other"},
    ],
)
def test_modified_block_cannot_claim_original_reference(changes):
    original = source("原文内容")
    with pytest.raises(DomainError):
        replace(split_source(original)[0], **changes).validate_against(original)


@pytest.mark.parametrize(
    "changes",
    [
        {"max_bytes": 3},
        {"max_bytes": 3001},
        {"max_bytes": True},
        {"overlap_chars": -1},
        {"overlap_chars": 1000},
        {"overlap_chars": True},
    ],
)
def test_invalid_chunking_limits(changes):
    with pytest.raises(DomainError):
        ChunkingConfig(**changes)


def test_missing_or_invalid_source_is_rejected():
    for original in (
        source("\ud800"),
        source("text", id=0),
        source("text", seq=-1),
        source("text", location=SourceLocation("unknown", "1")),
    ):
        with pytest.raises(DomainError):
            split_source(original)


def test_application_checks_workspace_before_reading_chunks():
    repos = Mock()
    repos.materials.get.return_value = Mock(workspace_id=2)
    with pytest.raises(NotFoundError):
        build_version_blocks(repos, 1, 10, 1)
    repos.materials.list_chunks.assert_not_called()


def test_application_uses_requested_version_and_preserves_saved_coordinates():
    repos = Mock()
    repos.materials.get.return_value = Mock(workspace_id=1)
    repos.materials.list_versions.return_value = [
        Mock(version_no=1, id=10),
        Mock(version_no=2, id=20),
    ]
    repos.materials.list_chunks.return_value = [
        Mock(
            id=5,
            version_id=20,
            seq=0,
            text="表格原文",
            locator_type="table_cell",
            locator_value="t1:r2:c3",
            page=None,
            paragraph=None,
            bbox=None,
            table_group="t1",
        )
    ]
    blocks = build_version_blocks(repos, 1, 2, 2)
    repos.materials.list_chunks.assert_called_once_with(20)
    assert blocks[0].version_id == 20 and blocks[0].source_chunk_id == 5
    assert blocks[0].location.value == "t1:r2:c3" and blocks[0].location.page is None
    with pytest.raises(NotFoundError):
        build_version_blocks(repos, 1, 2, 3)


def test_persisted_versions_build_distinct_blocks_without_altering_sources(client):
    from types import SimpleNamespace

    from conftest import TEST_DATABASE_URL
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from campusflow.application.materials import fragments_to_chunks
    from campusflow.application.ports.parsers import ParsedFragment, ParseOutcome, ParserLocator
    from campusflow.infrastructure.repositories import SqlAlchemyMaterialRepository

    workspace_id = client.get("/api/v1/settings").json()["workspace_id"]
    imported = client.post(
        "/api/v1/materials/text",
        json={"title": "Q003 合成样本", "content": "旧版第一段。\n\n旧版第二段。"},
    )
    assert imported.status_code == 201
    material_id = imported.json()["material"]["id"]
    engine = create_engine(TEST_DATABASE_URL)
    with Session(engine) as session:
        material_repo = SqlAlchemyMaterialRepository(session)
        repos = SimpleNamespace(materials=material_repo)
        first = build_version_blocks(repos, workspace_id, material_id, 1)
        version = material_repo.create_version(material_id, 2, "新版", None)
        material_repo.add_chunks(
            fragments_to_chunks(
                ParseOutcome(
                    [ParsedFragment(0, ParserLocator(kind="paragraph", paragraph=1), "新版原文。")],
                    [],
                ),
                version.id,
            )
        )
        session.commit()
        second = build_version_blocks(repos, workspace_id, material_id, 2)
        assert first == build_version_blocks(repos, workspace_id, material_id, 1)
        assert [block.text for block in first] == ["旧版第一段。", "旧版第二段。"]
        assert second[0].text == "新版原文。"
        assert {b.id for b in first}.isdisjoint({b.id for b in second})
        with pytest.raises(NotFoundError):
            build_version_blocks(repos, workspace_id + 100, material_id, 1)
    engine.dispose()
