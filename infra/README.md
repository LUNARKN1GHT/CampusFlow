# 基础设施配置

本地开发基础设施使用 Docker Compose（[docker-compose.yml](docker-compose.yml)），包含两个服务：

- **db**：PostgreSQL 17 + pgvector（镜像 `pgvector/pgvector:pg17`）。默认库 `campusflow`；首次初始化时通过 [postgres/init/01-init-dbs.sql](postgres/init/01-init-dbs.sql) 自动创建测试库 `campusflow_test` 并为两个库启用 `vector` 扩展。仅绑定 `127.0.0.1:5432`。
- **redis**：Redis 7，供 RQ 作业队列使用。仅绑定 `127.0.0.1:6379`。

数据保存在 Docker 命名卷（`pgdata`、`redisdata`）中，`docker compose down` 不会丢数据；只有 `docker compose down -v` 才会清空。

## 使用

在本目录打开终端：

```bash
docker compose up -d        # 启动（首次会自动拉取镜像，国内网络可能需要配置镜像加速）
docker compose ps           # 查看状态，两个服务都应显示 healthy
docker compose logs -f db   # 查看数据库日志
docker compose down         # 停止（数据保留）
docker compose down -v      # 停止并清空数据（慎用；清空后再次启动会重新执行初始化脚本）
```

## 连接信息（本地开发默认值）

| 项 | 值 |
| --- | --- |
| 主机 | 127.0.0.1 |
| PostgreSQL 端口 / Redis 端口 | 5432 / 6379 |
| 用户 / 密码 | campusflow / campusflow |
| 业务库 / 测试库 | campusflow / campusflow_test |

连接字符串样例见 `backend/.env.example`。这些是仅限本机开发环境的默认值；公网部署目标见 [ARCHITECTURE.md](../docs/ARCHITECTURE.md)，认证、TLS、备份与恢复检查完成之前，不得将本配置作为公网服务发布。

> 验证状态：Compose 配置已按镜像官方用法编写，启动与初始化脚本尚待本机 Docker 环境就绪后实测确认。
