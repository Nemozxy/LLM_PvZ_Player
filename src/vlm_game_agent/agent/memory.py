"""[feat] 记忆系统 - 加载与管理 Markdown 记忆文件."""

from __future__ import annotations

from pathlib import Path

from loguru import logger


class MemoryManager:
    """记忆管理器.

    负责加载指定记忆文件夹中的所有 `.md` 文件，
    合并为文本后注入 VLM 的系统提示上下文。
    """

    def __init__(self, memory_dir: str | Path | None = None) -> None:
        """初始化记忆管理器.

        Args:
            memory_dir: 记忆文件夹路径，默认项目根目录下的 `memories/`。
        """
        if memory_dir is None:
            # 默认放在项目根目录的 memories/ 下
            self._dir = Path(__file__).resolve().parents[3] / "memories"
        else:
            self._dir = Path(memory_dir)

        self._cached_text: str = ""
        self._cached_mtime: float = 0.0

    def load(self) -> str:
        """加载并合并所有记忆文件内容.

        按文件名字母顺序读取，去重空内容。

        Returns:
            合并后的 Markdown 文本。
        """
        if not self._dir.exists():
            logger.info("[记忆] 记忆文件夹不存在，跳过加载: {}", self._dir)
            return ""

        md_files = sorted(self._dir.glob("*.md"))
        if not md_files:
            logger.info("[记忆] 记忆文件夹中没有 .md 文件")
            return ""

        parts: list[str] = []
        for f in md_files:
            try:
                content = f.read_text(encoding="utf-8").strip()
                if content:
                    parts.append(f"# {f.stem}\n\n{content}")
            except Exception as exc:
                logger.warning("[记忆] 读取文件失败 {}: {}", f.name, exc)

        self._cached_text = "\n\n---\n\n".join(parts)
        logger.info("[记忆] 已加载 {} 个记忆文件", len(md_files))
        return self._cached_text

    def reload(self) -> str:
        """强制重新加载记忆文件."""
        self._cached_text = ""
        return self.load()

    def append(self, filename: str, text: str) -> None:
        """向指定记忆文件追加内容（用于 Agent 自动总结写入）.

        Args:
            filename: 记忆文件名（不含路径，自动加 `.md`）。
            text: 要追加的 Markdown 文本。
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        fpath = self._dir / f"{filename}.md"
        with fpath.open("a", encoding="utf-8") as f:
            f.write(f"\n\n{text}\n")
        logger.info("[记忆] 已追加到 {}", fpath.name)
