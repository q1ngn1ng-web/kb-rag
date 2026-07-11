"""通用工具函数。"""

import re
from pathlib import Path
from typing import Any

# YAML frontmatter 分隔符
_FM_DELIMITER = "---"


def strip_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """从 Markdown 文本中剥离 YAML frontmatter。

    Args:
        text: 可能包含 frontmatter 的 Markdown 文本。

    Returns:
        (frontmatter 字典, 正文内容) 的元组。
        如果没有 frontmatter 则返回 ({}, text)。
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != _FM_DELIMITER:
        return {}, text

    # 找到 closing delimiter
    end_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == _FM_DELIMITER:
            end_idx = i
            break

    if end_idx == -1:
        # 只有 opening 没有 closing，不算 frontmatter
        return {}, text

    frontmatter = _parse_simple_yaml(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1:])
    return frontmatter, body


def _parse_simple_yaml(lines: list[str]) -> dict[str, Any]:
    """简单的 YAML frontmatter 解析（不依赖 PyYAML，仅处理标量值）。"""
    result: dict[str, Any] = {}
    current_key: str | None = None
    current_list: list[str] | None = None

    for line in lines:
        # 跳过空行
        if not line.strip():
            continue

        # 列表项（缩进的 - value）
        list_match = re.match(r"^\s+-\s+(.+)$", line)
        if list_match and current_key is not None:
            result.setdefault(current_key, [])
            if isinstance(result[current_key], list):
                result[current_key].append(list_match.group(1).strip())
            continue

        # 键值对
        kv_match = re.match(r"^(\w[\w_]*):\s*(.*)$", line)
        if kv_match:
            current_key = kv_match.group(1)
            value = kv_match.group(2).strip()
            if value:
                # 去除引号
                value = value.strip("\"'")
                result[current_key] = value
            else:
                # 值可能为空（后面可能跟列表）
                result[current_key] = []
            current_list = None

    return result


def sanitize_filename(name: str) -> str:
    """清理文件名，保留中文字符，替换文件系统不兼容字符。"""
    return re.sub(r'[\\/:*?"<>|]', "_", name)


def unique_path(base_dir: Path, name: str, suffix: str = "") -> Path:
    """生成不重复的文件/文件夹路径。

    如果 base_dir / name 已存在，自动添加 _2, _3 后缀。

    Args:
        base_dir: 父目录。
        name: 名称（不含后缀，用于文件夹；含后缀用于文件）。
        suffix: 文件后缀（如 ".md"），文件夹时留空。

    Returns:
        不存在的路径。
    """
    if suffix:
        # 文件路径
        target = base_dir / f"{name}{suffix}"
        stem = name
    else:
        # 文件夹路径
        target = base_dir / name
        stem = name

    if not target.exists():
        return target

    counter = 2
    while True:
        if suffix:
            target = base_dir / f"{stem}_{counter}{suffix}"
        else:
            target = base_dir / f"{stem}_{counter}"
        if not target.exists():
            return target
        counter += 1
