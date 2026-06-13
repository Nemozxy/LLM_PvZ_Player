"""[feat] 软暂停 - 通过发送全局快捷键冻结/恢复游戏."""

from __future__ import annotations

import time

import pygetwindow as gw
from loguru import logger
from pynput.keyboard import Controller, Key

from .base import PauseStrategy


class SoftPauseStrategy(PauseStrategy):
    """软暂停策略.

    利用游戏内置的暂停快捷键（如 Esc、Space、P 等）冻结画面。
    通过 pynput 发送全局按键，无需目标窗口拥有焦点即可触发系统级热键。
    """

    def __init__(
        self,
        pause_key: str = "esc",
        resume_key: str | None = None,
        delay_after_pause: float = 0.1,
        delay_after_resume: float = 0.1,
    ) -> None:
        """初始化软暂停策略.

        Args:
            pause_key: 暂停快捷键名称，如 'esc', 'space', 'p'。
            resume_key: 恢复快捷键名称，默认与 pause_key 相同。
            delay_after_pause: 发送暂停键后的等待时间（秒），等游戏冻结稳定。
            delay_after_resume: 发送恢复键后的等待时间（秒），等游戏恢复稳定。
        """
        self.pause_key = pause_key.lower()
        self.resume_key = (resume_key or pause_key).lower()
        self.delay_after_pause = delay_after_pause
        self.delay_after_resume = delay_after_resume
        self._kbd = Controller()

    @property
    def name(self) -> str:
        return "soft"

    def pause(self, window: gw.Win32Window) -> None:
        """发送暂停快捷键."""
        logger.info("[时停-软暂停] 发送暂停键: {}", self.pause_key)
        self._press_key(self.pause_key)
        time.sleep(self.delay_after_pause)

    def resume(self, window: gw.Win32Window) -> None:
        """发送恢复快捷键."""
        logger.info("[时停-软暂停] 发送恢复键: {}", self.resume_key)
        self._press_key(self.resume_key)
        time.sleep(self.delay_after_resume)

    def _press_key(self, key_name: str) -> None:
        """通过 pynput 按下并释放指定按键."""
        key = self._parse_key(key_name)
        self._kbd.press(key)
        self._kbd.release(key)

    @staticmethod
    def _parse_key(name: str):
        """将字符串按键名解析为 pynput Key 对象."""
        mapping = {
            "esc": Key.esc,
            "space": Key.space,
            "enter": Key.enter,
            "tab": Key.tab,
            "shift": Key.shift,
            "ctrl": Key.ctrl,
            "alt": Key.alt,
            "pause": Key.pause,
            "p": "p",
        }
        return mapping.get(name.lower(), name)
