"""kb CLI 入口点。

用法:
    kb import [--index] [--vault] <path>       导入文档
    kb index [--vault] <path>                  索引 .md 到知识图谱
    kb serve                                    启动 MCP Server
    kb ui                                       启动 Streamlit
    kb query <question>                         命令行查询
"""

import sys
import os
from pathlib import Path


def main() -> None:
    args = sys.argv[1:]
    if not args:
        _print_help()
        sys.exit(1)

    command = args[0]
    rest = args[1:]

    if command == "import":
        _cmd_import(rest)
    elif command == "index":
        _cmd_index(rest)
    elif command == "serve":
        _cmd_serve()
    elif command == "ui":
        _cmd_ui()
    elif command == "query":
        _cmd_query(rest)
    else:
        print(f"未知命令: {command}")
        _print_help()
        sys.exit(1)


def _print_help() -> None:
    print("用法: kb <command> [options]")
    print("命令:")
    print("  import [--index] [--vault] <path>   导入文档（仅转换）")
    print("  index [--vault] <path>              索引 .md 到知识图谱")
    print("  serve                                启动 MCP Server")
    print("  ui                                   启动 Streamlit")
    print("  query [--mode] <question>             命令行查询")
    print()
    print("选项:")
    print("  --index    转换后自动索引到知识图谱")
    print("  --vault    转换后同步到 Obsidian Vault")
    print("  --engine   指定 PDF 引擎 (pymupdf|mineru)")
    print("  --mode     查询模式 (hybrid|mix|local|global，默认 hybrid)")
    print("  --force    索引前强制清理旧数据（用于重索引）")


# =============================================================================
# 参数解析
# =============================================================================

def _parse_flags(args: list[str]) -> tuple[list[str], dict]:
    """解析 flags (--xxx) 和位置参数。"""
    flags: dict = {"index": False, "vault": False, "force": False, "engine": None}
    paths: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--index":
            flags["index"] = True
            i += 1
        elif args[i] == "--vault":
            flags["vault"] = True
            i += 1
        elif args[i] == "--force":
            flags["force"] = True
            i += 1
        elif args[i] == "--engine" and i + 1 < len(args):
            flags["engine"] = args[i + 1]
            i += 2
        else:
            paths.append(args[i])
            i += 1
    return paths, flags


# =============================================================================
# 辅助
# =============================================================================

def _get_config():
    from knowledge_base.config import load_config
    return load_config()


def _get_logger():
    from knowledge_base.logger import setup_logger, get_logger
    setup_logger("knowledge_base")
    return get_logger("knowledge_base")


def _get_kg_engine(config):
    from knowledge_base.knowledge_graph.lightrag_engine import LightRAGEngine
    return LightRAGEngine(config)


def _sync_to_obsidian(markdown_body: str, metadata: dict, entities: list[dict] | None, config: dict) -> None:
    """如果配置了 vault_path，同步到 Obsidian。"""
    vault_path = config.get("obsidian", {}).get("vault_path", "")
    if not vault_path:
        return
    from knowledge_base.obsidian import sync_to_vault
    sync_to_vault(markdown_body, metadata, entities=entities, vault_path=vault_path)


# =============================================================================
# kb import
# =============================================================================

def _cmd_import(args: list[str]) -> None:
    """转换文档为 Markdown（可选 --index 自动索引，--vault 同步 Obsidian）。"""
    paths, flags = _parse_flags(args)
    if not paths:
        print("用法: kb import [--index] [--vault] <path>")
        sys.exit(1)

    logger = _get_logger()
    config = _get_config()

    from knowledge_base.importers.batch import BatchImporter

    output_dir = Path(config.get("knowledge_graph", {}).get("working_dir", "./kb_data"))
    output_dir.mkdir(parents=True, exist_ok=True)

    importer = BatchImporter(output_dir=output_dir)

    kg_engine = None
    if flags["index"]:
        kg_engine = _get_kg_engine(config)

    for path_str in paths:
        p = Path(path_str)
        if not p.exists():
            logger.error(f"路径不存在: {p}")
            continue

        logger.info(f"导入: {p}")
        results = importer.import_path(p, engine=flags["engine"])

        success = sum(1 for r in results if r["status"] == "ok")
        failed = sum(1 for r in results if r["status"] == "error")
        logger.info(f"转换完成: {success} 成功, {failed} 失败, 输出目录: {output_dir}")

        # --index: 索引到知识图谱
        if kg_engine and success > 0:
            for r in results:
                if r["status"] == "ok":
                    file_path = Path(r["file"])
                    md_path = output_dir / f"{file_path.stem}.md"
                    if md_path.exists():
                        logger.info(f"索引: {file_path.name}")
                        content = md_path.read_text(encoding="utf-8")
                        from knowledge_base.utils import strip_frontmatter
                        _, body = strip_frontmatter(content)
                        kg_engine.index_document(file_path.stem, body)
            logger.info("知识图谱索引完成")

        # --vault: 同步到 Obsidian
        if flags["vault"] and success > 0:
            for r in results:
                if r["status"] == "ok":
                    file_path = Path(r["file"])
                    md_path = output_dir / f"{file_path.stem}.md"
                    if md_path.exists():
                        content = md_path.read_text(encoding="utf-8")
                        metadata = {"title": file_path.stem, "source_path": str(file_path)}
                        _sync_to_obsidian(content, metadata, entities=None, config=config)


