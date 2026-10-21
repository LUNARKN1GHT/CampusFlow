"""P010：ZIP 容器读写（条目顺序固定、摘要校验、临时文件 + 原子改名）。

ZIP 只作为容器：`database.dump` 已由 pg_dump 压缩，因此以 STORED 写入，
避免二次压缩浪费时间；清单与资料文件使用 DEFLATE。条目时间戳固定为
1980-01-01，保证同样的输入产生同样顺序的条目（内容摘要由清单记录）。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import BinaryIO
from zipfile import ZipInfo

from pydantic import ValidationError

from campusflow.application.transfer.errors import ArchiveFormatError
from campusflow.application.transfer.manifest import (
    ARCHIVE_SUFFIX,
    DATABASE_NAME,
    MANIFEST_NAME,
    MATERIALS_PREFIX,
    ArchiveManifest,
    validate_relative_posix_path,
)

_CHUNK_BYTES = 1024 * 1024
# 清单是小型 JSON；超过这个大小说明文件不是我们写的清单，直接拒绝。
_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
# 防止误选超大文件或压缩炸弹：解压总量上限远高于正常个人资料库。
_MAX_TOTAL_BYTES = 100 * 1024 * 1024 * 1024
_FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)
_FILE_MODE = 0o600


class CampusflowArchive:
    """`.campusflow` 归档读写实现（`ArchiveStore` 端口）。"""

    # ------------------------------------------------------------------ 写入

    def write(
        self,
        target: Path,
        manifest: ArchiveManifest,
        database_path: Path,
        materials: Sequence[tuple[str, Path]],
    ) -> None:
        target = Path(target)
        if target.suffix != ARCHIVE_SUFFIX:
            raise ArchiveFormatError(f"归档文件名必须以 {ARCHIVE_SUFFIX} 结尾：{target}")
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        manifest_bytes = manifest.model_dump_json(indent=2).encode("utf-8")
        try:
            with zipfile.ZipFile(temporary, "w", allowZip64=True) as archive:
                archive.writestr(_entry(MANIFEST_NAME), manifest_bytes, compresslevel=9)
                _add_file(archive, DATABASE_NAME, database_path, compress=False)
                for relative, source in sorted(materials, key=lambda item: item[0]):
                    name = f"{MATERIALS_PREFIX}/{relative}"
                    _add_file(archive, name, source, compress=True)
            _fsync_file(temporary)
            os.chmod(temporary, _FILE_MODE)
            os.replace(temporary, target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    # ------------------------------------------------------------------ 读取

    def read(self, archive: Path) -> ArchiveManifest:
        archive = Path(archive)
        if not zipfile.is_zipfile(archive):
            raise ArchiveFormatError(
                f"不是有效的 ZIP 归档（{archive.name}）；"
                "文件可能损坏或不是 CampusFlow 迁移包。本机数据未做任何修改。"
            )
        try:
            with zipfile.ZipFile(archive) as handle:
                infos = _index_entries(handle)
                if MANIFEST_NAME not in infos:
                    raise ArchiveFormatError(
                        "归档缺少 manifest.json，无法确认内容与校验和。本机数据未做任何修改。"
                    )
                info = infos[MANIFEST_NAME]
                if info.file_size > _MAX_MANIFEST_BYTES:
                    raise ArchiveFormatError("manifest.json 异常过大，拒绝解析。")
                raw = handle.read(info)
        except zipfile.BadZipFile as error:
            raise ArchiveFormatError(f"归档 ZIP 结构损坏：{error}") from error
        except RuntimeError as error:  # 加密条目
            raise ArchiveFormatError("归档包含加密条目，当前版本不支持加密归档。") from error
        try:
            manifest = ArchiveManifest.model_validate_json(raw)
        except ValidationError as error:
            detail = "；".join(
                f"{'.'.join(str(part) for part in item['loc'])}：{item['msg']}"
                for item in error.errors()[:5]
            )
            raise ArchiveFormatError(f"manifest.json 字段不合法（{detail}）。") from error
        if manifest.database.path != DATABASE_NAME:
            raise ArchiveFormatError(
                f"清单声明的数据库文件名必须是 {DATABASE_NAME}，"
                f"实际为 {manifest.database.path!r}。"
            )
        return manifest

    def verify(self, archive: Path, manifest: ArchiveManifest) -> None:
        with zipfile.ZipFile(archive) as handle:
            infos = _index_entries(handle)
            declared = {f"{MATERIALS_PREFIX}/{entry.path}" for entry in manifest.materials}
            expected = {MANIFEST_NAME, DATABASE_NAME} | declared
            actual = set(infos)
            extra = sorted(actual - expected)
            absent = sorted(expected - actual)
            if extra:
                raise ArchiveFormatError(
                    f"归档包含清单未登记的文件：{'、'.join(extra[:5])}。拒绝导入。"
                )
            if absent:
                raise ArchiveFormatError(
                    f"归档缺少清单登记的文件：{'、'.join(absent[:5])}。拒绝导入。"
                )
            total = sum(info.file_size for info in infos.values())
            if total > _MAX_TOTAL_BYTES:
                raise ArchiveFormatError("归档解压后体积异常（超过 100 GiB 上限），拒绝导入。")
            database = manifest.database
            _verify_entry(handle, infos[DATABASE_NAME], database.size, database.sha256)
            for entry in manifest.materials:
                info = infos[f"{MATERIALS_PREFIX}/{entry.path}"]
                _verify_entry(handle, info, entry.size, entry.sha256)

    # ------------------------------------------------------------------ 解出

    def extract_database(
        self, archive: Path, manifest: ArchiveManifest, destination: Path
    ) -> None:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(archive) as handle, handle.open(DATABASE_NAME) as source:
                size, digest = _copy_verified(source, destination)
            if size != manifest.database.size or digest != manifest.database.sha256:
                raise ArchiveFormatError(
                    "database.dump 在解出时校验和不符（归档可能被修改）。本机数据未做任何修改。"
                )
        except BaseException:
            destination.unlink(missing_ok=True)
            raise

    def extract_materials(
        self, archive: Path, manifest: ArchiveManifest, destination: Path
    ) -> None:
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as handle:
            for entry in manifest.materials:
                name = f"{MATERIALS_PREFIX}/{entry.path}"
                target = _safe_target(destination, entry.path)
                target.parent.mkdir(parents=True, exist_ok=True)
                with handle.open(name) as source:
                    size, digest = _copy_verified(source, target)
                if size != entry.size or digest != entry.sha256:
                    raise ArchiveFormatError(
                        f"资料文件 {entry.path} 校验和不符（归档可能被修改）。"
                        "本机数据未做任何修改。"
                    )


def _entry(name: str, *, compress: bool = True) -> ZipInfo:
    info = ZipInfo(name, date_time=_FIXED_DATE_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    info.create_system = 3  # Unix，保证文件模式可跨平台读取
    info.external_attr = (_FILE_MODE & 0xFFFF) << 16
    return info


def _add_file(archive: zipfile.ZipFile, name: str, source: Path, *, compress: bool) -> None:
    with archive.open(_entry(name, compress=compress), "w") as output, source.open("rb") as reader:
        shutil.copyfileobj(reader, output, length=_CHUNK_BYTES)


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDWR)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _index_entries(handle: zipfile.ZipFile) -> dict[str, ZipInfo]:
    infos: dict[str, ZipInfo] = {}
    for info in handle.infolist():
        name = info.filename
        if name.endswith("/"):
            raise ArchiveFormatError("归档包含目录条目，当前格式只接受普通文件条目。")
        if name in infos:
            raise ArchiveFormatError(f"归档包含重复条目：{name}。拒绝导入。")
        if info.flag_bits & 0x1:
            raise ArchiveFormatError("归档包含加密条目，当前版本不支持加密归档。")
        _check_entry_name(name)
        infos[name] = info
    return infos


def _check_entry_name(name: str) -> None:
    if name in {MANIFEST_NAME, DATABASE_NAME}:
        return
    prefix = f"{MATERIALS_PREFIX}/"
    if not name.startswith(prefix):
        raise ArchiveFormatError(f"归档包含未知条目：{name!r}。拒绝导入。")
    try:
        validate_relative_posix_path(name[len(prefix) :])
    except ValueError as error:
        raise ArchiveFormatError(f"归档条目路径不安全：{error}") from error


def _verify_entry(handle: zipfile.ZipFile, info: ZipInfo, size: int, sha256: str) -> None:
    if info.file_size != size:
        raise ArchiveFormatError(
            f"归档文件 {info.filename} 大小与清单不符（清单 {size}，实际 {info.file_size}）。"
        )
    digest = hashlib.sha256()
    with handle.open(info) as source:
        for chunk in iter(lambda: source.read(_CHUNK_BYTES), b""):
            digest.update(chunk)
    if digest.hexdigest() != sha256:
        raise ArchiveFormatError(
            f"归档文件 {info.filename} 的 SHA-256 与清单不符（归档可能被修改或损坏）。"
        )


def _copy_verified(source: BinaryIO, destination: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with destination.open("wb") as writer:
        for chunk in iter(lambda: source.read(_CHUNK_BYTES), b""):
            digest.update(chunk)
            size += len(chunk)
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    return size, digest.hexdigest()


def _safe_target(root: Path, relative: str) -> Path:
    validate_relative_posix_path(relative)
    candidate = (root / relative).resolve()
    if root.resolve() not in candidate.parents:
        raise ArchiveFormatError(f"资料文件路径越出目标目录：{relative!r}")
    return candidate


__all__ = ["CampusflowArchive"]
