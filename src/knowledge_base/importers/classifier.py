"""PDF 文档类型分类器：基于启发式检测区分简单/学术/扫描 PDF。"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from knowledge_base.exceptions import ClassifierError


# 单个 Unicode 公式符号（不含 $，$ 在 _calculate_formula_density 中单独统计）
FORMULA_CHARS: set[str] = set("∑∫∂παβ→∈∞")

# LaTeX 环境标记（需要子串匹配）
LATEX_ENV_MARKERS: list[str] = [r"\begin", r"\end"]

# 学术关键词（英文 + 中文）
ACADEMIC_KEYWORDS: list[str] = [
    # English
    "Abstract",
    "Introduction",
    "Methodology",
    "Method",
    "Conclusion",
    "References",
    "Bibliography",
    "Theorem",
    "Lemma",
    "Proof",
    "Corollary",
    "Definition",
    "Proposition",
    "Hypothesis",
    "Experiment",
    "Evaluation",
    "Results",
    "Discussion",
    "Related Work",
    "Proposed",
    "Algorithm",
    # Chinese
    "摘要",
    "引言",
    "介绍",
    "方法",
    "实验",
    "结论",
    "参考文献",
    "定理",
    "证明",
    "算法",
]


@dataclass
class ClassificationResult:
    """文档分类结果。

    Attributes:
        doc_type: 文档类型 — "simple" | "academic" | "scanned"
        confidence: 置信度 (0.0 ~ 1.0)
        details: 分类依据的详细特征数据。
    """

    doc_type: str
    confidence: float
    details: dict = field(default_factory=dict)


class PDFClassifier:
    """PDF 文档类型分类器。

    使用启发式方法检测文档类型：
    1. 扫描件检测 — 检查是否有文本层
    2. 公式符号密度 — Unicode 数学符号 + LaTeX 环境标记
    3. 引用标记计数 — [1], [2,3] 等模式
    4. 学术关键词统计 — 英文/中文典型学术词汇

    配置项（通过 ``config`` 字典传入）:
        formula_density_threshold (float): 公式密度阈值，默认 0.02
        min_citation_count (int): 最小引用标记数，默认 3
        min_academic_keywords (int): 最少学术关键词数，默认 2
    """

    def __init__(self, config: dict | None = None) -> None:
        config = config or {}
        self.formula_density_threshold = config.get(
            "formula_density_threshold", 0.02
        )
        self.min_citation_count = config.get("min_citation_count", 3)
        self.min_academic_keywords = config.get("min_academic_keywords", 2)
        self._max_pages = 10

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify(self, file_path: Path) -> ClassificationResult:
        """对 PDF 文档进行类型分类。

        流程：
        1. 检查是否为扫描件（无文本层）
        2. 提取前 10 页文本
        3. 计算公式符号密度、引用标记数、学术关键词数
        4. 综合判定文档类型

        Args:
            file_path: PDF 文件路径。

        Returns:
            ClassificationResult 包含 doc_type、confidence 和 details。

        Raises:
            ClassifierError: 文件不存在、PyMuPDF 未安装或 PDF 解析失败。
        """
        if not file_path.exists():
            raise ClassifierError(f"文件不存在: {file_path}")

        try:
            import fitz  # noqa: F401
        except ImportError:
            raise ClassifierError("PyMuPDF 未安装，无法分类 PDF")

        # ---- 1. 扫描件检测 ----
        try:
            is_scanned = self._check_scanned(file_path)
        except Exception as e:
            raise ClassifierError(f"扫描件检测失败: {e}") from e

        if is_scanned:
            return ClassificationResult(
                doc_type="scanned",
                confidence=0.95,
                details={"scanned": True, "pages_without_text": True},
            )

        # ---- 2. 提取文本 ----
        try:
            text, page_count = self._extract_text(file_path)
        except Exception as e:
            raise ClassifierError(f"文本提取失败: {e}") from e

        if not text.strip():
            return ClassificationResult(
                doc_type="simple",
                confidence=0.5,
                details={"text_empty": True, "page_count": page_count},
            )

        # ---- 3. 计算特征 ----
        formula_density = self._calculate_formula_density(text)
        citation_count = self._count_citations(text)
        keyword_count = self._count_academic_keywords(text)

        details: dict = {
            "text_length": len(text),
            "page_count": page_count,
            "formula_density": round(formula_density, 6),
            "citation_count": citation_count,
            "academic_keyword_count": keyword_count,
        }

        # ---- 4. 分类决策 ----
        return self._decide(formula_density, citation_count, keyword_count, details)

    # ------------------------------------------------------------------
    # 扫描件检测 (Task 2.6)
    # ------------------------------------------------------------------

    def _check_scanned(self, file_path: Path) -> bool:
        """使用 PyMuPDF 检查 PDF 是否有文本层。

        Args:
            file_path: PDF 文件路径。

        Returns:
            True 如果所有检查的页面都没有可提取文本（极可能是扫描件）。
        """
        import fitz

        doc = fitz.open(file_path)
        try:
            total_pages = doc.page_count
            if total_pages == 0:
                return False

            # 最多检查 50 页；只要有任一页有文本则判定为非扫描件
            pages_to_check = min(total_pages, 50)

            for i in range(pages_to_check):
                page = doc[i]
                text = page.get_text().strip()
                if text:
                    return False

            return True
        finally:
            doc.close()

    # ------------------------------------------------------------------
    # 文本提取
    # ------------------------------------------------------------------

    def _extract_text(self, file_path: Path) -> tuple[str, int]:
        """从 PDF 中提取前 N 页文本。

        Returns:
            (文本内容, 文档总页数) 元组。
        """
        import fitz

        doc = fitz.open(file_path)
        try:
            page_count = doc.page_count
            pages_to_read = min(page_count, self._max_pages)

            text_parts: list[str] = []
            for i in range(pages_to_read):
                page = doc[i]
                text_parts.append(page.get_text())

            return "\n".join(text_parts), page_count
        finally:
            doc.close()

    # ------------------------------------------------------------------
    # 启发式检测方法
    # ------------------------------------------------------------------

    def _calculate_formula_density(self, text: str) -> float:
        """计算公式符号密度。

        密度 = (公式符号数 + LaTeX 环境标记数) / 总字符数

        公式符号包括:
        - Unicode 数学符号: ∑, ∫, ∂, π, α, β, →, ∈, ∞
        - LaTeX 行内/行间公式标记: $
        - LaTeX 环境标记: \\begin, \\end
        """
        if not text:
            return 0.0

        total_chars = len(text)
        if total_chars == 0:
            return 0.0

        formula_count = 0

        # 单个公式符号
        for ch in text:
            if ch in FORMULA_CHARS or ch == "$":
                formula_count += 1

        # LaTeX 环境标记
        for marker in LATEX_ENV_MARKERS:
            formula_count += text.count(marker)

        return formula_count / total_chars

    def _count_citations(self, text: str) -> int:
        """统计引用标记数量。

        匹配模式: [1], [12], [1,2], [1-3], [1, 2, 3] 等。

        Returns:
            引用标记的个数。
        """
        pattern = r"\[\d+(?:\s*[,–\-]\s*\d+)*\]"
        return len(re.findall(pattern, text))

    def _count_academic_keywords(self, text: str) -> int:
        """统计学术关键词在文本中的总出现次数（大小写不敏感）。"""
        count = 0
        text_lower = text.lower()
        for keyword in ACADEMIC_KEYWORDS:
            count += text_lower.count(keyword.lower())
        return count

    # ------------------------------------------------------------------
    # 决策逻辑
    # ------------------------------------------------------------------

    def _decide(
        self,
        formula_density: float,
        citation_count: int,
        keyword_count: int,
        details: dict,
    ) -> ClassificationResult:
        """根据特征值做出最终分类决策。"""
        is_formula_heavy = formula_density >= self.formula_density_threshold
        has_citations = citation_count >= self.min_citation_count
        has_keywords = keyword_count >= self.min_academic_keywords

        # 强学术信号: 公式密集 + 引用或关键词
        if is_formula_heavy and (has_citations or has_keywords):
            confidence = 0.85
            if has_citations and has_keywords:
                confidence = 0.95
            return ClassificationResult(
                doc_type="academic", confidence=confidence, details=details
            )

        # 中等学术信号: 引用 + 关键词（无公式）
        if has_citations and has_keywords:
            return ClassificationResult(
                doc_type="academic", confidence=0.8, details=details
            )

        # 弱学术信号: 仅有引用或仅有关键词
        if has_citations or has_keywords:
            return ClassificationResult(
                doc_type="academic", confidence=0.6, details=details
            )

        # 无学术信号 → 简单文档
        return ClassificationResult(
            doc_type="simple", confidence=0.8, details=details
        )
