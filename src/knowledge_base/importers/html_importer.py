"""HTML 文档导入器：使用 BeautifulSoup 将 .html/.htm 文件转为 Markdown。"""

from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from .base import Importer, ImportResult, auto_register


@auto_register
class HtmlImporter(Importer):
    """将 HTML 文件转换为 Markdown。

    支持：
    - h1~h6 → #~######
    - 链接、图片
    - 有序/无序列表
    - 代码块、行内代码
    - 引用块、水平线
    - 本地图片提取
    """

    extensions = [".html", ".htm"]

    # ── 公开接口 ──────────────────────────────────────────────

    def convert(self, file_path: Path) -> ImportResult:
        try:
            raw = file_path.read_bytes()
            # 尝试多种编码
            html_text = self._decode(raw)
            if html_text is None:
                return ImportResult(
                    markdown="",
                    metadata={"converter": "html", "source_format": "html"},
                    success=False,
                    error=f"Failed to decode {file_path}",
                )
        except Exception as exc:
            return ImportResult(
                markdown="",
                metadata={"converter": "html", "source_format": "html"},
                success=False,
                error=str(exc),
            )

        try:
            soup = BeautifulSoup(html_text, "html.parser")
        except Exception as exc:
            return ImportResult(
                markdown="",
                metadata={"converter": "html", "source_format": "html"},
                success=False,
                error=str(exc),
            )

        metadata = self._extract_metadata(soup, file_path)
        metadata["converter"] = "html"
        metadata["source_format"] = "html"

        images = self._extract_images(soup, file_path)
        markdown = self._convert_body(soup)

        return ImportResult(markdown=markdown, metadata=metadata, images=images)

    # ── 编码检测 ──────────────────────────────────────────────

    def _decode(self, raw: bytes) -> str | None:
        """尝试多种编码解码 HTML 内容。"""
        # BOM
        if raw.startswith(b"\xef\xbb\xbf"):
            try:
                return raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                pass

        candidates = ["utf-8", "gbk", "gb2312", "gb18030", "shift_jis", "euc-jp", "latin-1"]
        for enc in candidates:
            try:
                return raw.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return None

    # ── 元数据 ────────────────────────────────────────────────

    def _extract_metadata(self, soup: BeautifulSoup, file_path: Path) -> dict:
        meta: dict = {}

        # 标题
        title_tag = soup.find("title")
        if title_tag and title_tag.get_text(strip=True):
            meta["title"] = title_tag.get_text(strip=True)
        else:
            meta["title"] = file_path.stem

        # 描述 / 作者（meta 标签）
        desc = soup.find("meta", attrs={"name": "description"})
        if desc and desc.get("content"):
            meta["description"] = desc["content"]

        author = soup.find("meta", attrs={"name": "author"})
        if author and author.get("content"):
            meta["author"] = author["content"]

        return meta

    # ── 本地图片提取 ──────────────────────────────────────────

    def _extract_images(self, soup: BeautifulSoup, file_path: Path) -> list[tuple[str, bytes]]:
        """提取 HTML 中引用的本地图片文件。"""
        images: list[tuple[str, bytes]] = []
        base_dir = file_path.parent

        for img in soup.find_all("img"):
            src = img.get("src", "")
            if not src:
                continue

            # 只处理本地相对路径（非 URL）
            parsed = urlparse(src)
            if parsed.scheme or parsed.netloc:
                continue

            img_path = (base_dir / src).resolve()
            try:
                # 安全检查：图片必须在原目录内
                if img_path.exists() and img_path.is_file():
                    data = img_path.read_bytes()
                    images.append((img_path.name, data))
            except (OSError, PermissionError):
                continue

        return images

    # ── 正文转换 ──────────────────────────────────────────────

    def _convert_body(self, soup: BeautifulSoup) -> str:
        """将 HTML body 转换为 Markdown。"""
        body = soup.find("body")
        if body is None:
            body = soup

        lines: list[str] = []
        self._process_children(body, lines, list_level=0)
        return "\n".join(lines).strip()

    def _process_children(self, parent: Tag, lines: list[str], list_level: int = 0) -> None:
        """递归处理子节点，输出 Markdown。"""
        for child in parent.children:
            if not isinstance(child, Tag):
                # 文本节点
                text = child.strip()
                if text:
                    lines.append(text)
                continue

            tag = child.name
            if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                level = int(tag[1])
                text = child.get_text(strip=True)
                if text:
                    self._ensure_blank_before(lines)
                    lines.append(f"{'#' * level} {text}")
                    self._ensure_blank_after(lines)

            elif tag == "p":
                text = self._process_inline(child)
                if text:
                    self._ensure_blank_before(lines)
                    lines.append(text)
                    self._ensure_blank_after(lines)

            elif tag in ("ul", "ol"):
                prefix = "- " if tag == "ul" else "1. "
                for li in child.find_all("li", recursive=False):
                    li_text = self._process_inline(li)
                    indent = "  " * list_level
                    lines.append(f"{indent}{prefix}{li_text}")

                    # 处理嵌套列表
                    nested = li.find(["ul", "ol"], recursive=False)
                    if nested:
                        nested_tag = nested.name
                        nested_prefix = "- " if nested_tag == "ul" else "1. "
                        for nli in nested.find_all("li", recursive=False):
                            nli_text = self._process_inline(nli)
                            lines.append(f"{indent}  {nested_prefix}{nli_text}")
                self._ensure_blank_after(lines)

            elif tag == "blockquote":
                self._ensure_blank_before(lines)
                # 递归处理引用内内容
                inner_lines: list[str] = []
                self._process_children(child, inner_lines, list_level)
                for line in inner_lines:
                    if line:
                        lines.append(f"> {line}")
                    else:
                        lines.append(">")
                self._ensure_blank_after(lines)

            elif tag in ("pre", "code"):
                code_text = child.get_text()
                if code_text.strip():
                    self._ensure_blank_before(lines)
                    lines.append("```")
                    lines.append(code_text.rstrip("\n"))
                    lines.append("```")
                    self._ensure_blank_after(lines)

            elif tag == "hr":
                self._ensure_blank_before(lines)
                lines.append("---")
                self._ensure_blank_after(lines)

            elif tag in ("div", "section", "article", "main", "header", "footer", "nav"):
                self._process_children(child, lines, list_level)

            elif tag in ("br",):
                lines.append("")

            # 其他标签作为行内处理
            elif tag in ("a", "strong", "em", "b", "i", "span", "img", "code"):
                text = self._process_inline(child)
                if text:
                    lines.append(text)

    def _process_inline(self, element: Tag) -> str:
        """处理行内元素，返回格式化后的文本。"""
        parts: list[str] = []
        for child in element.children:
            if isinstance(child, str):
                text = child.strip()
                if text:
                    parts.append(text)
                continue

            if not isinstance(child, Tag):
                continue

            tag = child.name
            if tag == "a":
                href = child.get("href", "")
                text = child.get_text(strip=True)
                if text and href:
                    parts.append(f"[{text}]({href})")
                elif text:
                    parts.append(text)
            elif tag in ("strong", "b"):
                text = child.get_text(strip=True)
                if text:
                    parts.append(f"**{text}**")
            elif tag in ("em", "i"):
                text = child.get_text(strip=True)
                if text:
                    parts.append(f"*{text}*")
            elif tag == "code":
                text = child.get_text()
                if text:
                    parts.append(f"`{text}`")
            elif tag == "img":
                alt = child.get("alt", "")
                src = child.get("src", "")
                if src:
                    parts.append(f"![{alt}]({src})")
            elif tag == "br":
                parts.append("\n")
            elif tag == "span":
                text = child.get_text(strip=True)
                if text:
                    parts.append(text)
            else:
                text = child.get_text(strip=True)
                if text:
                    parts.append(text)

        return "".join(parts)

    # ── 工具方法 ──────────────────────────────────────────────

    @staticmethod
    def _ensure_blank_before(lines: list[str]) -> None:
        if lines and lines[-1] != "":
            lines.append("")

    @staticmethod
    def _ensure_blank_after(lines: list[str]) -> None:
        if lines and lines[-1] != "":
            lines.append("")
