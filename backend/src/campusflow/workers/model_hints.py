"""模型密钥缺失时的用户指引（Issue #553）。

桌面安装包的用户没有环境变量入口，提示必须指向应用内的「设置 → AI 服务」；
服务器与开发环境仍说明环境变量。文案只包含变量名，不含任何密钥取值。
"""

from campusflow.core.config import Settings

SETTINGS_ENTRY = "设置 → AI 服务"


def missing_model_key_hint(settings: Settings, *, env_name: str, next_step: str) -> str:
    """按部署模式给出可操作的下一步；`env_name` 例如 `ZHIPU_API_KEY`。"""
    if settings.deployment_mode == "desktop":
        label = env_name.removesuffix("_API_KEY").replace("_", " ")
        return f"请在{SETTINGS_ENTRY}填写 {label} API 密钥后重启应用并{next_step}"
    return f"请在环境变量 {env_name} 配置后重启服务并{next_step}"
