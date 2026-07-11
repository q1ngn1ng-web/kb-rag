"""FastMCP 服务器，暴露知识库检索能力。"""

from pathlib import Path

from fastmcp import FastMCP

from knowledge_base.config import load_config
from knowledge_base.logger import get_logger

# 创建 MCP 服务器
mcp = FastMCP("knowledge-base", instructions="个人知识库 — 检索导入的文档和知识图谱")


def _get_engine() -> "KnowledgeGraphEngine":
    """知识图谱引擎的懒初始化。"""
    from knowledge_base.knowledge_graph.lightrag_engine import LightRAGEngine

    config = load_config()
    return LightRAGEngine(config)


@mcp.tool(
    name="query",
    description="检索知识库。传入自然语言问题，返回相关文档段落、实体信息和关系描述。支持 hybrid（混合图检索，默认）、mix（混合图+原文块检索，推荐）、local（实体级）、global（关系级）四种模式。适合回忆内容、查找概念出处、关联分析等场景。",
)

def query(question: str, mode: str = "hybrid") -> str:
    """检索知识库。

    Args:
        question: 自然语言查询问题。
        mode: 查询模式 — "hybrid"（混合图检索，默认）、"mix"（混合图+原文块检索，推荐）、"local"（实体级）、"global"（关系级）。

    Returns:
        包含相关段落、实体上下文和来源的检索结果。
    """
    try:
        engine = _get_engine()
        return engine.query(question, mode=mode)
    except Exception as e:
        logger = get_logger("mcp_server")
        logger.error(f"查询失败: {e}")
        return f"查询失败: {e}"


@mcp.tool(
    name="list_documents",
    description="列出知识库中所有已索引的文档，包含标题、格式、导入日期和实体数量。",
)
def list_documents() -> str:
    """列出已索引的文档列表。"""
    try:
        engine = _get_engine()
        docs = engine.list_documents()
        if not docs:
            return "知识库中尚无索引的文档。"

        lines = ["## 已索引文档\n"]
        for doc in docs:
            lines.append(f"- {doc.get('id', 'unknown')}: {doc.get('path', '')}")
        return "\n".join(lines)
    except Exception as e:
        logger = get_logger("mcp_server")
        logger.error(f"获取文档列表失败: {e}")
        return f"获取文档列表失败: {e}"


@mcp.tool(
    name="get_entities",
    description="获取知识图谱中的实体列表。可按文档筛选（传入 doc_id 参数）或查询全局实体。返回实体名称、类型、关联文档。",
)
def get_entities(doc_id: str = "") -> str:
    """获取实体列表。

    Args:
        doc_id: 可选的文档 ID，传入后只返回该文档中的实体。
    """
    try:
        engine = _get_engine()
        entities = engine.get_entities(doc_id=doc_id if doc_id else None)
        if not entities:
            return "未找到实体。"

        lines = ["## 实体列表\n"]
        for ent in entities:
            name = ent.get("name", ent.get("id", "unknown"))
            etype = ent.get("type", ent.get("entity_type", "未知"))
            source = ent.get("doc_id", ent.get("source", ""))
            lines.append(f"- **{name}** (类型: {etype})")
            if source:
                lines[-1] += f" — 来源: {source}"
        return "\n".join(lines)
    except Exception as e:
        logger = get_logger("mcp_server")
        logger.error(f"获取实体列表失败: {e}")
        return f"获取实体列表失败: {e}"


@mcp.tool(
    name="get_document",
    description="获取指定文档的原始 Markdown 内容。传入文档 ID（可从 list_documents 获取）。",
)
def get_document(doc_id: str) -> str:
    """获取文档原始内容。

    Args:
        doc_id: 文档 ID（文件名或标识符）。
    """
    try:
        config = load_config()
        output_dir = config.get("knowledge_graph", {}).get("working_dir", "./kb_data")

        # 搜索文档文件
        doc_path = Path(output_dir) / f"{doc_id}.md"
        if not doc_path.exists():
            # 尝试更广泛的搜索
            matches = list(Path(output_dir).rglob(f"{doc_id}*"))
            if not matches:
                return f"未找到文档: {doc_id}"
            doc_path = matches[0]

        if not doc_path.exists():
            return f"文档文件不存在: {doc_id}"

        content = doc_path.read_text(encoding="utf-8")
        return content
    except Exception as e:
        logger = get_logger("mcp_server")
        logger.error(f"获取文档内容失败: {e}")
        return f"获取文档内容失败: {e}"


def run_server(host: str = "0.0.0.0", port: int = 8000) -> None:
    """启动 MCP 服务器。"""
    logger = get_logger("mcp_server")
    logger.info(f"正在启动 MCP 服务器，地址: {host}:{port}")
    mcp.run(transport="sse", host=host, port=port)


def main() -> None:
    """命令行入口。"""
    run_server()


if __name__ == "__main__":
    main()
