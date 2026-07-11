"""Streamlit 本地 UI — 文档导入、检索、浏览。

提供四个页面：
1. 文档导入 — 上传单个文件或批量导入目录
2. 知识检索 — 自然语言查询知识图谱
3. 文档列表 — 浏览和管理已索引文档
4. 实体浏览 — 查看和筛选知识图谱实体

用法：
    streamlit run src/knowledge_base/ui/app.py
"""

from __future__ import annotations

import os
import tempfile
import time as _time
from pathlib import Path
from typing import Any

import streamlit as st


# ---------------------------------------------------------------------------
# 懒加载单例
# ---------------------------------------------------------------------------


@st.cache_resource
def _get_engine():
    """获取知识图谱引擎（缓存单例，首次调用时懒加载）。"""
    from knowledge_base.config import load_config
    from knowledge_base.knowledge_graph.lightrag_engine import LightRAGEngine

    config = load_config()
    return LightRAGEngine(config)


# ---------------------------------------------------------------------------
# 文档处理辅助
# ---------------------------------------------------------------------------


def _process_document(file_path: Path, doc_id: str | None = None) -> dict[str, Any]:
    """处理单个文档：转换 → 写 Markdown → 索引到知识图谱。

    Args:
        file_path: 源文件路径。
        doc_id: 文档标识符（默认取 file_path.stem）。

    Returns:
        结果字典，包含 file / status / error / entities 键。
    """
    from knowledge_base.importers.base import get_importer
    from knowledge_base.importers.markdown_writer import write_markdown
    from knowledge_base.config import load_config

    ext = file_path.suffix.lower()
    importer_cls = get_importer(ext)

    if importer_cls is None:
        return {
            "file": str(file_path),
            "status": "skip",
            "error": f"不支持的文件格式: {ext}",
            "entities": 0,
        }

    try:
        importer = importer_cls()
    except Exception as exc:
        return {
            "file": str(file_path),
            "status": "error",
            "error": f"导入器实例化失败: {exc}",
            "entities": 0,
        }

    # 转换
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
            "error": result.error or "转换失败",
            "entities": 0,
        }

    # 写 Markdown 文件（持久化）
    config = load_config()
    output_dir = (
        Path(config.get("knowledge_graph", {}).get("working_dir", "./kb_data"))
        / "imports"
    )
    try:
        write_markdown(result, output_dir, file_path)
    except Exception as exc:
        return {
            "file": str(file_path),
            "status": "error",
            "error": f"写入 Markdown 失败: {exc}",
            "entities": 0,
        }

    # 索引到知识图谱
    engine = _get_engine()
    doc_id = doc_id or file_path.stem
    metadata = result.metadata or {}
    if "source_path" not in metadata:
        metadata["source_path"] = str(file_path)

    try:
        entity_count = engine.index_document(doc_id, result.markdown, metadata)
    except Exception as exc:
        return {
            "file": str(file_path),
            "status": "error",
            "error": f"知识图谱索引失败: {exc}",
            "entities": 0,
        }

    return {
        "file": str(file_path),
        "status": "ok",
        "error": None,
        "entities": entity_count,
    }


def _get_entity_value(entity: dict, *keys: str, default: str = "") -> str:
    """从实体字典中安全读取值，支持多个备选键名。"""
    for key in keys:
        val = entity.get(key)
        if val is not None:
            return str(val)
    return default


def _highlight_entities(text: str, entities: list[dict]) -> str:
    """高亮实体名称（用 **bold** 包裹），用于 Streamlit markdown 渲染。"""
    if not entities or not text:
        return text

    # 提取所有实体名称，按长度降序排列避免部分替换
    names: list[str] = []
    for e in entities:
        name = _get_entity_value(e, "name", "entity_name")
        if name:
            names.append(name)

    names.sort(key=len, reverse=True)

    result = text
    for name in names:
        if name in result:
            result = result.replace(name, f"**{name}**")

    return result


# ---------------------------------------------------------------------------
# 第 5.2 页：文档导入
# ---------------------------------------------------------------------------


