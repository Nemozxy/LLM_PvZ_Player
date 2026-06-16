"""[feat] 动作执行层 - 将 VLM 输出映射为 GUI 操作."""

from __future__ import annotations

import time
from typing import Any, Callable

import pyautogui
from loguru import logger

# 禁用 pyautogui 的 FailSafe（防止鼠标移到角落触发异常）
pyautogui.FAILSAFE = False


class ActionExecutor:
    """动作执行器.

    将解析后的 computer_use action 映射为 pyautogui 操作。
    坐标系由调用方完成相对→绝对的映射。
    """

    def __init__(
        self,
        get_window_client_rect: Callable[[], tuple[int, int, int, int]],
    ) -> None:
        """初始化执行器.

        Args:
            get_window_client_rect: 返回窗口客户区屏幕坐标 (left, top, right, bottom) 的回调。
        """
        self._get_rect = get_window_client_rect

    def execute(self, action: str, args: dict[str, Any]) -> dict[str, Any]:
        """执行单个动作.

        Args:
            action: action 名称。
            args: action 参数字典。

        Returns:
            执行结果摘要字典。
        """
        logger.info("[执行] action={}, args={}", action, args)

        result: dict[str, Any] = {"action": action, "status": "ok"}

        # 坐标映射：相对 [0, 1000] → 绝对屏幕像素
        coord = args.get("coordinate")
        abs_x: int | None = None
        abs_y: int | None = None
        if coord and len(coord) >= 2:
            left, top, right, bottom = self._get_rect()
            cw, ch = right - left, bottom - top
            abs_x = left + int(coord[0] / 1000 * cw)
            abs_y = top + int(coord[1] / 1000 * ch)
            result["abs_coordinate"] = (abs_x, abs_y)

        try:
            if action == "mouse_move":
                if abs_x is None or abs_y is None:
                    raise ValueError("mouse_move 缺少 coordinate")
                pyautogui.moveTo(abs_x, abs_y, duration=0.2)

            elif action == "left_click":
                if abs_x is not None and abs_y is not None:
                    pyautogui.click(abs_x, abs_y)
                else:
                    pyautogui.click()

            elif action == "left_click_drag":
                if abs_x is None or abs_y is None:
                    raise ValueError("left_click_drag 缺少 coordinate")
                pyautogui.dragTo(abs_x, abs_y, duration=0.3)

            elif action == "right_click":
                if abs_x is not None and abs_y is not None:
                    pyautogui.rightClick(abs_x, abs_y)
                else:
                    pyautogui.rightClick()

            elif action == "middle_click":
                if abs_x is not None and abs_y is not None:
                    pyautogui.middleClick(abs_x, abs_y)
                else:
                    pyautogui.middleClick()

            elif action == "double_click":
                if abs_x is not None and abs_y is not None:
                    pyautogui.doubleClick(abs_x, abs_y)
                else:
                    pyautogui.doubleClick()

            elif action == "triple_click":
                # pyautogui 没有三击，模拟为快速三次点击
                if abs_x is not None and abs_y is not None:
                    pyautogui.click(abs_x, abs_y, clicks=3, interval=0.05)
                else:
                    pyautogui.click(clicks=3, interval=0.05)

            elif action == "scroll":
                pixels = args.get("pixels", 0)
                pyautogui.scroll(int(pixels))

            elif action == "hscroll":
                pixels = args.get("pixels", 0)
                pyautogui.hscroll(int(pixels))

            elif action == "type":
                text = args.get("text", "")
                pyautogui.typewrite(text, interval=0.01)
                result["typed"] = text

            elif action == "key":
                keys = args.get("keys", [])
                if keys:
                    # pyautogui.keyDown/keyUp 只接受单个按键，
                    # 需逐个按下再逆序释放，才能正确模拟组合键（如 Ctrl+C）
                    for k in keys:
                        pyautogui.keyDown(k)
                    for k in reversed(keys):
                        pyautogui.keyUp(k)
                result["keys"] = keys

            elif action == "wait":
                seconds = args.get("time", 1.0)
                time.sleep(seconds)
                result["waited"] = seconds

            elif action == "terminate":
                status = args.get("status", "success")
                result["status"] = status
                logger.info("[执行] 任务终止: {}", status)

            elif action == "answer":
                text = args.get("text", "")
                result["answer"] = text
                logger.info("[执行] Agent 回答: {}", text)

            else:
                raise ValueError(f"未知 action: {action}")

        except Exception as exc:
            logger.error("[执行] 动作失败: {}", exc)
            result["status"] = "error"
            result["error"] = str(exc)

        return result
