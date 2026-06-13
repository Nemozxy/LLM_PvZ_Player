"""[feat] 全局配置 - 基于 pydantic-settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Agent 全局配置，支持 .env 文件与环境变量覆盖。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # VLM API
    vlm_api_key: str = ""
    vlm_base_url: str = "https://api.openai.com/v1"
    vlm_model: str = "gpt-4o"

    # 截图默认配置
    capture_scale: float = 1.0
    capture_fps: float = 2.0
    capture_format: str = "PNG"

    # 时停控制
    pause_hotkey: str = "esc"      # 软暂停快捷键
    resume_hotkey: str = "esc"     # 软恢复快捷键
    use_hard_pause: bool = False   # 是否使用进程级硬暂停

    # 记忆系统
    memory_dir: str = "./memories"

    # WebUI
    webui_host: str = "0.0.0.0"
    webui_port: int = 8080
