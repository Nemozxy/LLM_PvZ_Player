"""[feat] 上下文压缩 - 阈值触发、独立模型、不中断游戏."""

from __future__ import annotations

import time
from typing import Any

from loguru import logger
from openai import OpenAI

# 粗略 token 估算常数：中文约 1.5 字符/token，英文约 4 字符/token
# 取中间值 2.5 字符/token 作为通用估算
_CHARS_PER_TOKEN = 2.5


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """粗略估算消息列表的 token 数.

    文本按字符数除以常数估算，图片按固定值估算。
    不追求精确，只需用于阈值判断。
    """
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(content) / _CHARS_PER_TOKEN
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    ptype = part.get("type", "")
                    if ptype == "text":
                        total += len(part.get("text", "")) / _CHARS_PER_TOKEN
                    elif ptype == "image_url":
                        # 图片 token 数取决于分辨率，估算为固定值
                        total += 1000
    return int(total)


class ContextCompressor:
    """上下文压缩器.

    当上下文 token 数达到阈值时，使用独立配置的轻量模型
    将旧消息压缩为摘要，保留 system 消息和最近几轮对话。
    压缩过程不需要暂停游戏。
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        max_tokens: int = 32768,
        compress_threshold: float = 0.7,
        keep_recent_messages: int = 6,
    ) -> None:
        """初始化压缩器.

        Args:
            base_url: 压缩模型 API 地址。
            model: 压缩模型名称。
            api_key: 压缩模型 API Key。
            max_tokens: 上下文窗口总大小（token）。
            compress_threshold: 触发压缩的阈值比例（0.0-1.0）。
            keep_recent_messages: 压缩时保留的最近消息条数。
        """
        self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=60.0)
        self.model = model
        self.max_tokens = max_tokens
        self.compress_threshold = compress_threshold
        self.keep_recent_messages = keep_recent_messages
        self._last_summary = ""

    def should_compress(self, messages: list[dict[str, Any]]) -> bool:
        """判断是否需要压缩.

        当估算 token 数超过 max_tokens * threshold 时返回 True。
        """
        current = estimate_tokens(messages)
        threshold = int(self.max_tokens * self.compress_threshold)
        logger.debug("[压缩] 当前约 {} token，阈值 {}", current, threshold)
        return current >= threshold

    def compress(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """压缩上下文：将旧消息总结为摘要，保留 system 和最近消息.

        Args:
            messages: 当前完整消息列表。

        Returns:
            压缩后的消息列表。
        """
        if len(messages) <= self.keep_recent_messages + 1:
            logger.info("[压缩] 消息数过少，跳过压缩")
            return messages

        # 分离 system / 旧消息 / 最近消息
        system_msg = None
        rest_start = 0
        if messages and messages[0]["role"] == "system":
            system_msg = messages[0]
            rest_start = 1

        old_messages = messages[rest_start:-self.keep_recent_messages]
        recent_messages = messages[-self.keep_recent_messages:]

        if not old_messages:
            logger.info("[压缩] 无旧消息可压缩，跳过")
            return messages

        # 构建压缩请求
        old_text = self._messages_to_text(old_messages)
        summary = self._generate_summary(old_text)

        if not summary:
            logger.warning("[压缩] 摘要生成失败，保留原始消息")
            return messages

        self._last_summary = summary

        # 组装压缩后的消息
        result: list[dict[str, Any]] = []
        if system_msg:
            result.append(system_msg)

        # 将摘要作为 system 消息插入（紧跟原始 system 之后）
        result.append({
            "role": "system",
            "content": f"[历史摘要]\n{summary}",
        })

        result.extend(recent_messages)

        old_tokens = estimate_tokens(messages)
        new_tokens = estimate_tokens(result)
        logger.info(
            "[压缩] 完成：{} 条消息 → {} 条，约 {} token → {} token",
            len(messages), len(result), old_tokens, new_tokens,
        )
        return result

    def _generate_summary(self, old_text: str) -> str:
        """使用压缩模型生成历史摘要."""
        prompt = (
            "请将以下游戏 Agent 的历史对话记录压缩为一段简洁的摘要。"
            "摘要应包含：\n"
            "1. 当前游戏状态（关卡、分数、资源等）\n"
            "2. 已完成的关键操作和结果\n"
            "3. 用户的指令和要求\n"
            "4. 遇到的问题或失败教训\n"
            "请用中文输出，尽量精简，保留所有关键信息。\n\n"
            f"--- 历史对话 ---\n{old_text}"
        )

        try:
            t0 = time.perf_counter()
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1024,
                temperature=0.2,
            )
            elapsed = time.perf_counter() - t0
            summary = resp.choices[0].message.content or ""
            logger.info("[压缩] 摘要生成完成，耗时 {:.1f}s，{} 字符", elapsed, len(summary))
            return summary
        except Exception as exc:
            logger.error("[压缩] 摘要生成失败: {}", exc)
            return ""

    @staticmethod
    def _messages_to_text(messages: list[dict[str, Any]]) -> str:
        """将消息列表转为纯文本供压缩模型处理."""
        parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "?")
            content = msg.get("content", "")
            if isinstance(content, str):
                parts.append(f"[{role}] {content}")
            elif isinstance(content, list):
                # 提取文本部分，跳过图片
                texts: list[str] = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        texts.append(part.get("text", ""))
                    elif isinstance(part, dict) and part.get("type") == "image_url":
                        texts.append("[截图]")
                if texts:
                    parts.append(f"[{role}] {' | '.join(texts)}")
        return "\n".join(parts)
