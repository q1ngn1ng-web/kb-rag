"""Obsidian 输出模块。

将导入的文档转换为 Obsidian 兼容格式：
- YAML frontmatter（title、source、date、tags）
- [[wikilinks]] 双链（实体首次出现转换）
- 实体独立笔记文件（自动从文档正文提取上下文）
- Vault 目录写入（按文档标题隔离）
"""

from pathlib import Path
from datetime import datetime
import re
from typing import Any

from knowledge_base.utils import sanitize_filename, unique_path


def extract_entity_context(text: str, entity_name: str, max_sentences: int = 2) -> str:
    """从文档正文中提取包含实体名的上下文句子。

    Args:
        text: 文档正文文本。
        entity_name: 实体名称。
        max_sentences: 最多提取几个句子。

    Returns:
        由分号连接的上下文片段，无匹配时返回空字符串。
    """
    sentences = re.split(r'(?<=[。！？；])', text)
    contexts = []
    for sent in sentences:
        if entity_name in sent:
            contexts.append(sent.strip())
            if len(contexts) >= max_sentences:
                break
    return '；'.join(contexts) if contexts else ''


def generate_obsidian_markdown(
    markdown_body: str,
    metadata: dict,
    entities: list[dict] | None = None,
) -> str:
    """生成 Obsidian 兼容的 Markdown 内容。

    Args:
        markdown_body: 原始 Markdown 正文。
        metadata: 文档元数据（含 title、source_format 等）。
        entities: 实体列表，每个实体有 "name" 键。

    Returns:
        含 YAML frontmatter + wikilinks 的 Obsidian Markdown。
    """
    frontmatter = _generate_frontmatter(metadata)

    # 将实体提及转换为 wikilinks
    body = markdown_body
    if entities:
        body = _convert_entities_to_wikilinks(body, entities)

    return frontmatter + "\n" + body


def _generate_frontmatter(metadata: dict) -> str:
    """生成 YAML frontmatter。

    Frontmatter 字段：
    - title: 文档标题（必填）
    - source: 原始路径（选填）
    - date: 导入日期（必填）
    - tags: 从实体类型自动生成的标签（选填）
    - source_format: 原始格式（选填）
    """
    frontmatter: dict = {
        "title": metadata.get("title", "未命名文档"),
        "date": datetime.now().strftime("%Y-%m-%d"),
    }

    if "source_path" in metadata:
        frontmatter["source"] = metadata["source_path"]
    if "source_format" in metadata:
        frontmatter["source_format"] = metadata["source_format"]
    if "converter" in metadata:
        frontmatter["converter"] = metadata["converter"]

    tags = metadata.get("tags", [])
    if tags:
        frontmatter["tags"] = tags

    # 构建 YAML 字符串
    lines = ["---"]
    for key, value in frontmatter.items():
        if isinstance(value, list):
            items = "\n".join(f"  - {v}" for v in value)
            lines.append(f"{key}:\n{items}")
        elif isinstance(value, str) and (":" in value or value.startswith("http")):
            lines.append(f'{key}: "{value}"')
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


def _convert_entities_to_wikilinks(text: str, entities: list[dict]) -> str:
    """将实体的首次出现转换为 Obsidian [[wikilinks]]。"""
    sorted_entities = sorted(
        entities,
        key=lambda e: len(e.get("name", "")),
        reverse=True,
    )

    result = text
    for entity in sorted_entities:
        name = entity.get("name", "")
        if not name:
            continue

        aliases = entity.get("aliases", [])
        all_names = [name] + list(aliases)

        for n in all_names:
            if n in result:
                result = result.replace(n, f"[[{name}]]", 1)

    return result


# =============================================================================
# 实体独立笔记 (Task 6.5)
# =============================================================================


def generate_entity_note(
    entity: dict,
    source_doc_title: str,
    doc_body: str | None = None,
) -> str:
    """生成实体独立笔记的 Markdown 内容。

    Args:
        entity: 实体字典，含 name, type, aliases, description 等键。
        source_doc_title: 来源文档标题（用于 source wikilink）。
        doc_body: 可选的文档正文。传入后，description 为空时会自动提取上下文。

    Returns:
        完整的 .md 内容（含 frontmatter）。
    """
    name = entity.get("name", "未知实体")
    etype = entity.get("type", "实体")
    aliases = entity.get("aliases", [])
    description = entity.get("description", "")

    # description 为空时自动从文档正文提取上下文
    if not description and doc_body:
        description = extract_entity_context(doc_body, name)

    frontmatter: dict[str, Any] = {
        "title": name,
        "type": etype,
        "source": f"[[{source_doc_title}]]",
        "tags": ["entity"],
        "date": datetime.now().strftime("%Y-%m-%d"),
    }

    if aliases:
        frontmatter["aliases"] = aliases

    # 构建 frontmatter
    lines = ["---"]
    for key, value in frontmatter.items():
        if isinstance(value, list):
            items = "\n".join(f"  - {v}" for v in value)
            lines.append(f"{key}:\n{items}")
        elif isinstance(value, str) and (":" in value):
            lines.append(f'{key}: "{value}"')
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")

    # 正文
    body_parts: list[str] = []
    if description:
        body_parts.append(description)
    body_parts.append("")
    body_parts.append(f"来源文档：[[{source_doc_title}]]")

    return "\n".join(lines) + "\n\n" + "\n".join(body_parts)


