"""统一的 Markdown 输出，支持 YAML frontmatter 和图片处理。"""

from pathlib import Path
from datetime import datetime

from .base import ImportResult


def generate_frontmatter(result: ImportResult, source_path: Path) -> str:
    """从 ImportResult 元数据生成 YAML frontmatter 字符串。

    Args:
        result: 包含元数据字段的 ImportResult。
        source_path: 原始源文件路径（用于回退标题）。

    Returns:
        YAML frontmatter 块字符串（用 ``---`` 包围）。
    """
    metadata = result.metadata
    frontmatter: dict[str, object] = {
        "title": metadata.get("title", source_path.stem),
        "source_format": source_path.suffix.lower(),
        "source_path": str(source_path),
        "import_date": datetime.now().isoformat(),
    }

    if "page_count" in metadata:
        frontmatter["page_count"] = metadata["page_count"]
    if "converter" in metadata:
        frontmatter["converter"] = metadata["converter"]

    lines = ["---"]
    for key, value in frontmatter.items():
        if isinstance(value, str):
            # 对包含 YAML 特殊字符的字符串加引号
            if _needs_quoting(value):
                lines.append(f'{key}: "{value}"')
            else:
                lines.append(f"{key}: {value}")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


def _needs_quoting(value: str) -> bool:
    """检查 YAML 标量值是否需要双引号。"""
    special_chars = frozenset(":#{}[]&*?|-<>=!%@`")
    return any(c in value for c in special_chars) or not value.strip()


def _unique_filename(output_dir: Path, filename: str) -> Path:
    """如果文件名已存在，通过添加日期后缀生成唯一的文件名。"""
    output_path = output_dir / filename
    if not output_path.exists():
        return output_path
    stem = output_path.stem
    suffix = output_path.suffix
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return output_dir / f"{stem}_{timestamp}{suffix}"


def write_markdown(
    result: ImportResult,
    output_dir: Path,
    source_path: Path,
    images_subdir: str = "_assets",
) -> Path:
    """将 ImportResult 写入带有 YAML frontmatter 的 Markdown 文件。

    - 从 ``result.metadata`` 生成 YAML frontmatter。
    - 将提取的图片保存到 ``output_dir / images_subdir``。
    - 将 Markdown 正文中的图片引用重写为相对路径。
    - 通过添加日期后缀处理重复文件名。

    Args:
        result: 来自任何导入器的 ImportResult。
        output_dir: 根输出目录。
        source_path: 原始源文件路径（用于元数据和命名）。
        images_subdir: 提取图片的子目录名称。

    Returns:
        写入的 ``.md`` 文件路径。
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 生成 YAML frontmatter
    frontmatter = generate_frontmatter(result, source_path)

    # 确定输出文件名并处理重复
    output_filename = f"{source_path.stem}.md"
    output_path = _unique_filename(output_dir, output_filename)

    # 构建完整的 Markdown 内容（frontmatter + 正文）
    content = frontmatter + "\n\n" + (result.markdown or "")

    # 保存图片并重写 Markdown 正文中的图片引用
    if result.images:
        images_dir = output_dir / images_subdir
        images_dir.mkdir(parents=True, exist_ok=True)

        for img_name, img_data in result.images:
            # 处理重复的图片文件名
            img_path = _unique_filename(images_dir, img_name)
            # 确保父目录存在（处理 EPUB 等格式的内嵌子目录结构）
            img_path.parent.mkdir(parents=True, exist_ok=True)
            img_path.write_bytes(img_data)

            # 更新内容中的图片引用为相对路径
            if img_name != img_path.name:
                content = content.replace(
                    f"]({img_name})",
                    f"]({images_subdir}/{img_path.name})",
                )
            else:
                content = content.replace(
                    f"]({img_name})",
                    f"]({images_subdir}/{img_name})",
                )

    # 使用 UTF-8 编码写入输出文件
    output_path.write_text(content, encoding="utf-8")
    return output_path
