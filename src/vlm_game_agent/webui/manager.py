"""[feat] WebSocket 连接管理与消息广播."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from loguru import logger


class ConnectionManager:
    """WebSocket 连接管理器.

    维护所有前端客户端连接，支持广播画面、日志、操作流水，
    并接收用户下发的即时指令。
    """

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._user_commands: asyncio.Queue[str] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------ #
    #  连接生命周期
    # ------------------------------------------------------------------ #
    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        logger.info("[WebUI] 客户端已连接，当前在线: {}", len(self._clients))

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)
        logger.info("[WebUI] 客户端已断开，当前在线: {}", len(self._clients))

    # ------------------------------------------------------------------ #
    #  消息广播
    # ------------------------------------------------------------------ #
    async def broadcast(self, message: dict[str, Any]) -> None:
        """向所有在线客户端广播 JSON 消息."""
        if not self._clients:
            return
        dead: set[WebSocket] = set()
        for client in self._clients:
            try:
                await client.send_json(message)
            except Exception:
                dead.add(client)
        # 清理失效连接
        for d in dead:
            self._clients.discard(d)

    async def push_frame(self, image_b64: str) -> None:
        """推送一帧画面（Base64 JPEG/PNG）."""
        await self.broadcast({"type": "frame", "data": image_b64})

    async def push_log(self, text: str, level: str = "info") -> None:
        """推送一条日志/思考流."""
        await self.broadcast({"type": "log", "level": level, "text": text})

    async def push_action(self, action: str, detail: str) -> None:
        """推送一条操作流水记录."""
        await self.broadcast({"type": "action", "action": action, "detail": detail})

    # ------------------------------------------------------------------ #
    #  接收用户指令
    # ------------------------------------------------------------------ #
    async def handle_client(self, ws: WebSocket) -> None:
        """持续监听单个客户端发来的消息（阻塞协程）."""
        try:
            while True:
                data = await ws.receive_json()
                msg_type = data.get("type")
                if msg_type == "command":
                    cmd = data.get("text", "").strip()
                    if cmd:
                        await self._user_commands.put(cmd)
                        logger.info("[WebUI] 收到用户指令: {}", cmd)
                        # 回显给所有客户端
                        await self.broadcast({"type": "log", "level": "user", "text": f"[用户] {cmd}"})
                elif msg_type == "ping":
                    await ws.send_json({"type": "pong"})
        except WebSocketDisconnect:
            self.disconnect(ws)
        except Exception as exc:
            logger.error("[WebUI] WebSocket 异常: {}", exc)
            self.disconnect(ws)

    def get_command(self) -> str | None:
        """非阻塞地获取一条用户指令（供 Agent 主循环调用）.

        Returns:
            用户输入的指令文本，若无新指令则返回 None。
        """
        if self._user_commands.empty():
            return None
        # 需要在事件循环中调用，这里用 get_nowait 绕过阻塞
        try:
            return self._user_commands.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def get_command_async(self) -> str:
        """阻塞等待一条用户指令."""
        return await self._user_commands.get()