# =============================================================================
# kb index
# =============================================================================

def _cmd_index(args: list[str]) -> None:
    """接收 .md 文件/目录，索引到知识图谱。"""
    paths, flags = _parse_flags(args)
    if not paths:
        print("用法: kb index [--vault] [--force] <path/to.md or /dir>")
        sys.exit(1)

    logger = _get_logger()
    config = _get_config()

    kg_engine = _get_kg_engine(config)
    from knowledge_base.utils import strip_frontmatter

    # 收集 .md 文件
    md_files: list[Path] = []
    for path_str in paths:
        p = Path(path_str)
        if not p.exists():
            logger.warning(f"路径不存在: {p}")
            continue
        if p.is_file() and p.suffix.lower() == ".md":
            md_files.append(p)
        elif p.is_dir():
            md_files.extend(sorted(p.rglob("*.md")))

    if not md_files:
        logger.warning("未找到 .md 文件")
        return

    logger.info(f"索引 {len(md_files)} 个 .md 文件到知识图谱")

    for md_path in md_files:
        try:
            content = md_path.read_text(encoding="utf-8")
            fm, body = strip_frontmatter(content)
            doc_id = fm.get("title", md_path.stem) if fm else md_path.stem

            # --force: 先清理旧数据再索引
            if flags["force"]:
                try:
                    kg_engine.delete_document(doc_id)
                    logger.info(f"  已清理旧数据: {doc_id}")
                except Exception:
                    logger.info(f"  无旧数据需清理: {doc_id}")

            kg_engine.index_document(doc_id, body)
            logger.info(f"  已索引: {doc_id} ({md_path.name})")

            # --vault: 同步实体笔记到 Obsidian
            if flags["vault"]:
                vault_path = config.get("obsidian", {}).get("vault_path", "")
                if vault_path:
                    # 读取实体的描述信息（索引后可从 KG 查询）
                    entities = kg_engine.get_entities(doc_id=doc_id)
                    from knowledge_base.obsidian import sync_to_vault
                    sync_to_vault(body, {"title": doc_id, "source_path": str(md_path)},
                                  entities=entities if entities else None,
                                  vault_path=vault_path)

        except Exception as e:
            logger.error(f"  索引失败: {md_path.name}: {e}")

    logger.info("索引完成")


# =============================================================================
# kb serve / ui / query
# =============================================================================

def _cmd_serve() -> None:
    from knowledge_base.mcp_server import run_server
    run_server()


def _cmd_ui() -> None:
    import subprocess
    ui_path = Path(__file__).parent / "ui" / "app.py"
    if not ui_path.exists():
        print(f"错误: UI 文件不存在: {ui_path}")
        sys.exit(1)
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(ui_path)], check=True)


def _cmd_query(args: list[str]) -> None:
    # 解析 --mode
    mode = "hybrid"
    filtered = []
    i = 0
    while i < len(args):
        if args[i] == "--mode" and i + 1 < len(args):
            mode = args[i + 1]
            i += 2
        else:
            filtered.append(args[i])
            i += 1

    question = " ".join(filtered) if filtered else ""
    if not question:
        print("请输入查询内容: kb query [--mode <mode>] <question>")
        sys.exit(1)

    _get_logger()
    config = _get_config()
    kg_engine = _get_kg_engine(config)
    try:
        result = kg_engine.query(question, mode=mode)
        print(result)
    except Exception as e:
        msg = str(e) if str(e) else type(e).__name__
        print(f"查询失败: {msg}")
        sys.exit(1)
