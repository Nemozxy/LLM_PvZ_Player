"""[feat] 全局配置 - 基于 pydantic-settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Agent 全局配置，支持 .env 文件与环境变量覆盖."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ========== VLM API ==========
    vlm_base_url: str = "http://127.0.0.1:1234/v1"
    vlm_model: str = "qwen3.6-27b"
    vlm_api_key: str = "sk-no-key-required"
    vlm_context_size: int = 32768  # 模型上下文窗口总大小（token）
    vlm_max_output_tokens: int = 4096  # 模型单次回复的最大长度（token）
    vlm_temperature: float = 0.3

    # ========== 截图配置 ==========
    capture_scale: float = 1.0
    capture_format: str = "PNG"
    capture_fps: float = 1.0
    capture_area: str = "client"  # client / window

    # ========== 时停控制 ==========
    pause_strategy: str = "hard"  # soft / hard / focus
    pause_hotkey: str = "esc"
    resume_hotkey: str = "esc"

    # ========== Agent 行为 ==========
    agent_max_history_turns: int = 6
    agent_pause_before_think: bool = True
    agent_action_delay: float = 1.0
    agent_stop_hotkey: str = "f12"

    # -- 动作感知延迟：执行不同动作后最低观察等待时间（秒） --
    # 实际等待 = max(agent_action_delay, 本轮动作最大延迟, 模型主动wait)
    agent_delay_click: float = 2.0    # left_click / right_click / double_click / triple_click / middle_click
    agent_delay_drag: float = 2.5     # left_click_drag
    agent_delay_key: float = 1.5      # key
    agent_delay_type: float = 1.0     # type
    agent_delay_idle: float = 3.0     # 无有效动作（纯观察轮）

    # -- 上下文压缩 --
    # 当上下文 token 数达到窗口大小的指定比例时触发压缩
    # 压缩使用独立配置的模型，不需要暂停游戏
    vlm_context_size: int = 32768  # 模型上下文窗口总大小（token）
    agent_context_compress_threshold: float = 0.7  # 达到窗口大小的多少比例时触发压缩
    # 压缩模型配置（默认复用主 VLM，可单独指定更小的模型加速压缩）
    vlm_compress_base_url: str = ""   # 留空则复用 vlm_base_url
    vlm_compress_model: str = ""      # 留空则复用 vlm_model
    vlm_compress_api_key: str = ""    # 留空则复用 vlm_api_key

    # ========== 记忆系统 ==========
    memory_dir: str = "./memories"

    # ========== 操作日志 ==========
    action_log_enabled: bool = True
    action_log_dir: str = "./action_logs"

    # ========== WebUI ==========
    webui_enabled: bool = True
    webui_host: str = "0.0.0.0"
    webui_port: int = 8080

    # ========== 快捷启动（可选） ==========
    # 设置后跳过交互式输入，直接启动
    window_title: str = ""  # 窗口标题关键词
    task: str = ""          # 任务目标

    # ========== PvZ 内存读取 ==========
    pvz_memory_enabled: bool = False  # 启用 PvZ 内存读取模式
