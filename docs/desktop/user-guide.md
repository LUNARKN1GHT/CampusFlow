# CampusFlow 桌面版使用指南

适用于 macOS（arm64／x64）与 Windows（x64）安装包。桌面版自带 Python 运行时、
PostgreSQL 17 与 pgvector，不需要另装 Docker、Python、Node、PostgreSQL 或 Redis。
Docker 服务端部署见 [DEVELOPMENT.md](../DEVELOPMENT.md)。

> 当前安装包尚未签名与公证；Gatekeeper／SmartScreen 的实际表现见“安装”一节。
> 干净机器验收尚未执行，本指南不构成发布验收结论。

## 核对安装包

每次下载的 artifact 内附带 `SHA256SUMS.txt`，安装前先核对：

macOS：

```bash
cd <解压后的 artifact 目录>
shasum -a 256 -c SHA256SUMS.txt
```

Windows（PowerShell）：

```powershell
cd <解压后的 artifact 目录>
Get-Content SHA256SUMS.txt | ForEach-Object {
  $hash, $file = $_ -split '\s+', 2
  $actual = (Get-FileHash $file.Trim() -Algorithm SHA256).Hash.ToLower()
  if ($actual -ne $hash) { throw "校验失败：$file" }
}
Write-Host '校验通过'
```

校验失败时不要安装，重新下载或联系项目组。

## 安装

macOS：

1. 打开 `CampusFlow-<版本>-mac-<架构>.dmg`，把 `CampusFlow.app` 拖入“应用程序”
   （或当前用户的 `~/Applications`）。
2. 未签名包首次打开会被 Gatekeeper 拦截。较新版本的 macOS 可能不提供右键「打开」，
   此时用下面任一方式：
   - 打开「系统设置 → 隐私与安全性」，在底部选择「仍要打开」；
   - 或在终端执行 `xattr -dr com.apple.quarantine /Applications/CampusFlow.app` 后重开。

   证书与公证尚未配置，正式发布前所有包都是未签名包；包内只有 Electron 上游的 ad-hoc
   签名，不是 bundle 级签名。只应从项目组提供的渠道获取，并在打开前核对 SHA-256。

Windows：

1. 运行 `CampusFlow-<版本>-win-x64.exe`，按向导安装。默认安装到当前用户的
   `%LOCALAPPDATA%\Programs\CampusFlow`，不需要管理员权限，安装目录可修改。
2. 未签名包可能触发 SmartScreen 提示；选择“更多信息 → 仍要运行”。若文件来自下载且
   被系统锁定，可先右键安装包 → 属性 → 勾选“解除锁定”再运行。正式签名后仍需时间积累
   信誉。

## 首次启动与数据位置

首次启动会初始化内置数据库（initdb、迁移、启动 API 与后台 Worker），并自动登录本机
单用户会话。初始化期间会显示当前阶段；失败时显示阶段、退出码与日志目录。

用户数据目录：

| 平台 | 位置 |
| --- | --- |
| macOS | `~/Library/Application Support/CampusFlow/data` |
| Windows | `%APPDATA%\CampusFlow\data` |

目录内包含 `pgdata`（数据库）、`materials`（资料原文）、`logs`、`backups`、`run` 与
`secrets.json`（仅在不支持系统凭据后端时使用；macOS 用钥匙串，Windows 用 DPAPI）。

不要手动修改或删除这些目录；迁移与备份请按下面的入口操作。

## 资料、任务与计划

- 资料库支持 PDF、图片、粘贴文本、DOCX 与公开网页；网页抓取不会登录目标站点。
- 待核对页面集中展示提取结果，可逐字段修正、确认或忽略，并跳到原文位置。
- 问答、计划、提醒、课程路径与导出都在应用内完成；外部日历写入仍按逐项确认执行。

## 使用模型能力

问答、截图识别、资料索引与课程事项提取需要模型服务密钥：

- `ZHIPU_API_KEY`（默认文字供应方）或 `DEEPSEEK_API_KEY`（`CAMPUSFLOW_TEXT_PROVIDER=deepseek`），
  以及向量与视觉模型所需密钥。
- 未配置密钥时应用仍可启动，本地资料、任务、计划、提醒与导出可用；识别／索引作业与
  问答会以明确原因失败，不会伪造结果。桌面端密钥目前由进程环境读取，未接入系统钥匙串。

## 迁移数据

同一台机器或跨系统迁移时使用数据归档：

1. 在来源机器选择菜单「数据 → 导出数据归档…」，默认保存到“文档”目录，
   文件名形如 `campusflow-20261022-101500.campusflow`。
2. 在目标机器选择「数据 → 从归档导入…」。导入会先暂停 API 与 Worker，替换现有数据，
   需要确认；导入前建议先导出一份当前数据。
3. 导入完成后会自动恢复服务。归档包含数据库、资料原文与校验和，不包含模型密钥等凭据。

`desktop-archive-compat.yml` 已覆盖 macOS 与 Windows 之间的双向导入，恢复后逐项核对
资料、任务、候选、计划与问答引用。

## 升级

- 直接安装新版本覆盖旧版本；应用启动时会自动检查数据库结构，需要时先写入升级前备份。
- 升级前备份位于数据目录 `backups/pre-upgrade-<UTC 时间>/`，包含数据库转储、资料快照
  与带 SHA-256 的清单；迁移或校验失败会自动恢复。
- 数据库结构比当前应用新时，应用会拒绝启动而不会降级。需要回退时重新安装旧版应用，
  并用备份恢复数据。
- 也可手动选择「数据 → 检查数据库升级…」；备份保留数量由
  `CAMPUSFLOW_BACKUP_RETENTION_COUNT` 控制（默认 5）。

## 卸载

卸载默认保留用户数据，便于以后重装或恢复：

- Windows：在“设置 → 应用”中卸载 CampusFlow，或运行安装目录下的
  `Uninstall CampusFlow.exe`。如需彻底删除数据，再手动删除 `%APPDATA%\CampusFlow`。
- macOS：把 `CampusFlow.app` 移到废纸篓；如需彻底删除数据，再手动删除
  `~/Library/Application Support/CampusFlow`。

卸载前建议先导出数据归档；卸载不会自动删除资料库中的原始文件。

## 日志与诊断

- 菜单「CampusFlow → 打开日志目录」打开当前日志所在目录。
- 菜单「CampusFlow → 保存诊断信息」导出包含版本、路径、端口、启动阶段与最近日志的
  诊断文件，日志中的口令、令牌与模型密钥已脱敏。
- 反馈问题时请附诊断文件，不要直接粘贴数据库口令或 API 密钥。

## 已知限制

- 单用户本机会话，会话令牌不持久；应用启动时会用系统凭据自动登录，不是生产级身份系统。
- 不提供自动更新，需要手动下载安装包。
- 不支持 Mac App Store、Microsoft Store、Windows ARM 与 Linux 桌面。
- 外部日历的 Google 真实账户写入尚未验证；桌面端不改变既有的逐项确认要求。
