from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 数据库连接超时（秒）：不可达时必须快速失败，不能拖住就绪检查或启动补偿
DB_CONNECT_TIMEOUT_SECONDS = 3


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
    # 原文件私有存储根目录（相对后端工作目录；不进 Git，不对外公开）
    storage_dir: str = "data/materials"
    # 上传限制（D003）：可通过环境变量覆盖
    upload_max_bytes: int = 20 * 1024 * 1024
    upload_max_files: int = 5
    # 未来备份的保留期限（天），删除界面据此说明备份残留（D010）
    backup_retention_days: int = 30
    # 启动时扫描并重投待投递作业（J004 补偿）；关闭时只保留手动重投入口
    dispatch_on_startup: bool = True
    # 作业超时（秒，J005）：领取后超过这个时长仍是"处理中"就回收为可重试失败
    job_timeout_seconds: int = 900
    # 智谱文字/视觉模型共用密钥（ZHIPU_API_KEY；未配置时不得调用真实模型）
    zhipu_api_key: str | None = Field(default=None, validation_alias="ZHIPU_API_KEY")
    # X002：用户选择的文字模型，与视觉模型独立配置。
    text_model: str = "glm-4-flash-250414"
    text_timeout_seconds: float = Field(default=60, gt=0, le=120, allow_inf_nan=False)
    text_max_tokens: int = Field(default=4096, ge=1, le=8192)
    # 仅用于本机单用户开发。共享部署前必须通过环境变量替换默认凭据。
    local_username: str = "student"
    local_password: str = "campusflow-dev"
