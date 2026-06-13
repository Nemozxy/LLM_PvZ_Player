"""[feat] VLM 输出解析 - 提取 <tool_call> 中的动作指令."""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger


class ToolCall:
    """解析后的工具调用."""

    def __init__(self, name: str, arguments: dict[str, Any]) -> None:
        self.name = name
        self.arguments = arguments

    def __repr__(self) -> str:
        return f"ToolCall(name={self.name}, args={self.arguments})"


def parse_tool_calls(text: str) -> list[ToolCall]:
    """从 VLM 输出文本中解析所有 <tool_call> 块.

    Args:
        text: VLM 返回的原始文本。

    Returns:
        ToolCall 列表，若无匹配则返回空列表。
    """
    calls: list[ToolCall] = []
    # 匹配 <tool_call> ... </tool_call> 之间的内容
    pattern = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)
    for match in pattern.finditer(text):
        raw = match.group(1).strip()
        try:
            data = json.loads(raw)
            name = data.get("name", "")
            arguments = data.get("arguments", {})
            calls.append(ToolCall(name, arguments))
        except json.JSONDecodeError as exc:
            logger.warning("[解析] 无法解析 tool_call JSON: {} — {}", raw[:200], exc)
            continue

    # 备选：有些模型可能不包 XML 标签，直接返回 JSON 对象或数组
    if not calls:
        try:
            data = json.loads(text.strip())
            if isinstance(data, dict) and "action" in data:
                calls.append(ToolCall("computer_use", data))
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and "action" in item:
                        calls.append(ToolCall("computer_use", item))
        except json.JSONDecodeError:
            pass

    logger.info("[解析] 提取到 {} 个 tool_call", len(calls))
    return calls
