# 桌面版 Windows 11 验收记录（模板）

对应 Issue：#478（P012）与 #466 的干净机器验收。验收人按 `docs/desktop/release.md`
的清单执行，在本文逐项填写命令、结果与证据路径。未执行的项保持未勾选。

## 获取安装包

CI 产物为未签名安装包，随附校验和：

```bash
gh run download 39828326855 -n desktop-windows-x64-ci -D ./win-art
cd ./win-art && shasum -a 256 -c SHA256SUMS.txt   # macOS／Git Bash
# PowerShell: (Get-FileHash .\CampusFlow-0.1.0-win-x64.exe -Algorithm SHA256).Hash
```

该产物的 `CampusFlow-0.1.0-win-x64.exe`（185 MB）SHA-256 为
`45d36e9684260fb2808d8843d44e248b562616f908aee27344fa3502f71839ac`，与
`SHA256SUMS.txt` 一致（2026-10-23 复核）。产物对应提交 `a7ffdfc`，工作流已在该
安装包上完成静默安装、四条打包用例与卸载验证；本文仍按干净 Windows 11 实机重新执行。

## 环境

- Windows 版本／补丁：
- 是否干净机器（未装 Docker／Python／Node／PostgreSQL／Redis）：
- 安装包：`CampusFlow-<版本>-win-x64.exe`
- SHA-256（与 `SHA256SUMS.txt` 核对）：
- 安装账户是否有管理员权限：否（默认按用户安装）
- 模型密钥：未配置 / 已配置（供应方：）

## 验收项

| # | 项目 | 操作与判定 | 结果 | 证据 |
| --- | --- | --- | --- | --- |
| 1 | 安装 | 右键安装包解除锁定；SmartScreen 选择「更多信息 → 仍要运行」；默认路径 `%LOCALAPPDATA%\Programs\CampusFlow` | 待执行 | |
| 2 | 首启 | 初始化阶段完成；自动登录；数据目录 `%APPDATA%\CampusFlow\data` 出现 | 待执行 | |
| 3 | 核心流程 | 建学期／课程 → 导入 PDF 或粘贴文本 → 核对提取 → 提问并定位引用 → 生成并接受计划 → 导出清单或 ICS | 待执行 | |
| 4 | 无模型密钥 | 未配置密钥时应用可启动，本地功能可用；识别／问答返回明确原因 | 待执行 | |
| 5 | 退出无残留 | 退出后任务管理器中无 `CampusFlow.exe`、`campusflow-sidecar.exe`、`postgres.exe` | 待执行 | |
| 6 | 重启持久化 | 重新打开应用，资料、任务与计划仍在 | 待执行 | |
| 7 | 迁移导出 | 「数据 → 导出数据归档…」生成 `.campusflow` | 待执行 | |
| 8 | 跨系统导入 | 在 macOS 导入该归档，逐项核对资料、任务、候选、计划与引用 | 待执行 | |
| 9 | 升级覆盖 | 安装同一或更新构建覆盖；数据保留；`backups/` 出现升级前备份（如触发迁移） | 待执行 | |
| 10 | 卸载保留数据 | 「设置 → 应用」卸载；安装目录移除；`%APPDATA%\CampusFlow` 仍在 | 待执行 | |
| 11 | 日志 | 菜单「CampusFlow → 打开日志目录」可定位；日志不含口令、Cookie 或 API 密钥 | 待执行 | |
| 12 | 诊断 | 菜单「CampusFlow → 保存诊断信息」生成文件，内容为脱敏日志与运行信息 | 待执行 | |

## 已知限制（填写时保留）

- 安装包未签名，SmartScreen 提示属预期；不能据此判定失败。
- 首次安装与运行已在 windows-2022 CI 以 `/S` 静默方式验证，但真实 Windows 11 交互式
  安装、SmartScreen 弹窗与干净机器仍需由本记录确认。
- 跨版本升级（旧桌面版 → 新桌面版）按决定延后，不在本记录范围。

## 结论

- 通过项：
- 失败或待核对项：
- 验收人／日期：
- 维护者核对后关闭对应 Issue：
