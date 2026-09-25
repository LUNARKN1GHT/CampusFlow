# 数据库迁移

Alembic 已初始化，表结构通过版本化迁移变更（不在 API 启动时 `create_all()`）。

- 配置：`backend/alembic.ini`（`script_location = migrations`，`prepend_sys_path = src`）
- 环境：`env.py` 从应用配置（`CAMPUSFLOW_DATABASE_URL`）读取数据库地址
- 版本：`versions/`，首个迁移 `c95d3606e7ee` 建立 workspaces、semesters、courses、tasks、fixed_events、availability_slots 六张表

在 `backend/` 目录执行：

```bash
uv run --locked alembic upgrade head                  # 升级到最新
uv run --locked alembic revision --autogenerate -m "说明"  # 按模型变更生成新迁移
uv run --locked alembic downgrade -1                  # 回退一步
```

生成迁移后必须人工检查内容再执行。数据库与测试库（`campusflow_test`）由 `infra/docker-compose.yml` 初始化，测试库的迁移由 `tests/conftest.py` 自动执行。
