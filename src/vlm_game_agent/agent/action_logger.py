"""[feat] 操作日志记录 - 每轮截图、思考链、动作、结果保存到本地便于复盘."""

from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger


class ActionLogger:
    """操作日志记录器.

    每轮 Agent 操作自动保存：
    - 截图 (PNG)
    - 模型思考链 (reasoning_content)
    - 模型原始输出
    - 解析出的动作与坐标
    - 执行结果
    - 汇总到 Markdown 文件
    """

    def __init__(self, log_dir: str | Path = "./action_logs") -> None:
        self._dir = Path(log_dir)
        self._session_dir: Path | None = None
        self._md_path: Path | None = None
        self._turn = 0

    # ------------------------------------------------------------------ #
    #  会话生命周期
    # ------------------------------------------------------------------ #
    def open(self, task: str) -> None:
        """开始新会话，创建日志目录和 Markdown 文件."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._session_dir = self._dir / timestamp
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self._md_path = self._session_dir / "log.md"
        self._turn = 0

        self._md_path.write_text(
            f"# Agent 操作日志\n\n"
            f"**时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"**任务**: {task}\n\n"
            f"---\n\n",
            encoding="utf-8",
        )
        logger.info("[操作日志] 会话开始: {}", self._session_dir)

    def close(self) -> None:
        """结束会话."""
        if self._md_path and self._session_dir:
            with self._md_path.open("a", encoding="utf-8") as f:
                f.write(f"\n---\n\n**结束时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            logger.info("[操作日志] 会话结束: {}", self._session_dir)

    # ------------------------------------------------------------------ #
    #  每轮记录
    # ------------------------------------------------------------------ #
    def log_turn(
        self,
        img_b64: str,
        reasoning: str,
        raw_output: str,
        tool_calls: list[Any],
        execution_results: list[dict[str, Any]],
        elapsed_seconds: float,  # noqa: ARG002  # 保留供未来用
        wait_seconds: float,  # noqa: ARG002  # 保留供未来用
    ) -> None:
        """记录一轮完整的操作日志.

        Args:
            img_b64: 本轮截图的 Base64 字符串。
            reasoning: VLM 思维链内容。
            raw_output: VLM 原始输出。
            tool_calls: 解析出的 ToolCall 列表。
            execution_results: 每个动作的执行结果列表。
            elapsed_seconds: 本轮从上一轮结束到日志写入时的总耗时。
            wait_seconds: 本轮末尾用于等待画面变化的后置观察等待时间。
        """
        if self._session_dir is None or self._md_path is None:
            return

        self._turn += 1
        timestamp = datetime.now().strftime("%H:%M:%S")

        # 保存截图
        img_path = self._save_image(img_b64)

        non_wait_seconds = max(0.0, elapsed_seconds - wait_seconds)

        # 追加 Markdown
        with self._md_path.open("a", encoding="utf-8") as f:
            f.write(f"## 第 {self._turn} 轮 — {timestamp}\n\n")
            f.write(f"- 本轮总耗时: {elapsed_seconds:.1f}s\n")
            f.write(f"- 后置观察等待: {wait_seconds:.1f}s\n")
            f.write(f"- 推理与执行耗时: {non_wait_seconds:.1f}s\n\n")

            if img_path:
                f.write(f"![截图]({img_path})\n\n")

            if reasoning:
                f.write(f"### 思维链\n\n```\n{reasoning}\n```\n\n")

            f.write(f"### VLM 输出\n\n```\n{raw_output}\n```\n\n")

            if tool_calls:
                f.write("### 解析动作\n\n")
                for i, tc in enumerate(tool_calls, 1):
                    action = tc.arguments.get("action", "?")
                    coord = tc.arguments.get("coordinate")
                    f.write(f"{i}. `{action}`")
                    if coord:
                        f.write(f" → 相对坐标 ({coord[0]}, {coord[1]})")
                        # 标记绝对坐标
                        if execution_results and i <= len(execution_results):
                            abs_coord = execution_results[i - 1].get("abs_coordinate")
                            if abs_coord:
                                f.write(f" → 屏幕 ({abs_coord[0]}, {abs_coord[1]})")
                    f.write("\n")

                f.write("\n")

            if execution_results:
                f.write("### 执行结果\n\n")
                for i, r in enumerate(execution_results, 1):
                    status = r.get("status", "?")
                    status_icon = "❌" if status == "error" else "✅"
                    f.write(f"{i}. {status_icon} `{r.get('action', '?')}`: {status}")
                    if r.get("error"):
                        f.write(f" — {r['error']}")
                    f.write("\n")
                f.write("\n")

            f.write("---\n\n")

    # ------------------------------------------------------------------ #
    #  辅助
    # ------------------------------------------------------------------ #
    def _save_image(self, img_b64: str) -> str | None:
        """保存截图到会话目录，返回相对路径."""
        try:
            data = base64.b64decode(img_b64)
            filename = f"turn_{self._turn:03d}.png"
            filepath = self._session_dir / filename
            filepath.write_bytes(data)
            return filename  # Markdown 用相对路径引用
        except Exception as exc:
            logger.warning("[操作日志] 截图保存失败: {}", exc)
            return None
