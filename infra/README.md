# 基础设施配置（待实现）

当前骨架只需要本地 Python API 和 Vite 开发服务，无需 Docker、数据库、Redis 或模型密钥。

接入持久化时在此添加 Docker Compose：PostgreSQL 17 + pgvector、本地持久卷；接入解析作业时再加入 Redis 7 和同一后端包的 RQ Worker。

生产目标为同源部署：反向代理提供前端静态文件，`/api/` 转发到 Python API，文件、数据库和 Redis 仅在私网访问。认证、TLS、备份和恢复检查完成之前，开发骨架不能作为公网服务直接发布。

镜像版本和启动方式在实际验证后锁定；当前没有可执行的 Compose 配置或部署脚本。
