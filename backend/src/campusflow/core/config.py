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
    # Q004：后台恢复扫描间隔（秒）；0 表示关闭周期扫描，只在启动时扫一次
    recovery_interval_seconds: int = Field(default=15, ge=0)
    # Q004：索引作业心跳超时（秒）；Worker 每领取一块刷新一次心跳
    embed_job_timeout_seconds: int = Field(default=300, ge=1)
    # Q004：索引作业自动重投上限；0 表示关闭自动重投
    embed_job_max_auto_retries: int = Field(default=3, ge=0)
    # 智谱文字/视觉模型共用密钥（ZHIPU_API_KEY；未配置时不得调用真实模型）
    zhipu_api_key: str | None = Field(default=None, validation_alias="ZHIPU_API_KEY")
    # X002：用户选择的文字模型，与视觉模型独立配置。
    text_model: str = "glm-4-flash-250414"
    text_timeout_seconds: float = Field(default=60, gt=0, le=120, allow_inf_nan=False)
    text_max_tokens: int = Field(default=4096, ge=1, le=8192)
    # Q002：与文字/视觉分别配置。revision 是本地索引代次，不是供应商版本证明。
    embedding_model: str = "embedding-3"
    embedding_revision: str = "q001-v1"
    embedding_dimensions: int = Field(default=1024, ge=1, le=4096)
    embedding_timeout_seconds: float = Field(default=60, gt=0, le=120, allow_inf_nan=False)
    # Q009：视觉识别的单次等待上限、重试次数与线性退避。上限 3 次是为了
    # 保证最坏等待（4 次调用 + 退避）仍低于 job_timeout_seconds。
    vision_timeout_seconds: float = Field(default=60, gt=0, le=120, allow_inf_nan=False)
    vision_max_retries: int = Field(default=2, ge=0, le=3)
    vision_retry_backoff_seconds: float = Field(default=1.0, ge=0, le=10, allow_inf_nan=False)
    # 仅用于本机单用户开发。共享部署前必须通过环境变量替换默认凭据。
    local_username: str = "student"
    local_password: str = "campusflow-dev"
