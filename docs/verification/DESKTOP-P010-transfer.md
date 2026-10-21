# DESKTOP-P010：`.campusflow` 全量导出与导入

对应 Issue：P010（父任务 #476）。只记录已实现与已验收的内容；未验证项单列。

## 归档格式 v1

ZIP 容器，条目顺序固定（`manifest.json` → `database.dump` → `materials/` 按路径
排序），条目时间戳固定为 1980-01-01，文件权限 0600，先写临时文件再原子改名。

- `manifest.json`：`format_version`、`created_at`（UTC）、`app_version`、`schema_head`、
  `platform`、`database`（大小 + SHA-256）、`materials[]`（相对 POSIX 路径 + 大小 +
  SHA-256）、`missing_referenced_files`、`omitted_material_files`、`counts`（10 张
  关键表）、`excluded_credentials`（含中文原因与是否需要重新录入）。
- `database.dump`：`pg_dump --format=custom --no-owner --no-privileges`，对
  `calendar_connections`、`calendar_oauth_states`、`calendar_drafts`、
  `calendar_draft_items`、`calendar_operations`、`calendar_event_links` 只排数据
  （保留结构），避免恢复时外键悬空。
- `materials/<存储键>`：原始资料，相对路径，含中文、空格与多级目录。

## 凭据边界

不入包：外部日历连接与 OAuth state、外部写入草案/执行记录/事件链接、环境变量中的
`ZHIPU_API_KEY`／`DEEPSEEK_API_KEY`／`GOOGLE_CALENDAR_CLIENT_SECRET`／
`CAMPUSFLOW_CALENDAR_TOKEN_KEY`／`CAMPUSFLOW_LOCAL_PASSWORD`、进程内登录会话。
导入结果与清单都会列出需重新录入/授权的项。`pg_dump` 的 TOC 会记录**角色名**
（对象所有者元数据），它不是口令；验收用独立角色与独立口令扫描全部条目。

## 导出

读取 `alembic_version`（未迁移直接报错）→ 统计条目数与文件摘要 → 找出数据库引用
但磁盘缺失的文件 → `pg_dump` → 写清单并原子生成归档。`CAMPUSFLOW_PG_BIN` 指定
客户端目录，缺失时中文报错；口令只走 `PGPASSWORD`。首版只支持整库导出，
`workspace_id` 会被明确拒绝。

## 检查（不连接业务库）

校验 ZIP（重复/目录/加密/未知条目、路径穿越）→ 清单字段 → `format_version` 不得
高于 1 → 逐条核对大小与 SHA-256 → `schema_head` 必须在本应用迁移范围内（更新版本
拒绝，更旧则报告需要前向迁移）。返回 `ArchiveInfo`，`confirm_token` 为
`sha256:<归档文件摘要>`。

## 导入

检查与令牌 → 目标库不存在则创建；已有数据但未 `replace=True` 则拒绝 → 目标已迁移
时先导出安全备份归档 → 建临时库并预创建扩展（含必需的 `vector`）→ 解包校验 →
`pg_restore` → Alembic 前向迁移 → 条目数必须与清单一致 → 解资料并核对摘要 →
旧资料目录移入备份目录、新目录就位 → 断开连接后改库名切换 → 清理临时库与工作
目录。第二次改名失败时把旧库改回原名并还原资料目录；其余失败删除临时库、工作
目录并保持原数据不变。旧库保留为 `<target>_pre_import_*` 供人工回退。

限制：需要 CREATEDB（桌面模式默认本机超级用户，服务器请配置
`CAMPUSFLOW_PG_ADMIN_URL`）；切换会断开目标库连接，应在服务停止时导入；旧库与旧
目录不自动清理；归档未加密；目标机需安装 pgvector；pg 工具主版本应与服务器匹配。

## 公共 API（CLI / Electron）

```python
from campusflow.core.config import Settings
from campusflow.infrastructure.transfer import (
    export_workspace,   # (settings, output_path, *, workspace_id=None) -> ExportResult
    inspect_archive,    # (archive_path, *, settings=None) -> ArchiveInfo
    import_archive,     # (settings, archive_path, *, replace=False, confirm_token=None) -> ImportResult
)
```

`ExportResult`／`ImportResult`／`ArchiveInfo` 是冻结 dataclass（`Path` 序列化时转
字符串）；业务错误继承 `application.transfer.TransferError`，`str(error)` 即中文说明。
配置：`CAMPUSFLOW_PG_BIN`、`CAMPUSFLOW_PG_ADMIN_URL`、`CAMPUSFLOW_BACKUP_DIR`、
`CAMPUSFLOW_STORAGE_DIR`、`CAMPUSFLOW_MIGRATIONS_DIR`。

## 验收记录

- `uv run pytest tests/test_transfer_archive.py`：18 passed（真实 PostgreSQL；
  建库权限不足时模块整体 skip）。
- 后端全量 `uv run pytest`：1601 passed, 6 skipped；`uv run ruff check .` 通过。
- 真实 round-trip（独立角色，口令与角色名不同）：归档 213708 字节，
  SHA-256 `56fa5ea4830551f584b491fbe2d8fe8b49a7bb566d0e28b1c5ef3fb65cce14b3`；
  10 张关键表条目数导入前后一致；两份资料文件 SHA-256 一致；恢复后
  `calendar_connections=0`；`alembic_version=k001_external_calendar`；全部条目扫描
  未发现口令、OAuth 令牌与模型 Key；第二次导入生成
  `p010_evidence_dst_pre_import_20261021T073405Z_7599` 与备份归档。
- 拒绝路径：截断归档、缺 `manifest.json`、`database.dump` 摘要不符、资料被改、
  `format_version=2`、`schema_head` 来自更新版本、恢复后条目数不符；均断言目标库
  哨兵数据保持不变。另模拟"恢复后中断"与"迁移后中断"，临时库与工作目录被清理。

## 未验证

- macOS ↔ Windows 真实 round-trip：无 Windows 环境，只验证相对 POSIX 路径与中文/
  空格/多级目录的可移植性。
- 跨版本真实迁移：只验证版本策略与恢复后迁移流程，没有旧版本应用导出的归档样本。
- 加密与服务器最小权限账户下的导入。
- 外部日历授权不随包迁移，导入后必须重新 OAuth（已作为验收要求明确提示）。
