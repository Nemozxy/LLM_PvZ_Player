"""[feat] VLM 调用封装 - OpenAI 兼容格式."""

from __future__ import annotations

from typing import Any

from loguru import logger
from openai import OpenAI


class VLMClient:
    """VLM 客户端封装.

    使用 OpenAI 兼容 API 调用本地或远程 VLM 服务。
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:1234/v1",
        model: str = "qwen3.6-35b-a3b-apex",
        api_key: str = "sk-no-key-required",
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> None:
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature

    def chat(
        self,
        messages: list[dict[str, Any]],
    ) -> str:
        """发送聊天请求并返回文本内容.

        Args:
            messages: OpenAI 格式的消息列表。

        Returns:
            VLM 返回的文本内容。

        Raises:
            RuntimeError: API 调用失败时抛出。
        """
        logger.debug("[VLM] 请求消息数: {}", len(messages))
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,  # type: ignore[arg-type]
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )
        except Exception as exc:
            raise RuntimeError(f"VLM 调用失败: {exc}") from exc

        content = resp.choices[0].message.content or ""
        logger.debug("[VLM] 返回内容长度: {}", len(content))
        return content
