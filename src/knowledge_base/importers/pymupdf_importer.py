"""PyMuPDF 通道：普通 PDF 文档的 Markdown 转换。"""

from pathlib import Path

import fitz

from .base import Importer, ImportResult, auto_register


# 最小字体大小比例（与页面平均字体大小相比），超过此比例视为标题
HEADING_FONT_RATIO = 1.4


@auto_register
class PyMuPDFImporter(Importer):
    """基于 PyMuPDF (fitz) 的 PDF 导入器。

    适用于普通 PDF 文档（非扫描版）。对学术论文中常见的双栏布局、
    标题层级和简单表格提供基本支持。
    """

    extensions = [".pdf"]

    def convert(self, file_path: Path) -> ImportResult:
        """将 PDF 文件转换为 Markdown 文本。

        Args:
            file_path: 输入的 PDF 文件路径。

        Returns:
            ImportResult 包含 Markdown 文本、元数据及可能的错误信息。
        """
        if not file_path.exists():
            return ImportResult(
                markdown="",
                metadata={"converter": "pymupdf", "file": str(file_path)},
                success=False,
                error=f"文件不存在: {file_path}",
            )

        try:
            doc = fitz.open(file_path)
        except Exception as exc:
            return ImportResult(
                markdown="",
                metadata={"converter": "pymupdf", "file": str(file_path)},
                success=False,
                error=f"无法打开 PDF 文件: {exc}",
            )

        metadata = _extract_doc_metadata(doc, file_path)
        metadata["converter"] = "pymupdf"

        try:
            markdown_parts: list[str] = []
            total_pages = len(doc)

            for page_num in range(total_pages):
                page = doc[page_num]
                page_md = _convert_page(page, page_num, total_pages)
                markdown_parts.append(page_md)

            markdown_text = "\n\n".join(part for part in markdown_parts if part.strip())

            doc.close()
            return ImportResult(
                markdown=markdown_text,
                metadata=metadata,
                success=True,
            )
        except Exception as exc:
            doc.close()
            return ImportResult(
                markdown="",
                metadata=metadata,
                success=False,
                error=f"PDF 转换失败: {exc}",
            )


# =============================================================================
# 内部辅助函数
# =============================================================================


def _extract_doc_metadata(doc: fitz.Document, file_path: Path) -> dict:
    """提取文档级元数据。"""
    meta = {
        "source": str(file_path),
        "filename": file_path.name,
        "page_count": len(doc),
    }

    # PDF 内置元数据（标题、作者、主题等）
    pdf_meta = doc.metadata or {}
    if pdf_meta.get("title"):
        meta["title"] = pdf_meta["title"]
    if pdf_meta.get("author"):
        meta["author"] = pdf_meta["author"]
    if pdf_meta.get("subject"):
        meta["subject"] = pdf_meta["subject"]

    return meta


def _convert_page(page: fitz.Page, page_num: int, total_pages: int) -> str:
    """将单页 PDF 转换为 Markdown 片段。"""
    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
    if not blocks:
        return ""

    # 收集页面上所有文本块以计算平均字体大小
    all_spans = _collect_spans(blocks)
    avg_font_size = _average_font_size(all_spans) if all_spans else 12.0

    lines: list[str] = []
    table_mode = False

    for block in blocks:
        block_type = block.get("type")
        if block_type == 0:  # 文本块
            block_text, is_table = _process_text_block(
                block, avg_font_size, page.rect.width
            )
            if is_table:
                if not table_mode:
                    table_mode = True
                lines.append(block_text)
            else:
                if table_mode:
                    table_mode = False
                lines.append(block_text)

        elif block_type == 1:  # 图片块
            # PyMuPDF 图片块处理（保留替代文本或占位符）
            lines.append(_process_image_block(block))

    # 过滤空行，但保留段落间距
    content = "\n".join(lines)

    # 页眉/页脚页码标记（首页通常没有页码）
    if page_num > 0 and page_num < total_pages - 1:
        content = _strip_page_number(content, page_num + 1)

    return content.strip()


def _collect_spans(blocks: list[dict]) -> list[dict]:
    """收集所有文本 span 以便进行字体统计。"""
    spans: list[dict] = []
    for block in blocks:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if span.get("text", "").strip():
                    spans.append(span)
    return spans


def _average_font_size(spans: list[dict]) -> float:
    """计算页面上的平均字体大小（按文本长度加权）。"""
    total_weight = 0.0
    total_size = 0.0
    for span in spans:
        length = len(span.get("text", ""))
        total_size += span.get("size", 12.0) * length
        total_weight += length
    return total_size / total_weight if total_weight > 0 else 12.0


