"""文档导入路由：根据文档类型自动选择 PyMuPDF 或 MinerU 引擎。"""

from pathlib import Path

from .base import ImportResult
from .classifier import PDFClassifier

# ---------------------------------------------------------------------------
# ImportRouter — Tasks 2.5 + 2.6
# ---------------------------------------------------------------------------


class ImportRouter:
    """PDF 导入路由。

    根据 PDF 分类器结果 + 配置策略，自动选择：
    - ``pymupdf``: 普通 PDF（简单文档）
    - ``mineru``:  学术论文 / 扫描件等复杂文档

    配置项（通过 ``config["pdf"]["routing"]``）:
        auto (bool):   是否自动分类选择引擎（默认 True）
        engine (str | None):  引擎覆盖值 "pymupdf" | "mineru" | None
    """

    def __init__(self, config: dict) -> None:
        self.config = config
        pdf_config = config.get("pdf", {})
        classifier_config = pdf_config.get("classifier", {})
        self.classifier = PDFClassifier(classifier_config)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def route(self, file_path: Path) -> str:
        """确定使用哪个引擎处理指定的 PDF 文件。

        决策优先级:
        1. 配置显式指定引擎 (``config.pdf.routing.engine``)
        2. 自动模式 (``config.pdf.routing.auto``) → 运行分类器
        3. 默认回退 → pymupdf

        Args:
            file_path: PDF 文件路径。

        Returns:
            ``"pymupdf"`` 或 ``"mineru"``。

        Raises:
            ClassifierError: 分类过程出错（文件不存在 / 无法读取等）。
        """
        routing_cfg = self.config.get("pdf", {}).get("routing", {})
        engine_override = routing_cfg.get("engine")
        auto_mode = routing_cfg.get("auto", True)

        # 1. 配置显式覆盖
        if engine_override in ("pymupdf", "mineru"):
            return engine_override  # type: ignore[return-value]

        # 2. 自动分类模式
        if auto_mode:
            result = self.classifier.classify(file_path)
            # academic / scanned → mineru（高精度 OCR 引擎）
            if result.doc_type in ("academic", "scanned"):
                return "mineru"
            return "pymupdf"

        # 3. 默认回退（auto=False 且无覆盖时）
        return "pymupdf"

    def import_document(self, file_path: Path) -> ImportResult:
        """路由到正确的引擎并执行导入。

        Args:
            file_path: PDF 文件路径。

        Returns:
            ImportResult 包含转换后的 Markdown 文本及元数据。
            任何异常（包括分类失败、导入器缺失等）均被捕获并以
            ``success=False`` 的 ImportResult 形式返回。
        """
        try:
            engine = self.route(file_path)
        except Exception as exc:
            return ImportResult(
                markdown="",
                metadata={"file": str(file_path)},
                success=False,
                error=f"文档路由失败: {exc}",
            )

        if engine == "pymupdf":
            return self._import_with_pymupdf(file_path)
        return self._import_with_mineru(file_path)

    # ------------------------------------------------------------------
    # 内部引擎调用
    # ------------------------------------------------------------------

    def _import_with_pymupdf(self, file_path: Path) -> ImportResult:
        """使用 PyMuPDF 引擎（普通文档通道）。"""
        try:
            from knowledge_base.importers.pymupdf_importer import (
                PyMuPDFImporter,
            )
        except ImportError as exc:
            return ImportResult(
                markdown="",
                metadata={"converter": "pymupdf", "file": str(file_path)},
                success=False,
                error=f"PyMuPDF 导入器未找到: {exc}",
            )

        importer = PyMuPDFImporter()
        return importer.convert(file_path)

    def _import_with_mineru(self, file_path: Path) -> ImportResult:
        """使用 MinerU 引擎（学术/扫描文档通道）。"""
        try:
            from knowledge_base.importers.mineru_importer import (
                MinerUImporter,
            )
        except ImportError as exc:
            return ImportResult(
                markdown="",
                metadata={"converter": "mineru", "file": str(file_path)},
                success=False,
                error=f"MinerU 导入器未找到: {exc}",
            )

        importer = MinerUImporter(self.config.get("mineru", {}))
        return importer.convert(file_path)