# =============================================================================
# Vault 写入
# =============================================================================


def write_to_vault(
    markdown_content: str,
    metadata: dict,
    output_dir: str | Path,
    images: list[tuple[str, bytes]] | None = None,
    entities: list[dict] | None = None,
) -> Path:
    """写入 Markdown 文件到 Obsidian Vault（按文档标题隔离）。

    输出结构：
        vault/imports/{标题}/
            {源文件名}.md         ← 文档正文（含 wikilinks）
            实体A.md              ← 实体独立笔记
            实体B.md

    Args:
        markdown_content: Obsidian 格式的 Markdown 内容。
        metadata: 文档元数据（用于文件命名）。
        output_dir: Vault 输出目录（如 vault/imports）。
        images: 可选的图片列表 (filename, bytes)。
        entities: 可选的实体列表，自动生成独立笔记文件。

    Returns:
        写入的文档正文文件路径。
    """
    output_dir = Path(output_dir)

    # 按文档标题创建隔离文件夹（6.4）
    title = metadata.get("title", "untitled")
    safe_title = sanitize_filename(title)
    doc_dir = unique_path(output_dir, safe_title)  # 自动处理冲突（6.6）

    # 对于唯一的文件夹，doc_dir 就是最终路径
    # 但如果文件夹已存在且包含文件，unique_path 返回新路径
    doc_dir.mkdir(parents=True, exist_ok=True)

    # 写入文档正文（以源文件名命名）
    source_path = metadata.get("source_path", "")
    source_stem = Path(source_path).stem if source_path else safe_title
    safe_stem = sanitize_filename(source_stem)
    file_path = doc_dir / f"{safe_stem}.md"
    if file_path.exists():
        file_path = unique_path(doc_dir, safe_stem, ".md")
    file_path.write_text(markdown_content, encoding="utf-8")

    # 写入图片
    if images:
        assets_dir = doc_dir / "_assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        for img_name, img_data in images:
            (assets_dir / img_name).write_bytes(img_data)

    # 写入实体独立笔记（6.5）
    if entities:
        _write_entity_notes(doc_dir, entities, title, doc_body=markdown_content)

    return file_path


def _write_entity_notes(
    doc_dir: Path,
    entities: list[dict],
    source_doc_title: str,
    doc_body: str | None = None,
) -> list[Path]:
    """在文档文件夹下写入实体独立笔记。"""
    written: list[Path] = []
    for entity in entities:
        name = entity.get("name", "")
        if not name:
            continue

        content = generate_entity_note(entity, source_doc_title, doc_body)
        safe_name = sanitize_filename(name)
        note_path = unique_path(doc_dir, safe_name, ".md")
        note_path.write_text(content, encoding="utf-8")
        written.append(note_path)
    return written


def sync_to_vault(
    markdown_body: str,
    metadata: dict,
    entities: list[dict] | None = None,
    images: list[tuple[str, bytes]] | None = None,
    vault_path: str | Path | None = None,
) -> Path | None:
    """一站式方法：生成 Obsidian Markdown 并写入 Vault。

    从 config.yaml 读取 vault_path（可被 vault_path 参数覆盖），
    如果 vault_path 未配置则不做操作并返回 None。
    """
    config = _load_config_simple()

    if vault_path:
        vault_dir = Path(vault_path)
    else:
        vault_dir_str = config.get("obsidian", {}).get("vault_path", "")
        if not vault_dir_str:
            return None
        vault_dir = Path(vault_dir_str)

    output_subdir = config.get("obsidian", {}).get("output_subdir", "imports")
    output_dir = vault_dir / output_subdir

    content = generate_obsidian_markdown(markdown_body, metadata, entities)
    return write_to_vault(content, metadata, output_dir, images, entities)


def _load_config_simple() -> dict:
    """简单的配置加载器，如果 yaml 缺失不会崩溃。"""
    try:
        from knowledge_base.config import load_config

        return load_config()
    except Exception:
        return {}
