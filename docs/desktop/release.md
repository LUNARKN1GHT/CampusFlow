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
| 内置 PostgreSQL 17 + pgvector | P003a（macOS）／P003b（Windows） | `desktop/vendor/postgres/` |
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

产物位于 `desktop/electron/release/`，命名形如 `CampusFlow-<version>-mac-arm64.dmg`。Windows 在 PowerShell 中执行对应的 `build_windows.ps1` 与 `electron-builder --win`；脚本由 P002b／P003b 补充，当前尚未提供。

## 签名与公证

macOS：

- `MACOS_CSC_LINK`（Developer ID Application 证书的 base64 p12）与 `MACOS_CSC_KEY_PASSWORD`。
- `APPLE_ID`、`APPLE_APP_SPECIFIC_PASSWORD`、`APPLE_TEAM_ID` 用于 notarization 与 staple。
- hardened runtime 由 `desktop/electron/build/entitlements.mac.plist` 启用；授予 JIT、可执行内存与库验证豁免，因为随包 sidecar／PostgreSQL 不是同一签名主体。

Windows：

- `WINDOWS_CSC_LINK` 与 `WINDOWS_CSC_KEY_PASSWORD` 用于代码签名；SmartScreen 信誉需要持续签名积累，首次发布仍可能提示。

未配置密钥时工作流会产出**未签名**安装包；这种包不得作为正式 Release 发布。

## 产物与校验

- 每个平台上传一个安装包 artifact，Release 任务汇总后生成 `SHA256SUMS.txt` 并一并附加。
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
- 真实 GitHub Actions 构建、签名、公证与干净机器验收尚未执行；Windows 构建脚本待 P002b／P003b。