def _render_import_page() -> None:
    """文档导入页面：支持单个文件和批量目录导入。"""
    st.header("📥 文档导入")

    tab_file, tab_dir = st.tabs(["单个文件", "批量目录"])

    # ---- 单个文件 ----
    with tab_file:
        uploaded_file = st.file_uploader(
            "选择要导入的文档",
            type=["pdf", "docx", "epub", "txt", "html", "md"],
            key="file_uploader",
        )

        if uploaded_file is not None and st.button(
            "开始导入", key="import_single", type="primary"
        ):
            # 保存上传文件到临时目录
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=Path(uploaded_file.name).suffix
            ) as tmp:
                tmp.write(uploaded_file.getvalue())
                tmp_path = Path(tmp.name)

            try:
                with st.spinner("正在导入并索引到知识图谱…"):
                    result = _process_document(tmp_path, doc_id=Path(uploaded_file.name).stem)

                if result["status"] == "ok":
                    st.success(f"✅ 导入成功！")
                    st.write(f"**文件**: {uploaded_file.name}")
                    if result.get("entities"):
                        st.info(f"提取到 **{result['entities']}** 个实体")
                else:
                    st.error(
                        f"❌ 导入失败: {result.get('error', '未知错误')}"
                    )
            finally:
                # 清理临时文件
                if tmp_path.exists():
                    os.unlink(tmp_path)

    # ---- 批量目录 ----
    with tab_dir:
        dir_path = st.text_input(
            "文档目录路径",
            placeholder="例如: /home/user/documents",
        )

        if dir_path and st.button(
            "批量导入", key="import_dir", type="primary"
        ):
            path = Path(dir_path)

            if not path.exists():
                st.error(f"路径不存在: {dir_path}")
            elif not path.is_dir():
                st.error(f"路径不是目录: {dir_path}")
            else:
                from knowledge_base.importers.base import list_supported_extensions

                # 收集所有支持的文件
                exts = list_supported_extensions()
                all_files: list[Path] = []
                for ext in exts:
                    all_files.extend(path.rglob(f"*{ext}"))
                    all_files.extend(path.rglob(f"*{ext.upper()}"))
                all_files = sorted(set(all_files))

                if not all_files:
                    st.warning("目录中未找到支持的文档文件")
                    return

                st.info(f"找到 {len(all_files)} 个文档，开始批量导入…")

                progress_bar = st.progress(0, text="准备中…")
                status_text = st.empty()
                results: list[dict[str, Any]] = []

                for idx, fp in enumerate(all_files):
                    status_text.info(f"正在处理: {fp.name}")
                    progress_bar.progress(
                        (idx + 1) / len(all_files),
                        text=f"({idx + 1}/{len(all_files)}) {fp.name}",
                    )
                    results.append(_process_document(fp))

                progress_bar.empty()
                status_text.empty()

                # 汇总结果
                success_count = sum(
                    1 for r in results if r["status"] == "ok"
                )
                fail_count = sum(
                    1 for r in results if r["status"] == "error"
                )
                skip_count = sum(
                    1 for r in results if r["status"] == "skip"
                )
                total_entities = sum(
                    r.get("entities", 0) for r in results
                )

                st.subheader("📊 导入结果")
                cols = st.columns(4)
                cols[0].metric("总文件", len(results))
                cols[1].metric("成功", success_count)
                cols[2].metric("失败", fail_count, delta_color="inverse")
                cols[3].metric("提取实体", total_entities)

                if fail_count > 0 or skip_count > 0:
                    with st.expander("失败/跳过详情"):
                        for r in results:
                            if r["status"] in ("error", "skip"):
                                st.write(
                                    f"- **{Path(r['file']).name}**: "
                                    f"{r.get('error', r['status'])}"
                                )


# ---------------------------------------------------------------------------
# 第 5.3 页：知识检索
# ---------------------------------------------------------------------------


