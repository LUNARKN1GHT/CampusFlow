# 桌面版构建与发布

对应 Issue：[#475](https://github.com/LUNARKN1GHT/CampusFlow/issues/475)（P009）。本文记录安装包、签名、公证与独立 Release 的操作方式。当前状态：**配置已落地，真实签名与干净机器验收尚未执行**；未执行的项不得写成已验证。

## 版本与标签

- 桌面版使用独立 `desktop-vMAJOR.MINOR.PATCH` 标签与 Release，不移动、不删除既有 `v0.x` 标签与 Release。
- 候选版可用 `desktop-vX.Y.Z-rc.N`，仅在目标提交合并且验收通过后创建正式标签。
- `.github/workflows/desktop-release.yml` 只响应 `desktop-v*` 标签或手工触发；普通 `v*` 标签继续走原有发布流程。

## 前置产物

发布前必须已有：

| 产物 | 生产者 | 预期位置 |
| --- | --- | --- |
| PyInstaller one-folder sidecar | P002a（macOS）／P002b（Windows） | `desktop/dist/campusflow-sidecar/` |
| 内置 PostgreSQL 17 + pgvector | P003a（macOS）／P003b（Windows） | `desktop/vendor/pgsql/` |
| 前端构建产物 | `frontend/npm run build` | `frontend/dist/` |
| Electron 外壳 | P008 | `desktop/electron/dist/` |
| 第三方许可清单 | P001a／P012 | `THIRD-PARTY-NOTICES.md` |

`desktop/electron/electron-builder.yml` 通过 `extraResources` 把 sidecar、PostgreSQL、静态前端和许可清单放入安装包。

## 本地构建

macOS arm64／x64：

```bash
cd frontend && npm ci && npm run build
cd ..
bash desktop/postgres/build_macos.sh
bash desktop/build_macos.sh
cd desktop/electron && npm ci && npm run build
npx electron-builder --mac --arm64 --publish never   # 或 --x64
```

产物：`desktop/electron/release/CampusFlow-<version>-mac-<arch>.dmg`。

Windows（PowerShell，x64）：

```powershell
powershell -File desktop/postgres/build_windows.ps1   # 内置 PostgreSQL + pgvector
powershell -File desktop/build_windows.ps1            # PyInstaller sidecar
powershell -File desktop/postgres/smoke_windows.ps1   # initdb / pgvector / 重启 / sidecar
cd desktop/electron; npm ci; npm run build; npx electron-builder --win --publish never
```

- PostgreSQL 使用 EnterpriseDB 官方 17.11 x64 二进制包（SHA-256 固定），pgvector 0.8.7 用 MSVC `nmake` 编译进 `desktop/vendor/pgsql`，随后剔除 pgAdmin、StackBuilder 与开发文件。
- 需要 Visual Studio 2022 的 MSVC x64 工具链；缺少 VC 运行库时从 VS Redist 复制到 `bin`。
- 产物：`release/CampusFlow-<version>-win-x64.exe`。
- 两条脚本已由 `.github/workflows/desktop-windows-build.yml` 在 `windows-2022` 跑通，
  产出未签名 NSIS 安装包并校验包内 sidecar、PostgreSQL、静态前端与许可清单；该工作流还会
  静默安装、对安装后的应用跑通首启与维护流程、确认退出无残留并静默卸载。真实 Windows 11
  实机与签名包仍未验证。桌面发布工作流默认跳过 Windows，只有在仓库变量
  `DESKTOP_WINDOWS_ENABLED=true` 时才会构建。

macOS 两个架构由 `.github/workflows/desktop-macos-build.yml` 在 `macos-14`（arm64）与
`macos-15-intel`（x64）构建并校验 DMG 内容；`electron-builder` 固定为 `26.15.3`，
架构由 `--arm64`／`--x64` 指定（`electron-builder.yml` 不再写 `arch` 列表）。

## 签名与公证

安装包资源位于 `desktop/electron/build/`：`icon.png`（1024×1024，macOS 圆角网格，
electron-builder 会为 DMG 与 NSIS 生成对应图标）与 `entitlements.mac.plist`。

macOS：

- `MACOS_CSC_LINK`（Developer ID Application 证书的 base64 p12）与 `MACOS_CSC_KEY_PASSWORD`。
- `APPLE_ID`、`APPLE_APP_SPECIFIC_PASSWORD`、`APPLE_TEAM_ID` 用于 notarization 与 staple。
- hardened runtime 由 `desktop/electron/build/entitlements.mac.plist` 启用；授予 JIT、可执行内存与库验证豁免，因为随包 sidecar／PostgreSQL 不是同一签名主体。该文件随仓库保存；`plutil -lint` 校验通过，图标为 1024×1024。签名与公证本身尚未执行。

Windows：

- `WINDOWS_CSC_LINK` 与 `WINDOWS_CSC_KEY_PASSWORD` 用于代码签名；SmartScreen 信誉需要持续签名积累，首次发布仍可能提示。

未配置密钥时工作流会产出**未签名**安装包；这种包不得作为正式 Release 发布。
推送 `desktop-v*` 标签时，工作流会在打包前检查上述 secrets，缺失则直接失败，
避免把未签名产物发布出去；手工 `workflow_dispatch` 仍可用于构建未签名测试产物。

## 产物与校验

- 每个平台上传一个安装包 artifact，Release 任务汇总后生成 `SHA256SUMS.txt` 并一并附加。
- 打包后会挂载 DMG 校验架构与随包 sidecar／PostgreSQL／静态资源；构建与发布工作流共用
  `desktop/verify_macos_dmg.sh`。Windows 由 `desktop/verify_windows_installer.ps1`
  解包 NSIS 并检查同样的资源。
- Release 只会在 macOS 构建成功、且 Windows（由 `DESKTOP_WINDOWS_ENABLED` 启用时）也成功后创建；任一启用平台失败则不发布，避免半套安装包。
- 发布说明模板：`docs/desktop/release-notes.md`。
- 下载后先核对 SHA-256，再按下方清单验收。

## 干净机器验收清单

在未安装 Docker、Python、Node、PostgreSQL、Redis 的机器上：

- [ ] Windows 11 x64 安装、启动并完成核心流程；无管理员权限也能安装。
- [ ] macOS arm64／x64 安装、启动并完成核心流程；Gatekeeper 不拦截已签名／公证包。
- [ ] 首次启动自动完成数据库初始化与迁移，不出现默认口令。
- [ ] 资料导入、解析、问答、计划、导出可用；无模型密钥时给出能力边界提示。
- [ ] 退出后无残留 postgres／sidecar 进程。
- [ ] 升级安装包后数据保留；schema 降级被拒绝且有说明。
- [ ] 一个工作空间从 macOS 导出 `.campusflow`、在 Windows 导入后资料、任务、候选、计划与引用可核对。
- [ ] 卸载默认保留用户数据，并提供显式删除选项。
- [ ] 应用内「打开日志目录」可定位日志，日志不含密钥、Cookie 或 OAuth 令牌。

以上每项都应附带截图或命令记录；当前仓库尚无这些证据，因此 P009 不能标记为验收完成。

## 回滚

- 应用回滚：重新安装上一个 `desktop-v*` 正式版；不要在没有备份的情况下用旧应用打开新 schema（启动门禁会拒绝并提示）。
- 数据回滚：升级前自动备份位于用户数据目录 `backups/`（P011）；必要时用 sidecar `restore` 恢复。
- 已发布的 tag 与 Release 不移动、不删除；如需撤回，追加新的修正版本并说明原因。

## 已知限制

- 首版不提供自动更新，用户手动下载安装包。
- 不支持 Mac App Store、Microsoft Store、Windows ARM、Linux 桌面。
- GitHub Actions 已能构建并校验未签名的 macOS DMG 与 Windows NSIS 安装包（见
  `docs/verification/DESKTOP-P002b-P003b-windows.md` 与 `docs/verification/DESKTOP-E2E-macos.md`）；
  签名、公证、Gatekeeper／SmartScreen 与干净机器验收仍未执行，也未创建过 `desktop-v*` 发布。
