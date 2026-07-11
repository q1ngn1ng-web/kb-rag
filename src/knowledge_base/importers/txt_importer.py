"""TXT 文档导入器：将 .txt / .text 纯文本文件转为 Markdown。"""

from pathlib import Path

from .base import Importer, ImportResult, auto_register

# 按优先级尝试的编码
_ENCODINGS = ["utf-8", "utf-8-sig", "gbk", "gb2312", "gb18030", "shift_jis", "euc-jp", "euc-kr", "latin-1"]


@auto_register
class TxtImporter(Importer):
    """将纯文本文件转换为基本的 Markdown。

    自动检测编码（UTF-8 → GBK → Shift_JIS 等），
    内容直接包裹为 Markdown 段落格式。
    """

    extensions = [".txt", ".text"]

    # ── 公开接口 ──────────────────────────────────────────────

    def convert(self, file_path: Path) -> ImportResult:
        text, encoding = self._read_with_encoding(file_path)

        if text is None:
            return ImportResult(
                markdown="",
                metadata={"converter": "txt", "source_format": "txt"},
                success=False,
                error=f"Failed to decode {file_path} with any known encoding",
            )

        # 简单的 Markdown 包裹：保留空行作为段落分隔
        lines = text.splitlines()
        md_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                md_lines.append("")
            elif stripped.startswith("#") and not stripped.startswith("##"):
                # 防止已有 # 的内容被误判，但保留原样
                md_lines.append(stripped)
            else:
                md_lines.append(stripped)

        markdown = "\n".join(md_lines).strip()

        metadata: dict = {
            "converter": "txt",
            "source_format": "txt",
            "title": file_path.stem,
            "encoding": encoding,
            "char_count": len(text),
            "line_count": len(lines),
        }

        return ImportResult(markdown=markdown, metadata=metadata)

    # ── 编码检测 ──────────────────────────────────────────────

    def _read_with_encoding(self, file_path: Path) -> tuple[str | None, str | None]:
        """尝试多种编码读取文件，返回 (内容, 成功编码)。"""
        raw = file_path.read_bytes()

        # 尝试 BOM 优先
        if raw.startswith(b"\xef\xbb\xbf"):
            try:
                return raw.decode("utf-8-sig"), "utf-8-sig"
            except UnicodeDecodeError:
                pass

        for enc in _ENCODINGS:
            try:
                return raw.decode(enc), enc
            except (UnicodeDecodeError, LookupError):
                continue

        return None, None