def _render_search_page() -> None:
    """知识检索页面：自然语言查询 + 实体高亮 + 来源展示。"""
    st.header("🔍 知识检索")

    engine = _get_engine()

    # 检查是否有已索引的文档
    try:
        docs = engine.list_documents()
    except Exception:
        docs = []

    if not docs:
        st.info("📭 尚未导入任何文档，请先在「文档导入」页面导入文档。")
        return

    st.markdown(f"已索引 **{len(docs)}** 篇文档")

    # 查询输入
    query = st.text_area(
        "输入你的问题",
        placeholder="例如: 文档中提到了哪些关键概念？",
        height=100,
    )

    # 模式选择
    mode = st.selectbox(
        "检索模式",
        options=["mix","hybrid",  "local", "global"],
        format_func=lambda x: {
            "mix": "🔀 混合图+原文块检索（推荐）",
            "hybrid": "🌐 混合图检索",
            "local": "📄 局部检索",
            "global": "🌍 全局检索",
        }[x],
    )

    if st.button("搜索", type="primary", key="search_query"):
        query_text = query.strip()
        if not query_text:
            st.warning("请输入查询问题")
            return

        with st.spinner("正在检索知识图谱…"):
            try:
                _t0 = _time.time()
                response = engine.query(query_text, mode=mode)
                elapsed = _time.time() - _t0
                entities = engine.get_entities()

                # 高亮实体
                highlighted = _highlight_entities(response, entities)

                st.subheader("💡 回答")
                st.caption(f"查询耗时 {elapsed:.1f}s")
                st.markdown(highlighted)

                # 提及的实体
                if entities:
                    # 在回答中查找出现的实体
                    mentioned: list[dict] = []
                    for e in entities:
                        name = _get_entity_value(e, "name", "entity_name")
                        if name and (
                            name.lower() in response.lower()
                        ):
                            mentioned.append(e)

                    if mentioned:
                        with st.expander(
                            f"提及的实体 ({len(mentioned)})"
                        ):
                            for e in mentioned:
                                ename = _get_entity_value(
                                    e, "name", "entity_name"
                                )
                                etype = _get_entity_value(
                                    e, "type", "entity_type", default="概念"
                                )
                                st.write(f"- **{ename}** ({etype})")

                # 来源文档
                with st.expander(f"已索引文档 ({len(docs)})"):
                    for doc in docs:
                        doc_id = doc.get("id", "")
                        doc_path = doc.get("path", "")
                        title = doc_id
                        if doc_path:
                            title = Path(doc_path).stem
                        st.write(f"- {title}")

            except Exception as e:
                st.error(f"查询失败: {e}")


# ---------------------------------------------------------------------------
# 第 5.4 页（第一部分）：文档列表
# ---------------------------------------------------------------------------


