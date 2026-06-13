"""[feat] 时停策略抽象基类."""

from __future__ import annotations

from abc import ABC, abstractmethod

import pygetwindow as gw


class PauseStrategy(ABC):
    """暂停策略抽象基类.

    所有具体暂停策略（软暂停、失焦暂停、硬暂停）均需实现此接口。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """策略名称标识."""

    @abstractmethod
    def pause(self, window: gw.Win32Window) -> None:
        """暂停指定窗口对应的游戏/应用.

        Args:
            window: 目标窗口对象。
        """

    @abstractmethod
    def resume(self, window: gw.Win32Window) -> None:
        """恢复指定窗口对应的游戏/应用.

        Args:
            window: 目标窗口对象。
        """