def _process_text_block(
    block: dict, avg_font_size: float, page_width: float
) -> tuple[str, bool]:
    """将单个文本块转换为 Markdown。

    Returns:
        (markdown_text, is_table) 元组。
    """
    lines_in_block = block.get("lines", [])
    if not lines_in_block:
        return ("", False)

    # 检测是否为表格（多行且每行以空格分隔的列对齐模式）
    if _is_simple_table(lines_in_block, page_width):
        table_md = _format_as_table(lines_in_block)
        return (table_md, True)

    # 普通文本处理
    paragraph_parts: list[str] = []
    heading_detected = False

    for line in lines_in_block:
        spans = line.get("spans", [])
        if not spans:
            continue

        # 合并同一行的 span 文本
        line_text = "".join(span.get("text", "") for span in spans)

        # 检测标题：字体大小超过平均值的 1.4 倍，或者字体为粗体
        max_span = max(spans, key=lambda s: s.get("size", 0))
        font_size = max_span.get("size", 0)
        is_bold = any(
            "bold" in span.get("font", "").lower()
            or "heavy" in span.get("font", "").lower()
            for span in spans
        )

        # 短行且字体较大 ≈ 标题
        is_heading = (
            font_size >= avg_font_size * HEADING_FONT_RATIO
            and len(line_text.strip()) < 200
        )

        if is_heading:
            heading_detected = True
            level = _infer_heading_level(font_size, avg_font_size)
            prefix = "#" * level
            paragraph_parts.append(f"{prefix} {line_text.strip()}")
        else:
            paragraph_parts.append(line_text.strip())

    text = "\n\n".join(paragraph_parts) if heading_detected else " ".join(paragraph_parts)
    return (text, False)


def _infer_heading_level(font_size: float, avg_font_size: float) -> int:
    """根据字体大小推断标题层级（1-3）。"""
    ratio = font_size / avg_font_size
    if ratio >= 2.0:
        return 1
    elif ratio >= 1.6:
        return 2
    else:
        return 3


def _is_simple_table(lines: list[dict], page_width: float) -> bool:
    """启发式检测简单表格。

    判断条件：
    - 至少 2 行
    - 每行包含多个由空格分隔的"列"
    - 列数在各行之间基本一致
    """
    if len(lines) < 2:
        return False

    # 检查每行是否包含多个空格分隔的片段
    col_counts: list[int] = []
    for line in lines:
        spans = line.get("spans", [])
        if len(spans) < 2:
            return False
        # 检查 span 间的水平间距是否有明显分隔
        x_positions = [s.get("bbox", [0])[0] for s in spans]
        # 如果 span 都集中在左半页，可能只是普通列表
        if all(x < page_width * 0.5 for x in x_positions):
            return False
        col_counts.append(len(spans))

    # 列数应基本一致（允许 1 行差异）
    if not col_counts:
        return False
    return max(col_counts) - min(col_counts) <= 1


def _format_as_table(lines: list[dict]) -> str:
    """将检测到的表格行格式化为 Markdown 表格。"""
    rows: list[list[str]] = []
    for line in lines:
        spans = line.get("spans", [])
        row = [span.get("text", "").strip() for span in spans]
        rows.append(row)

    if not rows:
        return ""

    # 构建 Markdown 表格
    col_count = max(len(r) for r in rows)
    # 补齐短行
    for row in rows:
        while len(row) < col_count:
            row.append("")

    lines_out: list[str] = []
    # 表头（第一行）
    lines_out.append("| " + " | ".join(rows[0]) + " |")
    # 分隔行
    lines_out.append("| " + " | ".join(["---"] * col_count) + " |")
    # 数据行
    for row in rows[1:]:
        lines_out.append("| " + " | ".join(row) + " |")

    return "\n".join(lines_out)


def _process_image_block(block: dict) -> str:
    """处理图片块，生成 Markdown 图片引用。"""
    # 从 block 中提取可能的图片信息
    image_info = ""
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            text = span.get("text", "").strip()
            if text:
                image_info = text
                break
        if image_info:
            break

    if image_info:
        return f"*{image_info}*"
    return "[]"


def _strip_page_number(text: str, page_num: int) -> str:
    """去除独立的页码标记（页眉/页脚）。"""
    import re

    # 尝试匹配单独一行的页码
    pattern = re.compile(rf"^\s*{re.escape(str(page_num))}\s*$", re.MULTILINE)
    return pattern.sub("", text)
