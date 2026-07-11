"""MinerU API 通道：学术/复杂 PDF 文档的高精度 Markdown 转换。"""

import json
import time
import uuid
from pathlib import Path

import httpx

from knowledge_base.exceptions import MinerUError
from .base import Importer, ImportResult, auto_register

# 默认轮询间隔（秒）
_POLL_INTERVAL = 5.0


@auto_register
class MinerUImporter(Importer):
    """基于 MinerU REST API 的 PDF 导入器。

    适用于学术论文、扫描版 PDF 等复杂文档。通过调用 MinerU 服务端
    完成高精度解析（包括 LaTeX 公式、表格、图片提取）。

    配置项（通过 config.yaml 的 ``mineru`` 段传入）：
        - api_url: MinerU 服务地址
        - api_key: 认证密钥
        - timeout: 请求超时秒数（默认 300）
    """

    extensions = [".pdf"]

    def __init__(self, config: dict | None = None) -> None:
        cfg = config or {}
        self.api_url = cfg.get("api_url", "").rstrip("/")
        self.api_key = cfg.get("api_key", "")
        self.timeout = cfg.get("timeout", 300)

    def convert(self, file_path: Path) -> ImportResult:
        """通过 MinerU API 将 PDF 转换为 Markdown。

        Args:
            file_path: 输入的 PDF 文件路径。

        Returns:
            ImportResult 包含 Markdown 文本、提取的图片及元数据。
        """
        metadata: dict = {
            "converter": "mineru",
            "source": str(file_path),
            "filename": file_path.name,
        }

        # ---- 前置校验 ----
        if not self.api_url:
            return ImportResult(
                markdown="",
                metadata=metadata,
                success=False,
                error=(
                    "MinerU API 未配置：请设置 config.yaml 中的 "
                    "mineru.api_url 和 mineru.api_key"
                ),
            )

        if not file_path.exists():
            return ImportResult(
                markdown="",
                metadata=metadata,
                success=False,
                error=f"文件不存在: {file_path}",
            )

        # ---- 执行 API 流程 ----
        try:
            task_id = self._upload(file_path)
            result_data = self._poll(task_id)
            markdown_text, images, extra_meta = self._parse_result(
                result_data, file_path
            )
            metadata.update(extra_meta)
            success = True
            error = None
        except MinerUError as exc:
            markdown_text = ""
            images = []
            error = str(exc)
            success = False
        except Exception as exc:
            markdown_text = ""
            images = []
            error = f"MinerU 处理异常: {exc}"
            success = False

        return ImportResult(
            markdown=markdown_text,
            metadata=metadata,
            success=success,
            error=error,
            images=images,
        )

    # =========================================================================
    # API 交互
    # =========================================================================

    def _headers(self) -> dict[str, str]:
        """构建请求头。"""
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _upload(self, file_path: Path) -> str:
        """提交 PDF 文件 URL 并获取 task_id。

        MinerU v4 API 接受文件 URL，不直接上传文件。
        对于本地文件，先上传到临时存储（未来可扩展），
        或使用 PyMuPDF 通道处理。

        端点: POST {api_url}/extract/task
        Body: {"url": "<file_url>", "model_version": "vlm"}
        """
        file_url = self._get_file_url(file_path)
        submit_url = f"{self.api_url}/extract/task"
        payload = {"url": file_url, "model_version": "vlm"}

        try:
            with httpx.Client(timeout=httpx.Timeout(self.timeout)) as client:
                resp = client.post(
                    submit_url,
                    headers={**self._headers(), "Content-Type": "application/json"},
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.TimeoutException:
            raise MinerUError("MinerU API 提交超时")
        except httpx.HTTPStatusError as exc:
            raise MinerUError(
                f"MinerU API 提交失败 (HTTP {exc.response.status_code}): "
                f"{exc.response.text[:200]}"
            )
        except Exception as exc:
            raise MinerUError(f"MinerU API 提交异常: {exc}")

        task_id = data.get("data", {}).get("task_id")
        if not task_id:
            raise MinerUError("MinerU API 返回中缺少 task_id")
        return str(task_id)

    def _get_file_url(self, file_path: Path) -> str:
        """获取文件的可访问 URL。

        对于本地文件，当前不支持直接上传到 MinerU v4。
        未来可扩展为上传到临时文件托管服务。
        """
        file_str = str(file_path)
        if file_str.startswith("http://") or file_str.startswith("https://"):
            return file_str
        if file_path.exists():
            raise MinerUError(
                f"MinerU v4 API 需要文件 URL，不支持直接上传本地文件。"
                f"请将文件托管到可访问的 URL 再提交，"
                f"或设置 config.yaml 中 pdf.routing.engine=pymupdf 使用本地解析。"
            )
        raise MinerUError(f"文件不存在: {file_path}")

    def _poll(self, task_id: str) -> dict:
        """轮询任务状态，等待处理完成。

        端点: GET {api_url}/extract/task/{task_id}
        返回的 data 中包含 state、full_zip_url（完成时）。
        """
        status_url = f"{self.api_url}/extract/task/{task_id}"
        deadline = time.monotonic() + self.timeout

        try:
            with httpx.Client(timeout=httpx.Timeout(max(self.timeout, 30))) as client:
                while time.monotonic() < deadline:
                    resp = client.get(status_url, headers=self._headers())
                    resp.raise_for_status()
                    data = resp.json()

                    task_data = data.get("data", {})
                    state = (task_data.get("state") or "").lower()

                    if state == "done":
                        return self._download_result(task_data)
                    if state in ("failed", "error"):
                        err_msg = (
                            task_data.get("err_msg")
                            or data.get("msg")
                            or "MinerU 处理失败"
                        )
                        raise MinerUError(err_msg)

                    time.sleep(_POLL_INTERVAL)

                raise MinerUError("MinerU API 处理超时")
        except MinerUError:
            raise
        except httpx.TimeoutException:
            raise MinerUError("MinerU API 状态查询超时")
        except Exception as exc:
            raise MinerUError(f"MinerU API 状态查询异常: {exc}")

    def _download_result(self, task_data: dict) -> dict:
        """下载处理完成的 ZIP 并提取 Markdown。

        MinerU v4 完成时返回 full_zip_url，ZIP 内含 markdown 文件。
        """
        zip_url = task_data.get("full_zip_url")
        if not zip_url:
            raise MinerUError("MinerU 返回结果中缺少 full_zip_url")

        import io
        import zipfile

        try:
            with httpx.Client(timeout=httpx.Timeout(self.timeout)) as client:
                resp = client.get(zip_url, headers=self._headers())
                resp.raise_for_status()
                zip_data = io.BytesIO(resp.content)
                with zipfile.ZipFile(zip_data) as zf:
                    md_content = ""
                    for name in zf.namelist():
                        if name.endswith(".md"):
                            md_content = zf.read(name).decode("utf-8")
                            break
                    if not md_content:
                        raise MinerUError("ZIP 中未找到 Markdown 文件")
                    return {"markdown": md_content, **task_data}
        except MinerUError:
            raise
        except Exception as exc:
            raise MinerUError(f"MinerU 结果下载/解压失败: {exc}")

    # =========================================================================
    # 结果解析
    # =========================================================================

    def _parse_result(
        self, result_data: dict, file_path: Path
    ) -> tuple[str, list[tuple[str, bytes]], dict]:
        """解析 MinerU 返回的结果。

        Returns:
            (markdown_text, images, extra_metadata) 元组。
        """
        images: list[tuple[str, bytes]] = []
        metadata: dict = {}

        # --- Markdown 文本 ---
        markdown_text = (
            result_data.get("markdown")
            or result_data.get("result", {}).get("markdown")
            or ""
        )

        # --- 页面数 ---
        page_count = result_data.get("page_count") or result_data.get(
            "total_pages"
        )
        if page_count is not None:
            metadata["page_count"] = int(page_count)

        # --- 图片提取 ---
        raw_images = result_data.get("images", []) or result_data.get(
            "result", {}
        ).get("images", [])
        for img in raw_images:
            if isinstance(img, dict):
                filename = img.get("filename") or img.get("name", f"img_{uuid.uuid4().hex[:8]}.png")
                data = img.get("data") or img.get("bytes") or img.get("content")
                if data:
                    if isinstance(data, str):
                        import base64
                        try:
                            data = base64.b64decode(data)
                        except Exception:
                            data = data.encode("utf-8")
                    if isinstance(data, bytes):
                        images.append((str(filename), data))
            elif isinstance(img, tuple) and len(img) == 2:
                images.append(img)

        # --- content_list.json 解析 ---
        content_list = result_data.get("content_list") or result_data.get(
            "result", {}
        ).get("content_list")
        if content_list and isinstance(content_list, str):
            try:
                parsed = json.loads(content_list)
                if isinstance(parsed, dict):
                    metadata["content_blocks"] = len(parsed.get("contents", []))
            except (json.JSONDecodeError, TypeError):
                pass
        elif content_list and isinstance(content_list, dict):
            metadata["content_blocks"] = len(content_list.get("contents", []))

        # --- 标题 ---
        title = (
            result_data.get("title")
            or result_data.get("result", {}).get("title")
        )
        if title:
            metadata["title"] = title

        return (markdown_text, images, metadata)
