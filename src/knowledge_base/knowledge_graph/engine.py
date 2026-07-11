"""KnowledgeGraphEngine 抽象接口 — 隔离 LightRAG 依赖。"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class KnowledgeGraphEngine(ABC):
    """知识图谱引擎抽象接口。

    所有知识图谱操作都通过此接口完成，避免直接依赖 LightRAG。
    支持文档索引、查询、实体管理、导出等核心功能。
    """

    @abstractmethod
    def index_document(self, doc_id: str, content: str, metadata: dict | None = None) -> int:
        """索引一篇文档到知识图谱中。

        Args:
            doc_id: 文档唯一标识符。
            content: 文档内容（Markdown 文本）。
            metadata: 可选的附加元数据（如标题、来源路径等）。

        Returns:
            提取的实体数量。
        """
        ...

    @abstractmethod
    def query(self, question: str, mode: str = "hybrid") -> str:
        """查询知识图谱。

        Args:
            question: 自然语言查询。
            mode: 检索模式 — "local"（实体级）/ "global"（关系级）/ "hybrid"（混合，默认）/ "mix"（混合 + 原文块向量检索）。

        Returns:
            包含上下文的回答文本。
        """
        ...

    @abstractmethod
    def list_documents(self) -> list[dict]:
        """返回所有已索引文档的列表。

        Returns:
            每项包含文档元数据（id, path 等）的字典列表。
        """
        ...

    @abstractmethod
    def get_entities(self, doc_id: str | None = None) -> list[dict]:
        """获取知识图谱中的实体列表。

        Args:
            doc_id: 可选，按文档 ID 筛选。

        Returns:
            实体字典列表（含名称、类型、别名等）。
        """
        ...

    @abstractmethod
    def delete_document(self, doc_id: str) -> bool:
        """从知识图谱中删除指定文档及其相关实体。

        Args:
            doc_id: 文档唯一标识符。


        Returns:
            删除成功返回 True。
        """
        ...

    @abstractmethod
    def export_json(self, output_path: str | Path) -> Path:
        """将知识图谱导出为 JSON 文件。

        Args:
            output_path: 输出文件路径。

        Returns:
            写出的文件路径。
        """
        ...
