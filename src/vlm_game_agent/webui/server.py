"""[feat] FastAPI WebUI 服务端."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .manager import ConnectionManager

# 静态文件目录
STATIC_DIR = Path(__file__).resolve().parent / "static"


class WebUIServer:
    """WebUI 服务器封装.

    对外暴露 app (FastAPI) 与 manager (ConnectionManager)，
    方便 Agent 主循环直接调用 manager.push_frame() 等方法。
    """

    def __init__(self) -> None:
        self.manager = ConnectionManager()
        self.app = FastAPI(title="VLM Game Agent WebUI")
        self._setup_routes()

    def _setup_routes(self) -> None:
        # 确保静态目录存在
        STATIC_DIR.mkdir(parents=True, exist_ok=True)

        # 首页
        @self.app.get("/", response_class=HTMLResponse)
        async def index() -> str:
            html_path = STATIC_DIR / "index.html"
            if html_path.exists():
                return html_path.read_text(encoding="utf-8")
            return "<h1>WebUI 静态文件未找到</h1>"

        # WebSocket 实时推送/指令通道
        @self.app.websocket("/ws")
        async def websocket_endpoint(ws: WebSocket) -> None:
            await self.manager.connect(ws)
            # handle_client 会阻塞直到连接断开
            await self.manager.handle_client(ws)

    def get_manager(self) -> ConnectionManager:
        """获取连接管理器，供外部推流使用."""
        return self.manager


def create_app() -> FastAPI:
    """工厂函数：创建 FastAPI 应用实例."""
    server = WebUIServer()
    return server.app