def _render_document_list() -> None:
    """文档列表页面：表格展示、查看内容、删除文档。"""
    st.header("📄 文档列表")

    engine = _get_engine()

    try:
        docs = engine.list_documents()
    except Exception:
        docs = []

    if not docs:
        st.info("📭 尚未导入任何文档。")
        return

    # 丰富文档信息
    enriched: list[dict[str, Any]] = []
    for doc in docs:
        doc_id = doc.get("id", "")
        doc_path = doc.get("path", "")

        # 从 Markdown 前页元数据解析
        title = doc_id
        source_format = ""
        import_date = ""

        if doc_path and Path(doc_path).exists():
            try:
                raw = Path(doc_path).read_text(
                    encoding="utf-8", errors="ignore"
                )
            except Exception:
                raw = ""

            if raw.startswith("---"):
                parts = raw.split("---", 2)
                if len(parts) >= 3:
                    for line in parts[1].strip().split("\n"):
                        line = line.strip()
                        if line.startswith("title:"):
                            title = line.split(":", 1)[1].strip().strip('"')
                        elif line.startswith("source_format:"):
                            source_format = (
                                line.split(":", 1)[1].strip().strip('"')
                            )
                        elif line.startswith("import_date:"):
                            import_date = (
                                line.split(":", 1)[1].strip().strip('"')
                            )

        # 获取该文档的实体数
        try:
            doc_entities = engine.get_entities(doc_id)
            entity_count = len(doc_entities)
        except Exception:
            entity_count = 0

        enriched.append(
            {
                "id": doc_id,
                "title": title,
                "format": source_format
                or (Path(doc_path).suffix if doc_path else ""),
                "import_date": import_date[:10] if import_date else "",
                "entity_count": entity_count,
                "path": doc_path,
            }
        )

    # ---- 总览表格 ----
    st.subheader("文档总览")
    table_data = [
        {
            "标题": d["title"],
            "格式": d["format"],
            "导入日期": d["import_date"],
            "实体数": d["entity_count"],
        }
        for d in enriched
    ]
    st.dataframe(
        table_data,
        use_container_width=True,
        hide_index=True,
        column_config={
            "标题": st.column_config.TextColumn(width="large"),
            "格式": st.column_config.TextColumn(width="small"),
            "导入日期": st.column_config.TextColumn(width="small"),
            "实体数": st.column_config.NumberColumn(width="small"),
        },
    )

    # ---- 详情与操作 ----
    st.subheader("文档详情")
    doc_names = [d["title"] for d in enriched]
    selected_title = st.selectbox("选择文档查看详情", doc_names)

    if selected_title:
        selected = next(
            d for d in enriched if d["title"] == selected_title
        )
        doc_id = selected["id"]
        doc_path = selected["path"]

        col1, col2 = st.columns([3, 1])
        with col1:
            st.write(f"**文档 ID**: `{doc_id}`")
            st.write(f"**格式**: {selected['format']}")
            st.write(f"**导入日期**: {selected['import_date'] or '未知'}")
            st.write(f"**实体数**: {selected['entity_count']}")
        with col2:
            if st.button(
                "🗑️ 删除文档",
                key=f"delete_{doc_id}",
                type="secondary",
                use_container_width=True,
            ):
                try:
                    engine.delete_document(doc_id)
                    st.success(f"文档「{selected_title}」已删除")
                    st.rerun()
                except Exception as e:
                    st.error(f"删除失败: {e}")

        # 该文档的实体
        if selected["entity_count"] > 0:
            try:
                doc_entities = engine.get_entities(doc_id)
            except Exception:
                doc_entities = []

            if doc_entities:
                with st.expander(
                    f"文档中的实体 ({len(doc_entities)})"
                ):
                    for e in doc_entities:
                        ename = _get_entity_value(
                            e, "name", "entity_name"
                        )
                        etype = _get_entity_value(
                            e, "type", "entity_type", default="概念"
                        )
                        st.write(f"- **{ename}** ({etype})")

        # 原文预览
        if doc_path and Path(doc_path).exists():
            with st.expander("查看原文"):
                try:
                    content = Path(doc_path).read_text(
                        encoding="utf-8", errors="ignore"
                    )
                except Exception:
                    content = ""

                if content.startswith("---"):
                    parts = content.split("---", 2)
                    if len(parts) >= 3:
                        content = parts[2]

                preview = content[:2000]
                if len(content) > 2000:
                    preview += "\n\n*…（内容已截断）*"

                st.markdown(preview)


# ---------------------------------------------------------------------------
# 第 5.4 页（第二部分）：实体浏览
# ---------------------------------------------------------------------------


