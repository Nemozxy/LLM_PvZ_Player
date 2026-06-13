"""WebUI 远程监看层 - FastAPI + WebSocket."""

from .server import create_app

__all__ = ["create_app"]
