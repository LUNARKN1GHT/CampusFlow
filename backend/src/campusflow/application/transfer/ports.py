"""P010：迁移包用例依赖的端口（归档读写、数据库工具、迁移执行）。

应用层只调用这些接口；具体实现位于 `infrastructure/transfer/`。
端口显式接收连接地址，便于验收时把"目标库"换成临时数据库。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from campusflow.application.transfer.manifest import ArchiveManifest


@dataclass(frozen=True)
class TargetDatabase:
    """本机目标数据库的连接信息；名称与角色用于建库、改名与切换。"""

    name: str
    role: str
    # 应用连接（含驱动），用于计数、导出与恢复
    url: str
    # 维护连接（连到 postgres 库），用于 CREATE/ALTER/DROP DATABASE
    admin_url: str


class ArchiveStore(Protocol):
    """`.campusflow`（ZIP）的读写；实现负责原子写入、路径校验与摘要核对。"""

    def write(
        self,
        target: Path,
        manifest: ArchiveManifest,
        database_path: Path,
        materials: Sequence[tuple[str, Path]],
    ) -> None:
        """把清单、逻辑备份与资料文件写成一个完整归档。

        必须先写临时文件、fsync 后再原子改名为 `target`；失败时不留半成品。
        条目顺序固定（manifest.json → database.dump → materials/… 按路径排序）。
        """
        ...

    def read(self, archive: Path) -> ArchiveManifest:
        """读取并校验清单结构；ZIP 结构损坏、缺清单或路径越界时抛出格式错误。"""
        ...

    def verify(self, archive: Path, manifest: ArchiveManifest) -> None:
        """逐条核对清单声明的文件大小与 SHA-256，任何不符都拒绝整包。"""
        ...

    def extract_database(self, archive: Path, manifest: ArchiveManifest, destination: Path) -> None:
        """解出并核对逻辑备份；摘要不符时不保留解出的文件。"""
        ...

    def extract_materials(
        self, archive: Path, manifest: ArchiveManifest, destination: Path
    ) -> None:
        """把资料文件解到临时目录并核对摘要；任何不符都中止（由调用方清理）。"""
        ...


class DatabaseTools(Protocol):
    """数据库侧的 pg_dump/pg_restore 与建库、切换操作。"""

    def table_names(self, url: str, names: Sequence[str]) -> frozenset[str]:
        """返回这些表里实际存在的表名（区分迁移过与未迁移的数据库）。"""
        ...

    def count_rows(self, url: str, tables: Mapping[str, str]) -> dict[str, int]:
        """按"清单键 -> 表名"返回条目数；不存在的表按 0 处理。"""
        ...

    def stored_material_keys(self, url: str) -> list[str]:
        """数据库引用的原文件存储键（用于发现缺失文件）。"""
        ...

    def current_revision(self, url: str) -> str | None:
        """当前 Alembic 版本号；未迁移或没有版本表时返回 None。"""
        ...

    def dump_database(
        self, url: str, destination: Path, *, exclude_table_data: Sequence[str]
    ) -> None:
        """执行 `pg_dump -Fc`，排除指定表的数据（保留表结构）。"""
        ...

    def database_exists(self, name: str) -> bool:
        """数据库是否已存在（连接仍走维护连接）。"""
        ...

    def create_database(self, name: str, owner: str) -> None:
        """新建数据库并把 OWNER 设为应用角色；已存在时直接报错，不复用。"""
        ...

    def connection_url(self, database: str) -> str:
        """把应用连接地址切换到另一个数据库（用于临时库恢复与计数）。"""
        ...

    def drop_database(self, name: str) -> None:
        """强制断开连接后删除数据库；用于清理临时库与回滚。"""
        ...

    def installed_extensions(self, url: str) -> list[str]:
        """目标库已安装的扩展名（恢复临时库前需要同样具备）。"""
        ...

    def provision_extensions(self, database: str, extensions: Sequence[str]) -> None:
        """在建好的临时库里预先创建扩展（需要维护连接权限）。"""
        ...

    def restore_database(self, url: str, dump: Path) -> None:
        """`pg_restore` 到指定数据库；不可忽略的错误直接失败。"""
        ...

    def switch_database(self, *, target: str, replacement: str, previous: str) -> None:
        """把 `target` 改名为 `previous`，再把 `replacement` 改名为 `target`。

        第二次改名失败时必须把 `previous` 改回 `target` 再抛出错误。
        """
        ...


class SchemaMigrator(Protocol):
    """Alembic 版本查询与前向迁移。"""

    def head_revision(self) -> str:
        """随应用发布的迁移 head。"""
        ...

    def known_revisions(self) -> frozenset[str]:
        """本应用认识的全部迁移版本号；归档 head 不在其中即视为"来自更新版本"。"""
        ...

    def upgrade_to_head(self, database_url: str) -> None:
        """把指定数据库迁移到当前 head（只前向，不降级）。"""
        ...
