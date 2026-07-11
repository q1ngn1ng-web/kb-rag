"""MCP 查询接口：通过 FastMCP 暴露知识库检索能力。"""

from knowledge_base.mcp_server.server import mcp, run_server

__all__ = ["mcp", "run_server"]