def _render_entity_browser() -> None:
    """实体浏览页面：按类型筛选、查看详情、关联实体。"""
    st.header("🏷️ 实体浏览")

    engine = _get_engine()

    try:
        entities = engine.get_entities()
    except Exception:
        entities = []

    if not entities:
        st.info("📭 尚未提取任何实体，请先在「文档导入」页面导入文档。")
        return

    # 统计类型分布
    type_counts: dict[str, int] = {}
    for e in entities:
        etype = _get_entity_value(e, "type", "entity_type", default="概念")
        type_counts[etype] = type_counts.get(etype, 0) + 1

    # 展示统计
    st.markdown(f"共 **{len(entities)}** 个实体")
    type_cols = st.columns(min(len(type_counts), 6))
    for i, (t, cnt) in enumerate(sorted(type_counts.items())):
        type_cols[i % len(type_cols)].metric(t, cnt)

    # ---- 筛选与选择 ----
    all_types = ["全部"] + sorted(type_counts.keys())
    selected_type = st.selectbox("按类型筛选", all_types)

    filtered = entities
    if selected_type != "全部":
        filtered = [
            e
            for e in entities
            if _get_entity_value(e, "type", "entity_type", default="概念")
            == selected_type
        ]

    st.markdown(f"**{selected_type}**: {len(filtered)} 个实体")

    if not filtered:
        return

    # 排序实体名称
    entity_names = sorted(
        _get_entity_value(e, "name", "entity_name", default="未知")
        for e in filtered
    )
    selected_name = st.selectbox("选择实体查看详情", entity_names)

    if selected_name:
        entity = next(
            e
            for e in filtered
            if _get_entity_value(e, "name", "entity_name") == selected_name
        )

        name = _get_entity_value(entity, "name", "entity_name")
        etype = _get_entity_value(
            entity, "type", "entity_type", default="概念"
        )

        # 别名（可能是列表或字符串）
        aliases_raw = entity.get("aliases", entity.get("alias", []))
        if isinstance(aliases_raw, str):
            aliases = [aliases_raw]
        elif isinstance(aliases_raw, list):
            aliases = [str(a) for a in aliases_raw]
        else:
            aliases = []

        # 描述
        description = _get_entity_value(
            entity, "description", "desc", "备注"
        )

        col1, col2 = st.columns(2)
        with col1:
            st.write(f"**名称**: {name}")
            st.write(f"**类型**: {etype}")
        with col2:
            if aliases:
                st.write(f"**别名**: {'，'.join(aliases)}")
            if description:
                st.write(f"**描述**: {description}")

        # 来源文档
        source_doc = _get_entity_value(
            entity, "source_id", "doc_id", "source_documents"
        )
        if source_doc:
            st.write(f"**来源文档**: {source_doc}")

        # 相关实体（同类型的其他实体）
        related = [
            e
            for e in entities
            if _get_entity_value(e, "type", "entity_type", default="概念")
            == etype
            and _get_entity_value(e, "name", "entity_name") != name
        ]
        if related:
            with st.expander(f"相关实体（同类型）({len(related)})"):
                for r in related[:30]:
                    rname = _get_entity_value(r, "name", "entity_name")
                    raliases_raw = r.get("aliases", r.get("alias", []))
                    raliases = (
                        f" ({', '.join(raliases_raw)})"
                        if isinstance(raliases_raw, list) and raliases_raw
                        else ""
                    )
                    st.write(f"- **{rname}**{raliases}")

    # ---- 全部实体列表 ----
    with st.expander("全部实体列表"):
        # 分页展示
        page_size = 50
        total_pages = max(1, (len(filtered) + page_size - 1) // page_size)
        page = st.number_input(
            "页码",
            min_value=1,
            max_value=total_pages,
            value=1,
            key="entity_page",
        )
        start = (page - 1) * page_size
        end = min(start + page_size, len(filtered))

        for e in filtered[start:end]:
            ename = _get_entity_value(e, "name", "entity_name")
            etype = _get_entity_value(
                e, "type", "entity_type", default="概念"
            )
            st.write(f"- **{ename}** ({etype})")

        if total_pages > 1:
            st.caption(f"第 {page}/{total_pages} 页（共 {len(filtered)} 个实体）")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def main() -> None:
    """Streamlit 应用入口。"""
    st.set_page_config(
        page_title="个人知识库",
        page_icon="📚",
        layout="wide",
    )

    st.sidebar.title("个人知识库")

    page = st.sidebar.radio(
        "导航",
        ["文档导入", "知识检索", "文档列表", "实体浏览"],
    )

    if page == "文档导入":
        _render_import_page()
    elif page == "知识检索":
        _render_search_page()
    elif page == "文档列表":
        _render_document_list()
    elif page == "实体浏览":
        _render_entity_browser()


if __name__ == "__main__":
    main()
