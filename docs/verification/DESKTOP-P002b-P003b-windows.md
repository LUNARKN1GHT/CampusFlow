# DESKTOP-P002b / P003b：Windows sidecar 与内置 PostgreSQL（CI 验证）

对应 Issue：P002b（Windows PyInstaller sidecar）与 P003b（Windows PostgreSQL 17 + pgvector）。
只记录实际执行过的内容；未在本机运行安装包，见文末“未验证”。

## 交付

- `desktop/build_windows.ps1`：镜像 `build_macos.sh`，独立构建 venv、PyInstaller one-folder，
  产物 `desktop\dist\campusflow-sidecar\campusflow-sidecar.exe`。
- `desktop/postgres/build_windows.ps1`：
  - 下载 EnterpriseDB PostgreSQL 17.11 x64 二进制包，SHA-256
    `6eabdf00d2893713b75db4336a23c3fdf505f056e217ec6e2e95d901750cfea3`；
  - 编译 pgvector 0.8.7（源码包 SHA-256 `cac0b10c…e0bea85`，与 macOS 相同），
    `nmake /F Makefile.win` + `install`，`PGROOT` 指向暂存目录；
  - 跳过并删除 pgAdmin 4／StackBuilder，裁剪 include、pgxs、`*.lib`／`*.exp`／`*.pdb`
    与多余 exe，保留运行时 DLL；
  - `bin` 缺少 `vcruntime140.dll`／`msvcp140.dll` 时从 Visual Studio Redist 复制。
- `.github/workflows/desktop-windows-build.yml`：在 `windows-2022` 上完整执行两条脚本、
  Electron 编译与 `electron-builder --win`，上传未签名 NSIS 安装包 artifact
  （仅 PR 验证，不创建 Release）。

## CI 实跑（2026-10-21）

运行：[desktop-windows-build.yml run 39621852716](https://github.com/LUNARKN1GHT/CampusFlow/actions/runs/39621852716)，
job `Windows x64` 7m01s，全部步骤通过。

```text
verified postgresql-17.11-windows-x64-binaries.zip: 6eabdf00…750cfea3
extracting PostgreSQL 17.11
building pgvector against D:\a\...\desktop\vendor\pgsql
done: postgres (PostgreSQL) 17.11 (133.4 MB)
== build finished in 48.7 s, 111.6 MB ==
output: desktop\dist\campusflow-sidecar\campusflow-sidecar.exe
installer: desktop\electron\release\CampusFlow-0.1.0-win-x64.exe (184.6 MB)
```

- 校验步骤断言 `sidecar.exe`、`vendor\pgsql\bin\postgres.exe` 与 `release\*.exe` 均存在。
- 产物：artifact `desktop-windows-x64-ci`。

### 首次失败与修复

第一次运行在暂存校验处失败：裁剪阶段按文件名删除了 `bin` 下**所有**非白名单文件，
把 `libpq.dll` 等运行时 DLL 一并删掉，导致 `postgres.exe --version` 无输出。
修复为只删除多余 `*.exe`，并把版本检查改为同时输出退出码与 stderr。第二次运行通过。

## 未验证

- **安装与运行**：未在 Windows 11 上执行安装包，未验证首次启动、initdb、pgvector 扩展创建、
  Worker 轮询与退出后无残留进程。
- **干净机器**：CI runner 自带 Visual Studio 与多种运行时，不构成干净机器验收。
- **签名**：未配置 `WINDOWS_CSC_LINK`，产物未签名；SmartScreen 行为未验证。
- **macOS ↔ Windows 迁移**：未执行 `.campusflow` 跨平台往返。
