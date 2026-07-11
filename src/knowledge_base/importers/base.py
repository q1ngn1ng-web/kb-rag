"""抽象基类 Importer 和格式注册模式。"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar


class ImportResult:
    """文档导入结果。"""

    def __init__(
        self,
        *,
        markdown: str,
        metadata: dict,
        success: bool = True,
        error: str | None = None,
        images: list[tuple[str, bytes]] | None = None,
    ) -> None:
        self.markdown = markdown
        self.metadata = metadata
        self.success = success
        self.error = error
        self.images = images or []

    def __bool__(self) -> bool:
        return self.success


class Importer(ABC):
    """文档导入器抽象基类。

    所有格式特定的导入器继承此类，实现 ``convert`` 方法。
    """

    # 子类覆盖：支持的文件扩展名列表（如 [".pdf", ".PDF"]）
    extensions: ClassVar[list[str]] = []

    @abstractmethod
    def convert(self, file_path: Path) -> ImportResult:
        """将给定文件转换为 Markdown。

        Args:
            file_path: 输入文件路径。

        Returns:
            ImportResult 包含 Markdown 文本和元数据。
        """
        ...


# =============================================================================
# 格式注册
# =============================================================================

_importers: dict[str, type[Importer]] = {}


def register_importer(ext: str, cls: type[Importer]) -> None:
    """注册导入器到扩展名映射。"""
    _importers[ext.lower()] = cls


def get_importer(ext: str) -> type[Importer] | None:
    """获取指定扩展名对应的导入器类。"""
    return _importers.get(ext.lower())


def list_supported_extensions() -> list[str]:
    """返回所有已注册的扩展名列表。"""
    return list(_importers.keys())


def auto_register(cls: type[Importer]) -> type[Importer]:
    """装饰器：自动将导入器注册到其声明的扩展名列表。"""
    for ext in cls.extensions:
        register_importer(ext, cls)
    return cls
