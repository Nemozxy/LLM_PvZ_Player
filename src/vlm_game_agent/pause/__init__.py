"""时停控制层 - 暂停与恢复游戏状态."""

from .controller import PauseController
from .soft_pause import SoftPauseStrategy
from .focus_pause import FocusPauseStrategy
from .hard_pause import HardPauseStrategy

__all__ = [
    "PauseController",
    "SoftPauseStrategy",
    "FocusPauseStrategy",
    "HardPauseStrategy",
]
