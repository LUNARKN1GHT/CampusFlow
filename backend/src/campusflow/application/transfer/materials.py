"""P010：资料文件收集与摘要计算（纯标准库，不依赖数据库或存储实现）。

私有存储使用相对存储键（`{年}/{月}/{随机标识}.{扩展名}`），因此归档里
只写相对 POSIX 路径，跨系统复制后仍然指向同一份文件。
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

_CHUNK_BYTES = 1024 * 1024


def sha256_file(path: Path) -> str:
    """流式计算文件摘要，避免把大文件整个读进内存。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class CollectedMaterials:
    """存储根目录下可随包迁移的文件及被跳过的条目。"""

    files: tuple[tuple[str, Path], ...]
    omitted: tuple[str, ...]


def collect_material_files(root: Path) -> CollectedMaterials:
    """遍历存储根目录，返回按相对路径排序的文件列表。

    只接受普通文件；符号链接、目录内特殊文件与其他类型一律跳过并记入
    `omitted`，避免把指向存储根之外的链接打进归档。根目录不存在时返回空集。
    """
    if not root.is_dir():
        return CollectedMaterials(files=(), omitted=())
    files: list[tuple[str, Path]] = []
    omitted: list[str] = []
    for directory, subdirectories, filenames in os.walk(root, followlinks=False):
        subdirectories.sort()
        base = Path(directory)
        for name in sorted(filenames):
            full = base / name
            relative = full.relative_to(root).as_posix()
            if full.is_symlink() or not full.is_file():
                omitted.append(relative)
                continue
            files.append((relative, full))
    files.sort(key=lambda item: item[0])
    omitted.sort()
    return CollectedMaterials(files=tuple(files), omitted=tuple(omitted))


def copy_with_digest(source: Path, destination: Path) -> tuple[int, str]:
    """复制文件并同时计算大小与 SHA-256；目标目录不存在时创建。"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as reader, destination.open("wb") as writer:
        for chunk in iter(lambda: reader.read(_CHUNK_BYTES), b""):
            digest.update(chunk)
            size += len(chunk)
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    return size, digest.hexdigest()
