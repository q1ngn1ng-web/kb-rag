"""批量/目录导入，支持并行处理和进度报告。

用法::

    from knowledge_base.importers.batch import BatchImporter

    importer = BatchImporter(output_dir="./output", max_workers=4)
    results = importer.import_path("/path/to/files")
"""

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .base import get_importer, list_supported_extensions
from .markdown_writer import write_markdown
from ..progress import progress_iter, Timer, report_summary


class BatchImporter:
    """使用并行工作线程导入多个文件或整个目录。

    Args:
        output_dir: 转换后 Markdown 文件的根输出目录。
        max_workers: 最大并行工作线程数。
    """

    def __init__(
        self, output_dir: str | Path = "./output", max_workers: int = 4
    ) -> None:
        self.output_dir = Path(output_dir)
        self.max_workers = max_workers

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def import_path(
        self, path: str | Path, engine: str | None = None
    ) -> list[dict[str, Any]]:
        """导入单个文件或整个目录。

        Args:
            path: 文件路径或目录路径。
            engine: 可选的引擎覆盖（``'pymupdf'`` | ``'mineru'`` |
                    ``None`` 表示自动检测）。

        Returns:
            结果字典列表，每个字典包含以下键：
                - **file** (str): 源文件路径。
                - **status** (str): ``'ok'`` | ``'skip'`` | ``'error'``。
                - **error** (str | None): 错误信息（如有）。
                - **entities** (int): 实体数量占位符（未来使用）。
        """
        path_obj = Path(path)

        if path_obj.is_file():
            return [self.import_file(path_obj, engine=engine)]

        if path_obj.is_dir():
            return self.import_directory(path_obj, engine=engine)

        return [
            {
                "file": str(path_obj),
                "status": "error",
                "error": "Path does not exist",
                "entities": 0,
            }
        ]

    def import_file(
        self, file_path: Path, engine: str | None = None
    ) -> dict[str, Any]:
        """导入单个文件。

        步骤：
            1. 确定文件扩展名并查找已注册的导入器。
            2. 实例化导入器并调用 ``convert()``。
            3. 成功后，通过 ``write_markdown()`` 写入结果。

        Args:
            file_path: 源文件路径。
            engine: 可选的 PDF 路由引擎覆盖。

        Returns:
            单个结果字典。
        """
        ext = file_path.suffix.lower()
        importer_cls = get_importer(ext)

        if importer_cls is None:
            return {
                "file": str(file_path),
                "status": "skip",
                "error": f"No importer registered for extension '{ext}'",
                "entities": 0,
            }

        # --- 实例化导入器 ---
        # TODO: 当设置了 `engine` 时集成 PDF 文档路由器
        # 例如：if engine and ext == ".pdf": importer = get_pdf_router(engine)
        try:
            importer = importer_cls()
        except Exception as exc:
            return {
                "file": str(file_path),
                "status": "error",
                "error": f"Failed to instantiate importer: {exc}",
                "entities": 0,
            }

        # --- 转换 ---
        try:
            result = importer.convert(file_path)
        except Exception as exc:
            return {
                "file": str(file_path),
                "status": "error",
                "error": str(exc),
                "entities": 0,
            }

        if not result.success:
            return {
                "file": str(file_path),
                "status": "error",
                "error": result.error or "Unknown conversion error",
                "entities": 0,
            }

        # --- 写入输出 ---
        try:
            write_markdown(result, self.output_dir, file_path)
        except Exception as exc:
            return {
                "file": str(file_path),
                "status": "error",
                "error": f"Failed to write output: {exc}",
                "entities": 0,
            }

        return {
            "file": str(file_path),
            "status": "ok",
            "error": None,
            "entities": 0,
        }

    def import_directory(
        self, dir_path: Path, engine: str | None = None
    ) -> list[dict[str, Any]]:
        """递归导入目录中所有支持的文件。

        使用 :class:`ThreadPoolExecutor` 进行并行处理，通过
        :func:`progress_iter` 显示进度条，并在完成时打印摘要报告。

        Args:
            dir_path: 要扫描的目录路径。
            engine: 可选的引擎覆盖（传递给 :meth:`import_file`）。

        Returns:
            结果字典列表（每个文件一个）。
        """
        supported_exts = list_supported_extensions()

        # 收集所有匹配的文件（小写 + 大写变体）
        files: list[Path] = []
        for ext in supported_exts:
            files.extend(dir_path.rglob(f"*{ext}"))
            files.extend(dir_path.rglob(f"*{ext.upper()}"))
        files = sorted(set(files))

        if not files:
            print(f"No supported files found in {dir_path}")
            return []

        timer = Timer()
        timer.start()
        results: list[dict[str, Any]] = []

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_map = {
                executor.submit(self.import_file, f, engine=engine): f
                for f in files
            }

            for future in progress_iter(
                as_completed(future_map),
                desc="Importing files",
                total=len(files),
                unit="file",
            ):
                file_path = future_map[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as exc:
                    results.append(
                        {
                            "file": str(file_path),
                            "status": "error",
                            "error": str(exc),
                            "entities": 0,
                        }
                    )

        elapsed = timer.stop()

        success_count = sum(1 for r in results if r["status"] == "ok")
        fail_count = sum(1 for r in results if r["status"] == "error")
        total_entities = sum(r.get("entities", 0) for r in results)

        summary = report_summary(
            total_files=len(files),
            success_count=success_count,
            fail_count=fail_count,
            total_entities=total_entities,
            elapsed=elapsed,
        )
        print(summary)

        return results
