"""EPUB 文档导入器：使用 ebooklib 将 .epub 文件转为 Markdown。"""

from pathlib import Path

import ebooklib
from bs4 import BeautifulSoup
from ebooklib import epub

from .base import Importer, ImportResult, auto_register


@auto_register
class EpubImporter(Importer):
    """将 EPUB 文件转换为 Markdown。

    支持：
    - 逐章解析 HTML 内容
    - 保留章节标题
    - 图片提取
    - 元数据（标题、作者）
    """

    extensions = [".epub"]

    # ── 公开接口 ──────────────────────────────────────────────

    def convert(self, file_path: Path) -> ImportResult:
        try:
            book = epub.read_epub(str(file_path))
        except Exception as exc:
            return ImportResult(
                markdown="",
                metadata={"converter": "epub", "source_format": "epub"},
                success=False,
                error=str(exc),
            )

        metadata = self._extract_metadata(book, file_path)
        metadata["converter"] = "epub"
        metadata["source_format"] = "epub"

        images = self._extract_images(book)
        markdown = self._convert_documents(book)

        return ImportResult(markdown=markdown, metadata=metadata, images=images)

    # ── 元数据 ────────────────────────────────────────────────

    def _extract_metadata(self, book: epub.EpubBook, file_path: Path) -> dict:
        meta: dict = {}

        # --- 标题 ---
        title = book.get_metadata("DC", "title")
        if title:
            meta["title"] = title[0][0]
        else:
            meta["title"] = file_path.stem

        # --- 作者 ---
        creators = book.get_metadata("DC", "creator")
        if creators:
            meta["author"] = creators[0][0]

        # --- 语言 ---
        languages = book.get_metadata("DC", "language")
        if languages:
            meta["language"] = languages[0][0]

        return meta

    # ── 图片提取 ──────────────────────────────────────────────

    def _extract_images(self, book: epub.EpubBook) -> list[tuple[str, bytes]]:
        images: list[tuple[str, bytes]] = []
        try:
            for item in book.get_items_of_type(ebooklib.ITEM_IMAGE):
                images.append((item.get_name(), item.get_content()))
        except Exception:
            pass
        return images

    # ── 文档内容转换 ──────────────────────────────────────────

    def _convert_documents(self, book: epub.EpubBook) -> str:
        """遍历所有文档条目，将 HTML 转为 Markdown。"""
        chapters: list[str] = []

        # 收集章节目录的标题映射
        toc_titles: dict[str, str] = {}
        self._collect_toc_titles(book.toc, toc_titles)

        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            try:
                content = item.get_content()
                md = self._html_to_markdown(content, toc_titles)
                if md:
                    chapters.append(md)
            except Exception:
                # 跳过有问题的章节
                continue

        return "\n\n".join(chapters).strip()

    def _collect_toc_titles(self, toc: list, mapping: dict[str, str]) -> None:
        """从 EPUB 目录中收集 href → 标题 映射。"""
        for entry in toc:
            if isinstance(entry, tuple):
                # (section, sub_items) 形式
                if len(entry) >= 2:
                    section, subs = entry[0], entry[1]
                    if hasattr(section, "href") and hasattr(section, "title"):
                        mapping[section.href] = section.title
                    self._collect_toc_titles(subs, mapping)
            elif hasattr(entry, "href") and hasattr(entry, "title"):
                mapping[entry.href] = entry.title

    # ── HTML → 简单 Markdown ──────────────────────────────────

    def _html_to_markdown(self, html: bytes | str, toc_titles: dict[str, str]) -> str:
        """将 HTML 片段转换为简单的 Markdown。"""
        soup = BeautifulSoup(html, "html.parser")

        # 移除 <script> 和 <style>
        for tag in soup(["script", "style"]):
            tag.decompose()

        # 尝试从 TOC 获取章节标题，或从第一个 <h1~6> 提取
        chapter_title = None
        first_heading = soup.find(["h1", "h2", "h3", "h4", "h5", "h6"])
        if first_heading:
            chapter_title = first_heading.get_text(strip=True)

        lines: list[str] = []
        for element in soup.body.children if soup.body else soup.children:
            tag = getattr(element, "name", None)
            if tag is None:
                continue

            text = element.get_text(strip=True)
            if not text:
                continue

            if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                level = int(tag[1])
                lines.append(f"{'#' * level} {text}")
                lines.append("")
            elif tag == "p":
                lines.append(text)
                lines.append("")
            elif tag in ("ul", "ol"):
                for li in element.find_all("li", recursive=False):
                    prefix = "- " if tag == "ul" else "1. "
                    # 处理嵌套
                    li_text = li.get_text(strip=True)
                    lines.append(f"{prefix}{li_text}")
                lines.append("")
            elif tag == "blockquote":
                for line in text.split("\n"):
                    lines.append(f"> {line}")
                lines.append("")
            elif tag in ("pre", "code"):
                lines.append(f"```\n{text}\n```")
                lines.append("")
            elif tag == "hr":
                lines.append("---")
                lines.append("")
            elif tag == "div":
                # div 容器，处理其子元素
                lines.append(text)
                lines.append("")

        md = "\n".join(lines).strip()
        if not md:
            return ""

        # 如果已有章节标题则不重复添加
        if chapter_title and not md.startswith("#"):
            md = f"## {chapter_title}\n\n{md}"

        return md
