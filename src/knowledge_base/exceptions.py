"""自定义异常和错误处理。"""


class KnowledgeBaseError(Exception):
    """所有知识库异常的基类。"""


class ConfigError(KnowledgeBaseError):
    """配置加载或验证错误。"""


class ImportError(KnowledgeBaseError):
    """文档导入过程中的错误。"""


class ClassifierError(ImportError):
    """文档类型分类错误。"""


class PDFImportError(ImportError):
    """PDF 导入错误。"""


class MinerUError(ImportError):
    """MinerU API 调用错误。"""


class KnowledgeGraphError(KnowledgeBaseError):
    """知识图谱操作错误。"""


class LLMConnectionError(KnowledgeBaseError):
    """LLM 连接错误。"""


class MCPError(KnowledgeBaseError):
    """MCP 服务器错误。"""
