"""运行配置：默认值面向本机开发，生产必须用环境变量覆盖连接与凭据。

密钥只从环境（或 backend/.env）读取，不写入数据库、日志或接口响应；
文字、视觉与向量模型分别配置，互不继承，避免换模型时混用旧配置身份。
"""

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 数据库连接超时（秒）：不可达时必须快速失败，不能拖住就绪检查或启动补偿
DB_CONNECT_TIMEOUT_SECONDS = 3


class Settings(BaseSettings):
    """多数变量加 CAMPUSFLOW_ 前缀；密钥与 OAuth 客户端使用各自环境变量名。"""

    model_config = SettingsConfigDict(
        env_prefix="CAMPUSFLOW_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "CampusFlow API"
    # server：仅提供 API；desktop：同源托管前端并在本机运行，见 P004。
    deployment_mode: Literal["server", "desktop"] = "server"
    # 桌面模式的用户数据根目录，必须是绝对路径；存储目录未显式覆盖时落在其下。
    data_dir: str | None = None
    # 构建后的前端目录；未配置时桌面模式只提供 API。
    static_dir: str | None = None
    # 打包后迁移脚本不在仓库相对位置时显式指定；未配置时按打包资源解析。
    migrations_dir: str | None = None
    # P007：内置 PostgreSQL 的二进制目录（initdb／pg_ctl／pg_isready）；不填用 PATH。
    pg_bin: str | None = None
    # P007：本机口令存放位置。auto 依次尝试 macOS 钥匙串、Windows DPAPI、0600 文件。
    secret_backend: Literal["auto", "keychain", "dpapi", "file"] = "auto"
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
    # 删除界面据此说明备份残留（D010）；不代表升级备份会保留这么久。
    backup_retention_days: int = 30
    # P011：升级前备份按份数保留，避免每次升级后无限累积；只在升级成功后清理。
    backup_retention_count: int = Field(default=5, ge=1)
    # P011：pg_dump／pg_restore 所在目录；未配置时从 PATH 查找。
    pg_bin: str | None = None
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
    # 智谱文字/视觉/向量模型共用密钥（ZHIPU_API_KEY；未配置时不得调用真实模型）
    zhipu_api_key: str | None = Field(default=None, validation_alias="ZHIPU_API_KEY")
    # Q019：文字生成供应方；deepseek 使用 DEEPSEEK_API_KEY 与 OpenAI 兼容接口
    deepseek_api_key: str | None = Field(default=None, validation_alias="DEEPSEEK_API_KEY")
    text_provider: str = Field(default="zhipu", validation_alias="CAMPUSFLOW_TEXT_PROVIDER")
    # X002：用户选择的文字模型，与视觉模型独立配置。
    text_model: str = "glm-4-flash-250414"
    text_timeout_seconds: float = Field(default=60, gt=0, le=120, allow_inf_nan=False)
    # 输出上限同时约束费用；被截断的回答按无效响应拒绝，不当作成功。
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
    # M9：外部日历。local 只用于自动化验收与显式本地演示，生产使用 google。
    calendar_provider: str = "google"
    # Fernet 密钥，只用于加密 OAuth 令牌；未配置时拒绝连接/读取凭据，不落明文。
    calendar_token_key: str | None = None
    google_calendar_client_id: str | None = Field(
        default=None, validation_alias="GOOGLE_CALENDAR_CLIENT_ID"
    )
    google_calendar_client_secret: str | None = Field(
        default=None, validation_alias="GOOGLE_CALENDAR_CLIENT_SECRET"
    )
    google_calendar_redirect_uri: str = Field(
        default="http://127.0.0.1:8000/api/v1/calendar/oauth/callback",
        validation_alias="GOOGLE_CALENDAR_REDIRECT_URI",
    )

    @model_validator(mode="after")
    def _validate_desktop_paths(self) -> "Settings":
        """桌面模式必须使用独立绝对值数据目录，缺配置时启动即失败而不是写进仓库。"""
        if self.deployment_mode != "desktop":
            return self
        if self.data_dir is None:
            raise ValueError("桌面模式必须通过 CAMPUSFLOW_DATA_DIR 指定数据目录")
        data_dir = Path(self.data_dir).expanduser()
        if not data_dir.is_absolute():
            raise ValueError("桌面模式的 CAMPUSFLOW_DATA_DIR 必须是绝对路径")
        self.data_dir = str(data_dir)
        # 只有用户没有显式覆盖 storage_dir 时才派生子目录，避免改写自定义存储位置。
        if "storage_dir" not in self.model_fields_set:
            self.storage_dir = str(data_dir / "materials")
        return self
