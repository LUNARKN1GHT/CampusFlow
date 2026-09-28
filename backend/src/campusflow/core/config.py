from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CAMPUSFLOW_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "CampusFlow API"
    # 默认值与本机 Docker Compose 一致，未配置 .env 也能直接启动
    database_url: str = "postgresql+psycopg://campusflow:campusflow@127.0.0.1:5432/campusflow"
    test_database_url: str = (
        "postgresql+psycopg://campusflow:campusflow@127.0.0.1:5432/campusflow_test"
    )
    redis_url: str = "redis://127.0.0.1:6379/0"
