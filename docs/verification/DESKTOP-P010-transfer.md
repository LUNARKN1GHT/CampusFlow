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

## Electron 入口

打包应用通过菜单「数据 → 导出数据归档…／从归档导入…」调用同一套 sidecar 命令，
不在 Electron 内复制校验、备份或切换逻辑：

- 导出：保存对话框默认指向 `~/Documents/campusflow-<本地时间戳>.campusflow`，完成后只展示
  路径、大小、SHA-256 与已排除凭据数量；导出期间 API／Worker 继续运行。
- 导入：先执行 `import --inspect`；确认框展示来源平台与版本、数据库结构、条目与资料数量、
  需要重新授权的项。确认后暂停 API 与 Worker（PostgreSQL 保持运行），再执行
  `import --replace --confirm-token <令牌>`，成功后重启应用；失败自动恢复服务并提示。
  确认令牌只经命令行参数传递，不写日志、不落盘。
- 维护命令使用 bootstrap 内存中的超级用户连接串与 pg 工具位置，不传本机登录凭据或 API 端口。

验证：`cd desktop/electron && npm test` 40 passed（含 14 项维护流程用例与 1 项菜单用例）。
另有打包应用联调 `tests/packaged-maintenance.spec.ts`（默认跳过，需显式提供
`CAMPUSFLOW_PACKAGED_APP`／`CAMPUSFLOW_SIDECAR_BIN`／`CAMPUSFLOW_PG_BIN`）：
用真实 sidecar 与内置 PostgreSQL 启动打包应用，脚本替换原生对话框后经「数据」菜单
完成导出 → 确认归档含 SHA-256 与排除凭据 → 检查升级（已是最新）→ 导入同一归档并
看到「已恢复条目」，全程 9 秒，1 passed。替代的是对话框返回值，菜单、命令参数、
暂停/恢复与令牌校验都走真实代码路径。

## 跨平台迁移（Windows ↔ macOS）

`.github/workflows/desktop-archive-compat.yml` 在 CI 上验证真实跨平台归档：
`windows-2022` 用打包 sidecar 与内置 PostgreSQL 完成 bootstrap、写入一条工作空间／
学期／课程，再 `export`；`macos-14` 用打包 sidecar 与内置 PostgreSQL bootstrap 新数据目录后
`import --inspect` → `import --replace --confirm-token`，并逐表比对导出与恢复条目数、
查询导入后的工作空间与课程行。

运行 [39641384458](https://github.com/LUNARKN1GHT/CampusFlow/actions/runs/39641384458)
通过（Windows export、macOS import 均 success）：

```text
Windows: bootstrap_ready schema k001_external_calendar
          exported: 208959 bytes, sha256 b3e69c91…
          inspect: format 1, schema k001_external_calendar
macOS:   bootstrap_ready schema k001_external_calendar
          import_completed migrated_to k001_external_calendar
          cross-platform archive verified: desktop-transfer-check / Cross-OS Course
```

反向（macOS 导出 → Windows 导入）由同文件的 `macos-export` → `windows-import` 覆盖，
运行 [39644194138](https://github.com/LUNARKN1GHT/CampusFlow/actions/runs/39644194138)
通过：

```text
macOS:   exported from macOS: 208958 bytes
Windows: import_completed migrated_to k001_external_calendar
          reverse cross-platform archive verified: reverse-transfer-check / Reverse OS Course
```

这条链路暴露并修复了 6 个 Windows／跨平台缺陷：

1. `PgBinaries` 只查无后缀文件名，Windows 报“缺少 initdb, pg_ctl, pg_isready”，
   现按 `*.exe` 解析（`infrastructure/db/postgres_cluster.py`）。
2. `tempfile.mkdtemp` 在 Windows 生成的受限 ACL 使 initdb 无法读取口令文件或创建
   数据目录；首启改为默认 ACL 建目录，仅在 POSIX 上 `chmod 0700`。
3. Windows 配置写入 `unix_socket_directories` 时反斜杠被当作转义符导致启动失败；
   Windows 不再写该配置。
4. `pg_ctl start` 使用管道时 postgres 继承句柄，父进程等待 EOF 挂起；启动改用
   无管道方式并从日志文件取错误。
5. sidecar 在 Windows 控制台输出中文 JSON 触发 `UnicodeEncodeError`，现启动时把
   stdout／stderr 重设为 UTF-8。
6. `pg_dump` 默认 gzip 压缩，macOS 内置 PostgreSQL 未编译 zlib 无法恢复；归档内层
   改为 `--compress=0`，外层 ZIP 仍用 deflate 压缩。

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

- macOS ↔ Windows 双向 round-trip 已由 CI 覆盖（Windows→macOS 与 macOS→Windows）；
  反向测试同样通过后，`--compress=0` 的互操作修复得到两端确认。
- 安装后的桌面应用之间迁移：CI 使用打包 sidecar 与内置 PostgreSQL，未安装 Electron 应用。
- 跨版本真实迁移：只验证版本策略与恢复后迁移流程，没有旧版本应用导出的归档样本。
- 加密与服务器最小权限账户下的导入。
- 外部日历授权不随包迁移，导入后必须重新 OAuth（已作为验收要求明确提示）。
