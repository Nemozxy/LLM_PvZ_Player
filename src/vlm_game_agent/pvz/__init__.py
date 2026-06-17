"""PvZ 内存读取模块 — 将植物大战僵尸转换为结构化文本.

通过 ReadProcessMemory 读取 PvZ 进程内存, 获取完整游戏状态,
格式化为 LLM 可理解的结构化文本, 注入到 VLM Agent 的 prompt 中.

架构:
    PvZMemory  → 底层内存读取 (ctypes ReadProcessMemory)
    PvZOffsets → 版本偏移量表 (支持中文年度版 GOTY ZH)
    PvZStateReader → 高层游戏状态读取 + 文本格式化

用法::

    from vlm_game_agent.pvz import PvZMemory, PvZStateReader

    mem = PvZMemory()
    if mem.connect():
        reader = PvZStateReader(mem)
        game_text = reader.read_and_format()
        # 将 game_text 注入 VLM prompt
"""

from .memory import PvZMemory, PvZMemoryError
from .offsets import (
    ITEM_NAMES,
    PLACE_ITEM_NAMES,
    PLANT_NAMES,
    PLANT_SUN_COST,
    PVZ_BASE_ADDRESS,
    ZOMBIE_NAMES,
    GameUI,
    PvZOffsets,
    PvZVersion,
    SceneType,
    detect_version_from_timestamp,
    get_offsets,
)
from .reader import (
    GameState,
    GridItemInfo,
    ItemInfo,
    LawnMowerInfo,
    PlantInfo,
    PvZStateReader,
    SeedInfo,
    ZombieInfo,
)
from .executor import PvZExecutor

__all__ = [
    # 核心
    "PvZMemory",
    "PvZMemoryError",
    "PvZStateReader",
    "GameState",
    # 偏移
    "PvZOffsets",
    "PvZVersion",
    "detect_version_from_timestamp",
    "get_offsets",
    # 枚举
    "GameUI",
    "SceneType",
    # 名称映射
    "PLANT_NAMES",
    "ZOMBIE_NAMES",
    "ITEM_NAMES",
    "PLACE_ITEM_NAMES",
    "PLANT_SUN_COST",
    # 常量
    "PVZ_BASE_ADDRESS",
    # 数据类
    "SeedInfo",
    "PlantInfo",
    "ZombieInfo",
    "ItemInfo",
    "GridItemInfo",
    "LawnMowerInfo",
    # 执行器
    "PvZExecutor",
]
