"""[feat] VLM 调用封装 - 基于 curl subprocess（兼容 llama.cpp 协议差异）."""

from __future__ import annotations

import json
import subprocess
import time
from typing import Any

from loguru import logger


class VLMClient:
    """VLM 客户端封装.

    使用 curl subprocess 调用 llama.cpp 兼容 API。
    内置自动重试，并保留 reasoning_content（思维链）。
    Python HTTP 库（urllib/httpx/openai SDK）与部分 llama.cpp 版本存在
    协议兼容性问题，curl 是唯一稳定的通信方式。
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8888/v1",
        model: str = "qwen3.6-35b-a3b-apex",
        api_key: str = "sk-no-key-required",
        max_output_tokens: int = 8192,
        temperature: float = 0.4,
        retries: int = 3,
        retry_delay: float = 2.0,
        timeout: float = 300.0,
        curl_path: str = "curl",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.chat_url = f"{self.base_url}/chat/completions"
        self.model = model
        self.api_key = api_key
        self.max_output_tokens = max_output_tokens
        self.temperature = temperature
        self.retries = retries
        self.retry_delay = retry_delay
        self.timeout = timeout
        self.curl_path = curl_path
        self.last_prompt_tokens: int = 0

    def chat(
        self,
        messages: list[dict[str, Any]],
    ) -> tuple[str, str]:
        """发送聊天请求并返回 (内容, 思维链).

        Args:
            messages: OpenAI 格式的消息列表。

        Returns:
            (content, reasoning_content) 元组。

        Raises:
            RuntimeError: API 调用失败时抛出。
        """
        body = json.dumps({
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_output_tokens,
            "temperature": self.temperature,
        }, ensure_ascii=False)

        last_exc: Exception | None = None

        for attempt in range(1, self.retries + 1):
            logger.debug("[VLM] 第 {} 次请求，消息数: {}", attempt, len(messages))
            self._log_request(messages)

            try:
                t0 = time.perf_counter()
                # 使用 stdin 传入请求体，避免 Windows 命令行长度限制
                # (base64 图片可能超过 8192 字符)
                result = subprocess.run(
                    [
                        self.curl_path, "-s",
                        self.chat_url,
                        "-H", "Content-Type: application/json",
                        "-d", "@-",
                        "--max-time", str(int(self.timeout)),
                    ],
                    input=body,
                    capture_output=True,
                    encoding="utf-8",
                    timeout=self.timeout + 10,
                )
                elapsed = time.perf_counter() - t0

                if result.returncode != 0:
                    raise RuntimeError(f"curl 退出码 {result.returncode}: {result.stderr[:200]}")

                raw = result.stdout.strip()
                if not raw:
                    raise RuntimeError("空响应")

                resp = json.loads(raw)

                # 检查错误
                if "error" in resp:
                    raise RuntimeError(f"API 错误: {resp['error']}")

                # 记录 prompt_tokens
                usage = resp.get("usage", {})
                if usage and "prompt_tokens" in usage:
                    self.last_prompt_tokens = usage["prompt_tokens"]
                    logger.debug("[VLM] prompt_tokens: {}", self.last_prompt_tokens)

                msg = resp["choices"][0]["message"]
                content = msg.get("content") or ""
                reasoning = msg.get("reasoning_content") or ""

                logger.debug(
                    "[VLM] 返回 content={} chars, reasoning={} chars, 耗时 {:.1f}s",
                    len(content), len(reasoning), elapsed,
                )
                return content, reasoning

            except Exception as exc:
                last_exc = exc
                logger.warning("[VLM] 第 {} 次请求失败: {}", attempt, exc)
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
