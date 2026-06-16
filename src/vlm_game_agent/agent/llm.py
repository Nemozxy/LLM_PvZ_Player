"""[feat] VLM 调用封装 - OpenAI 兼容格式 + 重试 + 思维链保留."""

from __future__ import annotations

import time
from typing import Any

from loguru import logger
from openai import OpenAI


class VLMClient:
    """VLM 客户端封装.

    使用 OpenAI 兼容 API 调用本地或远程 VLM 服务。
    内置自动重试，并保留 reasoning_content（思维链）。
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:1234/v1",
        model: str = "qwen3.6-35b-a3b-apex",
        api_key: str = "sk-no-key-required",
        max_tokens: int = 8192,
        temperature: float = 0.4,
        retries: int = 3,
        retry_delay: float = 2.0,
        timeout: float = 300.0,
    ) -> None:
        self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.retries = retries
        self.retry_delay = retry_delay

    def chat(
        self,
        messages: list[dict[str, Any]],
    ) -> tuple[str, str]:
        """发送聊天请求并返回 (内容, 思维链).

        Args:
            messages: OpenAI 格式的消息列表。

        Returns:
            (content, reasoning_content) 元组。
            content 是模型最终输出（可能包含 <tool_call>）。
            reasoning_content 是思维链（供日志展示）。

        Raises:
            RuntimeError: API 调用失败时抛出。
        """
        last_exc: Exception | None = None

        for attempt in range(1, self.retries + 1):
            logger.debug("[VLM] 第 {} 次请求，消息数: {}", attempt, len(messages))
            self._log_request(messages)

            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,  # type: ignore[arg-type]
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                )
                msg = resp.choices[0].message
                content = msg.content or ""
                reasoning = getattr(msg, "reasoning_content", None) or ""
                logger.debug(
                    "[VLM] 返回 content={} chars, reasoning={} chars",
                    len(content),
                    len(reasoning),
                )
                return content, reasoning
            except Exception as exc:
                last_exc = exc
                status = getattr(exc, "status_code", None) or getattr(exc, "code", "unknown")
                logger.warning("[VLM] 第 {} 次请求失败 [{}]: {}", attempt, status, exc)
                if attempt < self.retries:
                    logger.info("[VLM] {} 秒后重试...", self.retry_delay)
                    time.sleep(self.retry_delay)

        raise RuntimeError(f"VLM 调用失败（重试 {self.retries} 次）: {last_exc}") from last_exc

    @staticmethod
    def _log_request(messages: list[dict[str, Any]]) -> None:
        """打印精简请求日志（隐藏图片 Base64 主体）."""
        try:
            summary = []
            for msg in messages:
                role = msg.get("role", "?")
                content = msg.get("content", "")
                if isinstance(content, list):
                    parts = []
                    for part in content:
                        ptype = part.get("type", "?")
                        if ptype == "image_url":
                            url = part.get("image_url", {}).get("url", "")
                            prefix = url[:60] if url else ""
                            parts.append(f"image_url({prefix}...)")
                        elif ptype == "text":
                            text = part.get("text", "")
                            parts.append(f"text({text[:80]}...)")
                        else:
                            parts.append(str(ptype))
                    summary.append(f"[{role}] {', '.join(parts)}")
                else:
                    text = str(content)[:120]
                    summary.append(f"[{role}] {text}...")
            logger.debug("[VLM] 请求结构:\n{}", "\n".join(summary))
        except Exception:
            pass
