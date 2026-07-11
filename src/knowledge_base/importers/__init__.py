"""文档导入引擎：支持 PDF（PyMuPDF/MinerU）、DOCX、EPUB、TXT、HTML 格式的导入和 Markdown 转换。"""

# ---------------------------------------------------------------------------
# 核心基类（无第三方依赖，始终可用）
# ---------------------------------------------------------------------------
from . import base
from .base import Importer, ImportResult, auto_register, get_importer, list_supported_extensions, register_importer

# ---------------------------------------------------------------------------
# 格式导入器（按需加载，缺失依赖时静默跳过）
# ---------------------------------------------------------------------------
_IMPORTER_MODULES = [
    "classifier",
    "docx_importer",
    "epub_importer",
    "html_importer",
    "router",
    "txt_importer",
    "pymupdf_importer",
    "mineru_importer",
]

for _mod_name in _IMPORTER_MODULES:
    try:
        __import__(f"knowledge_base.importers.{_mod_name}", fromlist=[""])
    except ImportError:
        pass
