"""FastMCP 服务器，暴露知识库检索能力（重构版 - 原生 async tools）。

关键改动：
1. 所有 tool 改为 async def，直接 await engine.aquery / aindex 等。
2. 引擎通过 lru_cache 单例获取，确保 worker thread 只启动一次。
3. 默认绑定 127.0.0.1，强制要求 API Key（安全）。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from fastmcp import FastMCP

from knowledge_base.config import load_config
from knowledge_base.logger import get_logger
from knowledge_base.knowledge_graph.lightrag_engine import LightRAGEngine

logger = get_logger("mcp_server")


@lru_cache(maxsize=1)
def _get_engine() -> LightRAGEngine:
    """知识图谱引擎单例（进程内只初始化一次）。"""
    config = load_config()
    return LightRAGEngine(config)


mcp = FastMCP(
    "knowledge-base",
    instructions="个人知识库 — 检索导入的文档和知识图谱",
)


def _check_auth(api_key: str | None) -> None:
    """简单 Bearer 校验。config 里可配置 knowledge_graph.mcp_api_key。"""
    expected = (
        load_config()
        .get("knowledge_graph", {})
        .get("mcp_api_key")
        or os.environ.get("KB_MCP_API_KEY")
    )
    if not expected:
        # 未配置 key 时仅警告（开发友好），生产必须配
        logger.warning("MCP API Key 未配置，跳过认证（不安全）")
        return
    if not api_key or api_key != expected:
        raise PermissionError("无效的 API Key")


@mcp.tool(
    name="query",
    description=(
        "检索知识库。传入自然语言问题，返回相关文档段落、实体信息和关系描述。"
        "支持 hybrid（混合图检索，默认）、mix（混合图+原文块检索，推荐）、"
        "local（实体级）、global（关系级）四种模式。"
    ),
)
async def query(question: str, mode: str = "hybrid", api_key: str = "") -> str:
    """异步查询知识库。"""
    try:
        _check_auth(api_key)
        engine = _get_engine()
        return await engine.aquery(question, mode=mode)
    except Exception as e:
        logger.error(f"查询失败: {e}")
        return f"查询失败: {e}"


@mcp.tool(
    name="list_documents",
    description="列出知识库中所有已索引的文档，包含标题、格式、导入日期和实体数量。",
)
async def list_documents(api_key: str = "") -> str:
    """列出已索引文档。"""
    try:
        _check_auth(api_key)
        engine = _get_engine()
        docs = engine.list_documents()
        if not docs:
            return "知识库中尚无索引的文档。"
        lines = ["## 已索引文档\n"]
        for doc in docs:
            lines.append(f"- {doc.get('id', 'unknown')}: {doc.get('path', '')}")
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"获取文档列表失败: {e}")
        return f"获取文档列表失败: {e}"


@mcp.tool(
    name="get_entities",
    description=(
        "获取知识图谱中的实体列表。可按文档筛选（传入 doc_id 参数）或查询全局实体。"
        "返回实体名称、类型、关联文档。"
    ),
)
async def get_entities(doc_id: str = "", api_key: str = "") -> str:
    """获取实体列表。"""
    try:
        _check_auth(api_key)
        engine = _get_engine()
        entities = engine.get_entities(doc_id=doc_id if doc_id else None)
        if not entities:
            return "未找到实体。"
        lines = ["## 实体列表\n"]
        for ent in entities:
            name = ent.get("name", ent.get("id", "unknown"))
            etype = ent.get("type", ent.get("entity_type", "未知"))
            source = ent.get("doc_id", ent.get("source", ""))
            line = f"- **{name}** (类型: {etype})"
            if source:
                line += f" — 来源: {source}"
            lines.append(line)
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"获取实体列表失败: {e}")
        return f"获取实体列表失败: {e}"


@mcp.tool(
    name="get_document",
    description="获取指定文档的原始 Markdown 内容。传入文档 ID（可从 list_documents 获取）。",
)
async def get_document(doc_id: str, api_key: str = "") -> str:
    """获取文档原始内容。"""
    try:
        _check_auth(api_key)
        config = load_config()
        output_dir = config.get("knowledge_graph", {}).get("working_dir", "./kb_data")

        doc_path = Path(output_dir) / f"{doc_id}.md"
        if not doc_path.exists():
            matches = list(Path(output_dir).rglob(f"{doc_id}*"))
            if not matches:
                return f"未找到文档: {doc_id}"
            doc_path = matches[0]

        if not doc_path.exists():
            return f"文档文件不存在: {doc_id}"

        return doc_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"获取文档内容失败: {e}")
        return f"获取文档内容失败: {e}"


def run_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    """启动 MCP 服务器。

    默认绑定 127.0.0.1（安全）。远程访问请显式传 host="0.0.0.0"
    并配置 knowledge_graph.mcp_api_key 或环境变量 KB_MCP_API_KEY。
    """
    logger.info(f"正在启动 MCP 服务器，地址: {host}:{port}")
    if host == "0.0.0.0":
        logger.warning(
            "警告：绑定 0.0.0.0 暴露所有网卡，请确保已配置 mcp_api_key！"
        )
    mcp.run(transport="sse", host=host, port=port)


def main() -> None:
    run_server()


if __name__ == "__main__":
    main()