# CampusFlow Desktop 发布说明

<!-- 由 .github/workflows/desktop-release.yml 在创建 desktop-v* Release 时引用。
     发布前替换占位内容，并保留已知限制与校验和说明。 -->

## 版本

- 版本号：（替换）
- 发布日期：（替换）

## 安装

- macOS：下载 `CampusFlow-<version>-mac-<arch>.dmg`，拖入「应用程序」。
- Windows：下载 `CampusFlow-<version>-win-x64.exe`，按向导安装（无需管理员权限）。

首次启动会自动初始化本地数据库，不需要安装 Docker、Python、Node、PostgreSQL 或 Redis。

## 校验

下载目录中的 `SHA256SUMS.txt` 记录每个安装包的 SHA-256；先核对再安装。

## 数据与升级

- 升级前会自动备份数据库与资料，备份位于用户数据目录的 `backups/`。
- 可用「设置 → 导出工作空间」生成 `.campusflow` 包，在另一台电脑导入。
- 卸载默认保留用户数据；删除前请自行导出。

## 已知限制

- 首版不包含自动更新。
- 未配置模型密钥时，需要模型的识别、问答与向量检索不可用，界面会给出提示。
- 真实 Google 日历授权需要项目组提供 OAuth 客户端与测试账户。
- 不支持 Mac App Store、Microsoft Store、Windows ARM 与 Linux 桌面。
