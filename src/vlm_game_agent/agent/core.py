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
from vlm_game_agent.pvz import PvZExecutor, PvZMemory, PvZStateReader

from .executor import ActionExecutor
from .llm import VLMClient
from .memory import MemoryManager
from .parser import ToolCall, parse_tool_calls, parse_compact
from .prompt import build_system_prompt
from .compressor import ContextCompressor
from .action_logger import ActionLogger


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
        max_history_turns: str | int = "full",
        pause_before_think: bool = True,
        stop_hotkey: keyboard.Key = keyboard.Key.f12,
        action_delay: float = 1.0,
        delay_click: float = 2.0,
        delay_drag: float = 2.5,
        delay_key: float = 1.5,
        delay_type: float = 1.0,
        delay_idle: float = 3.0,
        compressor: ContextCompressor | None = None,
        action_logger: ActionLogger | None = None,
        pvz_memory: PvZMemory | None = None,
        include_images_in_history: bool = False,
        include_image: bool = True,
        include_reasoning_in_history: bool = True,
    ) -> None:
        """初始化 Agent.

        Args:
            capture: 窗口截图器。
            pause: 时停控制器。
            vlm: VLM 客户端。
            memory: 记忆管理器（可选）。
            webui: WebUI 连接管理器（可选）。
            max_history_turns: 保留的最大历史对话轮数。"full" 保留全部，整数保留最近 N 轮。
            pause_before_think: 是否在 VLM 推理前暂停游戏。
            stop_hotkey: 全局停止热键，默认 F12。
            action_delay: 基础兜底延迟（秒），动作感知延迟会在此基础上取最大值。
            delay_click: 点击类动作后的最低观察等待（秒）。
            delay_drag: 拖拽类动作后的最低观察等待（秒）。
            delay_key: 按键类动作后的最低观察等待（秒）。
            delay_type: 文本输入类动作后的最低观察等待（秒）。
            delay_idle: 无有效动作（纯观察轮）的最低等待（秒）。
            compressor: 上下文压缩器（可选），达到阈值时自动压缩历史。
            action_logger: 操作日志记录器（可选），保存截图与动作日志。
            pvz_memory: PvZ 内存读取器（可选），注入结构化游戏状态到 prompt。
            include_images_in_history: 是否将历史截图保留在上下文中（默认关）。
            include_image: 是否传图片给模型（默认开）。False 则纯文本模式，适用于纯 LLM。
            include_reasoning_in_history: 是否将思维链加入历史上下文（默认开）。
        """
        self.capture = capture
        self.pause = pause
        self.vlm = vlm
        self.memory = memory or MemoryManager()
        self.webui = webui
        self.max_history_turns = max_history_turns
        self.pause_before_think = pause_before_think
        self.stop_hotkey = stop_hotkey
        self.action_delay = action_delay
        self.include_images_in_history = include_images_in_history
        self.include_image = include_image
        self.include_reasoning_in_history = include_reasoning_in_history

        # 动作感知延迟映射表
        self._action_delays: dict[str, float] = {
            "left_click": delay_click,
            "right_click": delay_click,
            "double_click": delay_click,
            "triple_click": delay_click,
            "middle_click": delay_click,
            "left_click_drag": delay_drag,
            "key": delay_key,
            "type": delay_type,
            # PvZ 专用动作延迟
            "place_plant": delay_click,
            "shovel": delay_click,
            "click_card": delay_click,
            "use_cob_cannon": delay_click,
        }
        self._delay_idle = delay_idle

        self._compressor = compressor
        self._action_logger = action_logger
        self._compress_needed = False

        # PvZ 内存读取
        self._pvz_memory = pvz_memory
        self._pvz_reader: PvZStateReader | None = None
        if self._pvz_memory and self._pvz_memory.is_connected():
            self._pvz_reader = PvZStateReader(self._pvz_memory)
            logger.info("[Agent] PvZ 内存读取已启用: {}", self._pvz_memory.version_name)

        self._executor: ActionExecutor | None = None
        self._pvz_executor: PvZExecutor | None = None
        self._last_execution_results: list[dict[str, Any]] = []
        self._history: list[dict[str, Any]] = []
        self._running = False
        self._stop_listener: keyboard.Listener | None = None
        self._last_turn_time: float = 0.0  # 上一轮结束的时间戳
        self._last_game_state_text: str = ""  # 与最新截图同步的游戏状态文本（pause 前读取）

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

        # 开始操作日志会话
        if self._action_logger:
            self._action_logger.open(task)

        # 启动全局停止热键监听（后台线程）
        self._start_stop_listener()
        logger.info("[Agent] 按 {} 可随时停止 Agent", self.stop_hotkey)

        # 初始化执行器
        self._executor = ActionExecutor(
            get_window_client_rect=self._get_client_rect,
        )

        # 初始化 PvZ 执行器（如果已连接 PvZ 内存）
        if self._pvz_memory and self._pvz_memory.is_connected():
            self._pvz_executor = PvZExecutor(
                memory=self._pvz_memory,
                get_client_rect=self._get_client_rect,
            )
            logger.info("[Agent] PvZ 执行器已初始化")

        # 加载记忆
        memory_text = self.memory.load()

        # 构建系统提示（LM Studio 兼容：content 用字符串而非数组）
        w, h = self.capture.window_size
        window_title = self.capture._window_title if self.capture._window else ""
        pvz_mode = self._pvz_memory is not None and self._pvz_memory.is_connected()
        system_prompt = build_system_prompt(w, h, memory_text, window_title, pvz_mode=pvz_mode)
        self._history = [{"role": "system", "content": system_prompt}]

        # 首轮用户消息：截图 + 任务
        self._push_user_frame(task)
        self._last_turn_time = time.perf_counter()

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

            # 1.5 同步读取游戏状态（与截图同一时刻，在 pause 之前）
            # 关键: is_paused 必须在 pause 前读取。否则软暂停(esc)后读到的永远是 True，
            # 与截图（pause 前拍的运行画面）矛盾，导致模型陷入"取消暂停"死循环。
            self._last_game_state_text = self._read_pvz_state()

            # 2. 检查 WebUI 用户指令
            user_cmd = self._fetch_user_command()
            if user_cmd:
                self._inject_user_command(user_cmd)
                self._notify_webui("log", f"人工指令: {user_cmd}", "user")

            # 3. 时停（PvZ 优先用注入冻结主循环，避免 Esc 菜单污染）
            if self.pause_before_think:
                try:
                    self._pause_for_thinking()
                except Exception as exc:
                    logger.warning("[Agent] 暂停失败: {}", exc)

            # 4. 构建本轮消息（最新截图）
            messages = self._build_messages_with_latest_frame(img_b64)

            # 4.5 上下文压缩（由上一轮 VLM 返回的精确 prompt_tokens 触发）
            # 复用主模型压缩，游戏已处于暂停状态，不需要额外暂停
            if self._compressor and self._compress_needed:
                logger.info("[Agent] 正在压缩上下文...")
                self._notify_webui("log", "上下文压缩中...", "info")
                compressed = self._compressor.compress(messages)
                # 压缩结果回写到 _history（去掉末尾最新截图消息）
                self._history = compressed[:-1]
                messages = compressed
                self._compress_needed = False

            # 4.6 推送本轮 prompt 到 WebUI（默认折叠）
            self._push_prompt_to_webui(messages)

            # 5. VLM 推理
            try:
                raw_output, reasoning = self.vlm.chat(messages)

                # 用 VLM 返回的精确 prompt_tokens 判断是否需要压缩
                if self._compressor:
                    prompt_tokens = self.vlm.last_prompt_tokens
                    threshold_tokens = int(
                        self._compressor.max_tokens * self._compressor.compress_threshold
                    )
                    if prompt_tokens >= threshold_tokens:
                        logger.info(
                            "[Agent] 上下文已用 {} token（阈值 {}），下轮将压缩",
                            prompt_tokens, threshold_tokens,
                        )
                        self._notify_webui("log", f"上下文达到 {prompt_tokens} token，即将压缩", "info")
                        self._compress_needed = True

                if reasoning:
                    logger.debug("[Agent] VLM 思维链:\n{}", reasoning)
                    self._notify_webui("log", f"[思考] {reasoning}", "debug")
                logger.info("[Agent] VLM 输出:\n{}", raw_output)
                self._notify_webui("log", f"VLM: {raw_output[:200]}", "info")
            except Exception as exc:
                logger.error("[Agent] VLM 调用失败: {}", exc)
                self._notify_webui("log", f"VLM 失败: {exc}", "error")
                if self.pause_before_think:
                    self._resume_after_thinking()
                time.sleep(2)
                continue

            # 6. 恢复游戏
            if self.pause_before_think:
                try:
                    self._resume_after_thinking()
                except Exception as exc:
                    logger.warning("[Agent] 恢复失败: {}", exc)

            # 7. 解析动作
            # === 模型主动压缩：检测 <compact> 标记 ===
            compact_summary = parse_compact(raw_output)
            if compact_summary is not None:
                logger.info("[Agent] 模型主动压缩上下文（<compact>），摘要 {} 字符", len(compact_summary))
                self._notify_webui("log", "模型已主动压缩上下文", "info")
                # 激进保留：只留 system + 摘要消息
                system_msg = self._history[0]
                self._history = [
                    system_msg,
                    {"role": "assistant", "content": f"[历史摘要]\n{compact_summary}"},
                ]
                # 纯摘要轮：不解析/执行任何动作，直接进入下一轮
                time.sleep(self._delay_idle)
                continue

            tool_calls = parse_tool_calls(raw_output)
            if not tool_calls:
                logger.warning("[Agent] 未解析到有效动作")
                self._notify_webui("log", "未解析到动作，继续观察", "warning")
                self._push_history_assistant(raw_output)
                # 无动作轮也先完成后置观察等待，再记录真实总耗时
                time.sleep(self._delay_idle)
                if self._action_logger:
                    now = time.perf_counter()
                    elapsed = now - self._last_turn_time
                    self._action_logger.log_turn(
                        img_b64=img_b64,
                        reasoning=reasoning if 'reasoning' in dir() else "",
                        raw_output=raw_output,
                        tool_calls=[],
                        execution_results=[],
                        elapsed_seconds=elapsed,
                        wait_seconds=self._delay_idle,
                    )
                self._last_turn_time = time.perf_counter()
                continue

            # 将 assistant 输出加入历史（可选包含思维链）
            if self.include_reasoning_in_history and reasoning:
                self._push_history_assistant(f"{raw_output}\n\n[思维链]\n{reasoning}")
            else:
                self._push_history_assistant(raw_output)

            # 8. 执行动作
            execution_results: list[dict[str, Any]] = []
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

                # 判断是否为 PvZ 专属动作，分派到对应执行器
                is_pvz_action = tc.name == "pvz_action" and self._pvz_executor and self._pvz_reader
                if is_pvz_action:
                    result = self._execute_pvz_action(action, tc.arguments)
                else:
                    result = self._executor.execute(action, tc.arguments)
                execution_results.append(result)
                translated = self._translate_action(action, tc.arguments)
                # 推送动作 + 执行结果到 WebUI
                action_status = result.get("status", "ok")
                action_result_text = result.get("error") or result.get("detail", "")
                self._notify_webui(
                    "action", action, translated,
                    status=action_status, result_text=action_result_text,
                )

                # 把执行结果反馈加入历史
                if result.get("status") == "error":
                    feedback = (
                        f"[执行失败] action={action}, "
                        f"error={result.get('error', '未知错误')}。"
                        f"请重新观察截图，检查坐标是否正确或操作是否可行，然后重试或换一种方式。"
                    )
                else:
                    feedback = f"[执行结果] action={action}, status={result.get('status')}, detail={result}"
                self._push_history_user_text(feedback)
                logger.debug("[Agent] {}", feedback)

                if is_pvz_action and result.get("status") == "error":
                    logger.warning("[Agent] PvZ 动作失败，停止执行本轮后续动作")
                    break

                # 连续动作之间留小间隔，让游戏响应
                time.sleep(0.15)

            # 9. 等待画面变化（动作感知延迟 + 基础延迟 + 模型主动 wait，三者取最大值）
            wait_time = self._compute_wait_time(tool_calls)
            logger.debug("[Agent] 等待 {} 秒", wait_time)
            time.sleep(wait_time)

            # 记录操作日志
            if self._action_logger:
                now = time.perf_counter()
                elapsed = now - self._last_turn_time
                self._action_logger.log_turn(
                    img_b64=img_b64,
                    reasoning=reasoning if 'reasoning' in dir() else "",
                    raw_output=raw_output if 'raw_output' in dir() else "",
                    tool_calls=tool_calls,
                    execution_results=execution_results,
                    elapsed_seconds=elapsed,
                    wait_seconds=wait_time,
                )

            # 记录本轮结束时间，供下轮计算间隔
            self._last_turn_time = time.perf_counter()
            self._last_execution_results = execution_results

        logger.info("[Agent] 任务结束")
        self._notify_webui("log", "任务结束", "info")
        # 恢复所有 hack 并关闭注入句柄
        if self._pvz_executor:
            try:
                self._pvz_executor.close()
                logger.info("[Agent] PvZ 注入器已关闭，所有 hack 已恢复")
            except Exception as exc:
                logger.warning("[Agent] PvZ 注入器关闭失败: {}", exc)
        if self._action_logger:
            self._action_logger.close()
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
        # 恢复 hack 并关闭注入器
        if self._pvz_executor:
            try:
                self._pvz_executor.close()
            except Exception as exc:
                logger.warning("[Agent] PvZ 注入器关闭失败: {}", exc)
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
        当 max_history_turns 为 "full" 时不截断。
        """
        # system 消息固定在第 0 条
        system = self._history[0]
        rest = self._history[1:]
        # 一轮 = user + assistant（或 user + assistant + user 反馈）
        # 简化：按消息数截断，保留最近 max_history_turns * 3 条
        if self.max_history_turns == "full":
            return
        max_msgs = int(self.max_history_turns) * 3
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

        # 若开启 include_images_in_history，将历史截图作为独立 user 消息追加
        # 注：当前 _history 中不持久化截图，此逻辑为未来扩展预留结构
        if self.include_images_in_history:
            # 历史图片暂不支持（截图不持久化），仅记录日志
            logger.debug("[Agent] include_images_in_history=True，但历史截图未持久化，暂不生效")

        # 最新截图作为最后一条 user 消息（数组 content，只有这里放图片）
        fmt = self.capture.config.output_format.lower() if self.capture else "png"
        mime = f"image/{fmt}"

        # 注入时间信息，帮助模型理解游戏节奏
        now = time.perf_counter()
        elapsed = now - self._last_turn_time
        time_hint = f"[当前截图，距上一轮 {elapsed:.1f} 秒]"

        # 注入上一轮操作摘要，让模型有持续性记忆
        last_turn_summary = self._build_last_turn_summary(execution_results=self._last_execution_results)

        # 注入 PvZ 内存读取的游戏状态（与最新截图同步，已在主循环截图后读取并缓存）
        game_state_text = self._last_game_state_text

        # 构建用户消息
        user_text_parts = [time_hint]
        if game_state_text:
            user_text_parts.append(f"\n<game_state>\n{game_state_text}\n</game_state>")
        user_text_parts.append(f"\n{last_turn_summary}")
        if self.include_image:
            user_text_parts.append("\n请根据当前截图和游戏状态，输出下一步操作。")
        else:
            user_text_parts.append("\n请根据当前游戏状态，输出下一步操作。")
        user_text = "\n".join(user_text_parts)

        if self.include_image:
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{img_b64}"},
                    },
                    {"type": "text", "text": user_text},
                ],
            })
        else:
            messages.append({
                "role": "user",
                "content": user_text,
            })
        return messages

    # ------------------------------------------------------------------ #
    #  WebUI 交互
    # ------------------------------------------------------------------ #
    def _notify_webui(
        self,
        msg_type: str,
        data: str,
        detail: str = "",
        status: str = "ok",
        result_text: str = "",
    ) -> None:
        """向 WebUI 推送消息（非阻塞）.

        Args:
            msg_type: 消息类型 ("frame" / "log" / "action")。
            data: 主数据（frame 为 base64，log 为文本，action 为动作名）。
            detail: log 的级别或 action 的翻译描述。
            status: action 的执行状态 ("ok" / "error")。
            result_text: action 的执行结果详情。
        """
        if self.webui is None or self.webui._loop is None:
            return
        loop = self.webui._loop
        if msg_type == "frame":
            asyncio.run_coroutine_threadsafe(self.webui.push_frame(data), loop)
        elif msg_type == "log":
            asyncio.run_coroutine_threadsafe(
                self.webui.push_log(data, detail or "info"), loop
            )
        elif msg_type == "action":
            asyncio.run_coroutine_threadsafe(
                self.webui.push_action(data, detail, status, result_text), loop
            )

    def _fetch_user_command(self) -> str | None:
        """从 WebUI 获取用户指令（非阻塞）."""
        if self.webui is None:
            return None
        return self.webui.get_command()

    def _push_prompt_to_webui(self, messages: list[dict[str, Any]]) -> None:
        """推送 system prompt 和 user prompt 到 WebUI（两条独立消息）.

        不包含历史上下文。
        """
        if self.webui is None or self.webui._loop is None:
            return

        # 取第一条 system 消息
        for msg in messages:
            if msg.get("role") == "system":
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    self._webui_push("system_prompt", content)
                break

        # 取最后一条 user 消息
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, list):
                    texts = [p.get("text", "") for p in content if p.get("type") == "text"]
                    content = "\n".join(texts)
                if isinstance(content, str) and content.strip():
                    self._webui_push("user_prompt", content)
                break

    def _webui_push(self, msg_type: str, text: str) -> None:
        """向 WebUI 推送一条 prompt 类消息."""
        if self.webui is None or self.webui._loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            self.webui.push_prompt(text, msg_type), self.webui._loop
        )

    @staticmethod
    def _translate_action(action: str, args: dict[str, Any]) -> str:
        """将 VLM 动作翻译成中文人话."""
        # PvZ 专属动作
        if action == "place_plant":
            row = args.get("row", "?")
            col = args.get("col", "?")
            card_index = args.get("card_index", "?")
            return f"种植植物 [卡片{card_index}] 到 行{row}列{col}"
        if action == "shovel":
            row = args.get("row", "?")
            col = args.get("col", "?")
            return f"铲除 行{row}列{col} 的植物"
        if action == "use_cob_cannon":
            row = args.get("row", "?")
            col = args.get("col", "?")
            return f"发射玉米炮 到 行{row}列{col}"
        if action == "click_card":
            card_index = args.get("card_index", "?")
            return f"点击卡片 [{card_index}]"
        if action == "win_level":
            return "直接通关"
        if action == "select_seeds":
            seeds = args.get("seeds", [])
            return f"选卡: {seeds}"

        # 通用 GUI 动作
        if action == "left_click":
            coord = args.get("coordinate", [])
            return f"左键点击 相对坐标({coord[0] if len(coord) > 0 else '?'}, {coord[1] if len(coord) > 1 else '?'})"
        if action == "right_click":
            coord = args.get("coordinate", [])
            return f"右键点击 相对坐标({coord[0] if len(coord) > 0 else '?'}, {coord[1] if len(coord) > 1 else '?'})"
        if action == "double_click":
            coord = args.get("coordinate", [])
            return f"双击 相对坐标({coord[0] if len(coord) > 0 else '?'}, {coord[1] if len(coord) > 1 else '?'})"
        if action == "drag":
            start = args.get("start_coordinate", [])
            end = args.get("end_coordinate", [])
            return f"拖拽 从({start[0] if len(start) > 0 else '?'},{start[1] if len(start) > 1 else '?'}) 到({end[0] if len(end) > 0 else '?'},{end[1] if len(end) > 1 else '?'})"
        if action == "scroll":
            direction = args.get("direction", "?")
            amount = args.get("amount", "?")
            coord = args.get("coordinate", [])
            cx = coord[0] if len(coord) > 0 else "?"
            cy = coord[1] if len(coord) > 1 else "?"
            return f"滚动 {direction} {amount}次 位置({cx},{cy})"
        if action == "key_press":
            key = args.get("key", "?")
            return f"按键 {key}"
        if action == "type_text":
            text = args.get("text", "")
            return f"输入文字: {text}"
        if action == "wait":
            t = args.get("time", "?")
            return f"等待 {t} 秒"
        if action == "terminate":
            status = args.get("status", "success")
            return f"任务结束 ({status})"
        if action == "answer":
            text = args.get("text", "")
            return f"回答: {text}"

        # 未知动作，原样显示
        return f"{action} {args}"

    # ------------------------------------------------------------------ #
    #  辅助
    # ------------------------------------------------------------------ #
    def _pause_for_thinking(self) -> None:
        """进入推理前暂停游戏.

        PvZ 模式优先使用注入器冻结游戏主循环，避免 Esc 暂停菜单污染画面；
        非 PvZ 或注入暂停不可用时回退到通用 PauseController。
        """
        if self._pvz_executor:
            for method_name in (
                "pause_for_thinking",
                "freeze_for_thinking",
                "freeze_main_loop",
                "pause_game",
                "freeze_game",
                "pause",
                "freeze",
            ):
                method = getattr(self._pvz_executor, method_name, None)
                if callable(method):
                    method()
                    logger.debug("[Agent] PvZ 注入暂停已启用: {}", method_name)
                    return
            logger.debug("[Agent] PvZ 执行器不支持注入暂停，回退通用暂停")
        self.pause.pause()

    def _resume_after_thinking(self) -> None:
        """推理结束后恢复游戏.

        与 _pause_for_thinking 对应：PvZ 模式优先解除注入冻结；
        非 PvZ 或注入恢复不可用时回退到通用 PauseController。
        """
        if self._pvz_executor:
            for method_name in (
                "resume_after_thinking",
                "unfreeze_after_thinking",
                "unfreeze_main_loop",
                "resume_game",
                "unfreeze_game",
                "resume",
                "unfreeze",
            ):
                method = getattr(self._pvz_executor, method_name, None)
                if callable(method):
                    method()
                    logger.debug("[Agent] PvZ 注入暂停已解除: {}", method_name)
                    return
            logger.debug("[Agent] PvZ 执行器不支持注入恢复，回退通用恢复")
        self.pause.resume()

    def _read_pvz_state(self) -> str:
        """从 PvZ 内存读取游戏状态文本.

        如果 PvZ 内存未连接或读取失败，返回空字符串。
        """
        if not self._pvz_reader:
            # 尝试延迟连接
            if self._pvz_memory and not self._pvz_memory.is_connected():
                try:
                    if self._pvz_memory.connect():
                        self._pvz_reader = PvZStateReader(self._pvz_memory)
                        logger.info("[Agent] PvZ 内存读取已连接: {}", self._pvz_memory.version_name)
                except Exception as exc:
                    logger.debug("[Agent] PvZ 内存连接失败: {}", exc)
                    return ""

        if not self._pvz_reader:
            return ""

        try:
            return self._pvz_reader.read_and_format()
        except Exception as exc:
            logger.warning("[Agent] PvZ 状态读取失败: {}", exc)
            return ""

    def _build_last_turn_summary(self, execution_results: list[dict[str, Any]] | None = None) -> str:
        """构建上一轮操作摘要，帮助模型保持持续性记忆.

        Args:
            execution_results: 上一轮的执行结果列表。

        Returns:
            摘要文本，无操作时返回空字符串。
        """
        results = execution_results or self._last_execution_results
        if not results:
            return ""

        parts: list[str] = ["[上轮操作]"]
        for r in results:
            action = r.get("action", "?")
            status = r.get("status", "?")
            detail = r.get("detail", "")
            if status == "error":
                parts.append(f"  {action}: 失败 - {r.get('error', '未知')}")
            elif detail:
                parts.append(f"  {action}: {detail}")
            else:
                parts.append(f"  {action}: {status}")
        return "\n".join(parts)

    def _execute_pvz_action(self, action: str, args: dict[str, Any]) -> dict[str, Any]:
        """执行 PvZ 专属动作，需要先读取最新游戏状态.

        Args:
            action: PvZ 动作名称 (place_plant / shovel / click_card / ...).
            args: 动作参数。

        Returns:
            执行结果字典。
        """
        if not self._pvz_executor or not self._pvz_reader:
            return {"action": action, "status": "error", "error": "PvZ 执行器未初始化"}

        # 读取最新游戏状态供执行器使用
        try:
            state = self._pvz_reader.read_state()
        except Exception as exc:
            return {"action": action, "status": "error", "error": f"读取游戏状态失败: {exc}"}

        expected_plant_type: int | None = None
        expected_plant_name = ""
        if action == "place_plant":
            card_index = args.get("card_index")
            if isinstance(card_index, int) and 0 <= card_index < len(state.seeds):
                seed = state.seeds[card_index]
                expected_plant_type = seed.plant_type
                expected_plant_name = seed.name

        result = self._pvz_executor.execute(action, args, state)
        if result.get("status") == "ok" and action == "place_plant":
            self._verify_pvz_place_plant(args, result, expected_plant_type, expected_plant_name)
        return result

    def _verify_pvz_place_plant(
        self,
        args: dict[str, Any],
        result: dict[str, Any],
        expected_plant_type: int | None,
        expected_plant_name: str,
    ) -> None:
        """验证种植动作是否真的落到目标格。"""
        if not self._pvz_reader:
            return
        row = args.get("row")
        col = args.get("col")
        if row is None or col is None:
            return

        time.sleep(0.3)
        try:
            state = self._pvz_reader.read_state()
        except Exception as exc:
            result["status"] = "error"
            result["error"] = f"种植后验证失败: {exc}"
            return

        for plant in state.plants:
            if plant.row == row and plant.col == col:
                if expected_plant_type is None or plant.plant_type == expected_plant_type:
                    result["verified"] = True
                    result["detail"] = f"已验证种植 {plant.name} 到 行{row}列{col}"
                    return
                result["status"] = "error"
                result["error"] = (
                    f"种植后验证失败: 行{row}列{col} 是 {plant.name}，"
                    f"预期 {expected_plant_name or expected_plant_type}"
                )
                return

        result["status"] = "error"
        result["error"] = f"种植后验证失败: 行{row}列{col} 未发现 {expected_plant_name or '目标植物'}"

    def _get_client_rect(self) -> tuple[int, int, int, int]:
        """返回窗口客户区屏幕坐标 (left, top, right, bottom)."""
        # 复用 capture 中的客户区计算逻辑
        box = self.capture._window_box  # 当前已是客户区（capture_area=client）
        return box

    def _compute_wait_time(self, tool_calls: list[ToolCall]) -> float:
        """根据本轮动作计算合理的观察等待时间.

        三层保障取最大值：
        1. 动作感知延迟：不同动作类型有不同的最低观察时间
        2. 基础兜底延迟：action_delay（.env 可配）
        3. 模型主动 wait：如果模型输出了 wait 动作，取其指定时间

        Returns:
            本轮应等待的秒数。
        """
        # 1. 动作感知延迟：本轮所有可执行动作的延迟取最大值
        action_delay = 0.0
        has_action = False
        for tc in tool_calls:
            action = tc.arguments.get("action", "")
            if action in ("terminate", "answer", "wait"):
                continue
            has_action = True
            delay = self._action_delays.get(action)
            if delay is not None:
                action_delay = max(action_delay, delay)

        # 无有效动作时使用 idle 延迟
        if not has_action:
            action_delay = self._delay_idle

        # 2. 模型主动 wait
        model_wait = 0.0
        for tc in tool_calls:
            if tc.arguments.get("action") == "wait":
                model_wait = max(model_wait, tc.arguments.get("time", 0.5))

        # 三者取最大值
        return max(self.action_delay, action_delay, model_wait)
