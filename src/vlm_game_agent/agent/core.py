"""[feat] Agent 主循环 - 截图→暂停→VLM→执行→恢复."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from loguru import logger
from pynput import keyboard

from vlm_game_agent.pause import PauseController
from vlm_game_agent.vision import WindowCapture
from vlm_game_agent.webui.manager import ConnectionManager

from .executor import ActionExecutor
from .llm import VLMClient
from .memory import MemoryManager
from .parser import ToolCall, parse_tool_calls
from .prompt import build_system_prompt


class GameAgent:
    """游戏 Agent 核心.

    协调视觉输入、VLM 推理、时停控制、动作执行形成闭环。
    可选接入 WebUI 实现远程监看与人工介入。
    """

    def __init__(
        self,
        capture: WindowCapture,
        pause: PauseController,
        vlm: VLMClient,
        memory: MemoryManager | None = None,
        webui: ConnectionManager | None = None,
        max_history_turns: int = 6,
        pause_before_think: bool = True,
        stop_hotkey: keyboard.Key = keyboard.Key.f12,
    ) -> None:
        """初始化 Agent.

        Args:
            capture: 窗口截图器。
            pause: 时停控制器。
            vlm: VLM 客户端。
            memory: 记忆管理器（可选）。
            webui: WebUI 连接管理器（可选）。
            max_history_turns: 保留的最大历史对话轮数。
            pause_before_think: 是否在 VLM 推理前暂停游戏。
            stop_hotkey: 全局停止热键，默认 F12。
        """
        self.capture = capture
        self.pause = pause
        self.vlm = vlm
        self.memory = memory or MemoryManager()
        self.webui = webui
        self.max_history_turns = max_history_turns
        self.pause_before_think = pause_before_think
        self.stop_hotkey = stop_hotkey

        self._executor: ActionExecutor | None = None
        self._history: list[dict[str, Any]] = []
        self._running = False
        self._stop_listener: keyboard.Listener | None = None

    # ------------------------------------------------------------------ #
    #  主循环
    # ------------------------------------------------------------------ #
    def run(self, task: str) -> None:
        """启动 Agent 主循环，直到任务完成或手动停止.

        Args:
            task: 用户设定的高层目标。
        """
        self._running = True
        logger.info("[Agent] 任务启动: {}", task)
        self._notify_webui("log", f"任务启动: {task}", "info")

        # 启动全局停止热键监听（后台线程）
        self._start_stop_listener()
        logger.info("[Agent] 按 {} 可随时停止 Agent", self.stop_hotkey)

        # 初始化执行器
        self._executor = ActionExecutor(
            get_window_client_rect=self._get_client_rect,
        )

        # 加载记忆
        memory_text = self.memory.load()

        # 构建系统提示（LM Studio 兼容：content 用字符串而非数组）
        w, h = self.capture.window_size
        system_prompt = build_system_prompt(w, h, memory_text)
        self._history = [{"role": "system", "content": system_prompt}]

        # 首轮用户消息：截图 + 任务
        self._push_user_frame(task)

        turn = 0
        while self._running:
            turn += 1
            logger.info("[Agent] ===== 第 {} 轮 =====", turn)
            self._notify_webui("log", f"第 {turn} 轮推理...", "info")

            # 1. 截图
            try:
                img_b64 = self.capture.capture_to_base64()
                self._notify_webui("frame", img_b64)
            except Exception as exc:
                logger.error("[Agent] 截图失败: {}", exc)
                self._notify_webui("log", f"截图失败: {exc}", "error")
                time.sleep(1)
                continue

            # 2. 检查 WebUI 用户指令
            user_cmd = self._fetch_user_command()
            if user_cmd:
                self._inject_user_command(user_cmd)
                self._notify_webui("log", f"人工指令: {user_cmd}", "user")

            # 3. 时停（软/硬暂停）
            if self.pause_before_think:
                try:
                    self.pause.pause()
                except Exception as exc:
                    logger.warning("[Agent] 暂停失败: {}", exc)

            # 4. 构建本轮消息（最新截图）
            messages = self._build_messages_with_latest_frame(img_b64)

            # 5. VLM 推理
            try:
                raw_output, reasoning = self.vlm.chat(messages)
                if reasoning:
                    logger.info("[Agent] VLM 思维链:\n{}", reasoning)
                    self._notify_webui("log", f"[思考] {reasoning[:300]}", "debug")
                logger.info("[Agent] VLM 输出:\n{}", raw_output)
                self._notify_webui("log", f"VLM: {raw_output[:200]}", "info")
            except Exception as exc:
                logger.error("[Agent] VLM 调用失败: {}", exc)
                self._notify_webui("log", f"VLM 失败: {exc}", "error")
                if self.pause_before_think:
                    self.pause.resume()
                time.sleep(2)
                continue

            # 6. 恢复游戏
            if self.pause_before_think:
                try:
                    self.pause.resume()
                except Exception as exc:
                    logger.warning("[Agent] 恢复失败: {}", exc)

            # 7. 解析动作
            tool_calls = parse_tool_calls(raw_output)
            if not tool_calls:
                logger.warning("[Agent] 未解析到有效动作")
                self._notify_webui("log", "未解析到动作，继续观察", "warning")
                self._push_history_assistant(raw_output)
                time.sleep(1)
                continue

            # 将 assistant 输出加入历史
            self._push_history_assistant(raw_output)

            # 8. 执行动作
            for tc in tool_calls:
                action = tc.arguments.get("action", "")
                if action == "terminate":
                    status = tc.arguments.get("status", "success")
                    logger.info("[Agent] 任务终止: {}", status)
                    self._notify_webui("log", f"任务完成: {status}", "info")
                    self._running = False
                    break

                if action == "answer":
                    text = tc.arguments.get("text", "")
                    logger.info("[Agent] 回答: {}", text)
                    self._notify_webui("log", f"Agent 回答: {text}", "info")
                    continue

                result = self._executor.execute(action, tc.arguments)
                self._notify_webui("action", action, str(tc.arguments))

                # 把执行结果反馈加入历史
                feedback = f"[执行结果] action={action}, status={result.get('status')}, detail={result}"
                self._push_history_user_text(feedback)
                logger.debug("[Agent] {}", feedback)

                # 连续动作之间留小间隔，让游戏响应
                time.sleep(0.15)

            # 9. 等待画面变化
            wait_time = 0.5
            for tc in tool_calls:
                if tc.arguments.get("action") == "wait":
                    wait_time = max(wait_time, tc.arguments.get("time", 0.5))
            time.sleep(wait_time)

        logger.info("[Agent] 任务结束")
        self._notify_webui("log", "任务结束", "info")
        self._cleanup_stop_listener()

    def stop(self) -> None:
        """停止 Agent 主循环（全局热键触发时调用）."""
        if not self._running:
            return
        self._running = False
        logger.info("[Agent] 收到停止信号，正在恢复游戏...")
        # 如果当前处于暂停状态，强制恢复，避免窗口被冻结
        try:
            if self.pause.is_paused():
                self.pause.resume()
        except Exception as exc:
            logger.warning("[Agent] 恢复游戏失败: {}", exc)
        self._cleanup_stop_listener()

    def _start_stop_listener(self) -> None:
        """在后台线程启动全局热键监听."""
        def on_press(key: keyboard.Key) -> bool | None:
            if key == self.stop_hotkey:
                logger.info("[Agent] 检测到停止热键 {}，正在停止...", key)
                self.stop()
                return False  # 停止监听器
            return None

        self._stop_listener = keyboard.Listener(on_press=on_press)
        self._stop_listener.daemon = True
        self._stop_listener.start()

    def _cleanup_stop_listener(self) -> None:
        """清理热键监听器."""
        if self._stop_listener and self._stop_listener.is_alive():
            self._stop_listener.stop()
            self._stop_listener = None

    # ------------------------------------------------------------------ #
    #  消息历史管理
    # ------------------------------------------------------------------ #
    def _push_user_frame(self, text: str) -> None:
        """将截图 + 文本作为用户消息加入历史."""
        # 这里只记录文本占位，实际 Base64 图片在每次请求前动态插入
        self._history.append({
            "role": "user",
            "content": f"[截图] {text}",
        })

    def _push_history_assistant(self, text: str) -> None:
        """将 assistant 输出加入历史，并控制长度."""
        self._history.append({
            "role": "assistant",
            "content": text,
        })
        self._trim_history()

    def _push_history_user_text(self, text: str) -> None:
        """将纯文本用户消息加入历史."""
        self._history.append({
            "role": "user",
            "content": text,
        })
        self._trim_history()

    def _inject_user_command(self, cmd: str) -> None:
        """插入用户通过 WebUI 下发的即时指令."""
        self._history.append({
            "role": "user",
            "content": f"[用户指令] {cmd}",
        })

    def _trim_history(self) -> None:
        """限制历史轮数，防止上下文过长.

        保留 system + 最近 max_history_turns 轮对话。
        """
        # system 消息固定在第 0 条
        system = self._history[0]
        rest = self._history[1:]
        # 一轮 = user + assistant（或 user + assistant + user 反馈）
        # 简化：按消息数截断，保留最近 max_history_turns * 3 条
        max_msgs = self.max_history_turns * 3
        if len(rest) > max_msgs:
            rest = rest[-max_msgs:]
        self._history = [system] + rest

    def _build_messages_with_latest_frame(self, img_b64: str) -> list[dict[str, Any]]:
        """构建最终请求消息：system + 文本历史 + 最新截图.

        兼容 LM Studio：
        - system / assistant / 纯文本 user 的 content 为字符串
        - 带图片的 user 消息 content 为数组
        """
        messages: list[dict[str, Any]] = []

        # system（字符串 content）
        if self._history and self._history[0]["role"] == "system":
            messages.append(self._history[0])

        # 历史文本消息（字符串 content，跳过首轮 [截图] 占位符）
        for msg in self._history[1:]:
            content = msg.get("content", "")
            if isinstance(content, str) and content.strip():
                messages.append({"role": msg["role"], "content": content})
            elif isinstance(content, list):
                # 兼容旧格式兜底
                texts = [p.get("text", "") for p in content if p.get("type") == "text"]
                if texts:
                    messages.append({"role": msg["role"], "content": "\n".join(texts)})

        # 最新截图作为最后一条 user 消息（数组 content，只有这里放图片）
        fmt = self.capture.config.output_format.lower() if self.capture else "png"
        mime = f"image/{fmt}"
        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{img_b64}"},
                },
                {"type": "text", "text": "请根据当前截图，输出下一步操作。"},
            ],
        })
        return messages

    # ------------------------------------------------------------------ #
    #  WebUI 交互
    # ------------------------------------------------------------------ #
    def _notify_webui(self, msg_type: str, data: str, detail: str = "") -> None:
        """向 WebUI 推送消息（非阻塞）."""
        if self.webui is None:
            return
        if msg_type == "frame":
            asyncio.run_coroutine_threadsafe(
                self.webui.push_frame(data), asyncio.get_event_loop()
            )
        elif msg_type == "log":
            asyncio.run_coroutine_threadsafe(
                self.webui.push_log(data, detail or "info"), asyncio.get_event_loop()
            )
        elif msg_type == "action":
            asyncio.run_coroutine_threadsafe(
                self.webui.push_action(data, detail), asyncio.get_event_loop()
            )

    def _fetch_user_command(self) -> str | None:
        """从 WebUI 获取用户指令（非阻塞）."""
        if self.webui is None:
            return None
        return self.webui.get_command()

    # ------------------------------------------------------------------ #
    #  辅助
    # ------------------------------------------------------------------ #
    def _get_client_rect(self) -> tuple[int, int, int, int]:
        """返回窗口客户区屏幕坐标 (left, top, right, bottom)."""
        # 复用 capture 中的客户区计算逻辑
        box = self.capture._window_box  # 当前已是客户区（capture_area=client）
        return box
