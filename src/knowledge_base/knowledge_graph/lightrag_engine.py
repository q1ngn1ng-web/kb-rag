"""基于 LightRAG 的知识图谱引擎实现。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from knowledge_base.exceptions import KnowledgeGraphError
from knowledge_base.logger import get_logger

import jieba

from .bm25_index import ChineseBM25Index
from .engine import KnowledgeGraphEngine
from .query_cache import QueryCache


class LightRAGEngine(KnowledgeGraphEngine):
    """基于 LightRAG 的知识图谱引擎实现。

    通过 LightRAG（lightrag-hku）完成实体/关系提取、向量索引和检索。
    懒初始化：仅在首次操作时加载 LightRAG，避免模块级别导入错误。

    Args:
        config: 全局配置字典（需包含 llm, embedding, knowledge_graph 等键）。
    """

    def __init__(self, config: dict) -> None:
        self.config = config
        kg_config = config.get("knowledge_graph", {})
        self.working_dir = Path(kg_config.get("working_dir", "./kb_data"))
        self.working_dir.mkdir(parents=True, exist_ok=True)

        # 自定义实体词典（Task 3.4）：{实体名: {"type": str, "aliases": list[str]}}
        self._entity_dict: dict[str, dict] = {}

        # LightRAG 实例（懒初始化）
        self._rag: Any = None
        self._initialized = False

        # 查询语义缓存
        cache_cfg = kg_config.get("query_cache", {})
        if cache_cfg.get("enabled", True):
            threshold = cache_cfg.get("threshold", 0.92)
            max_entries = cache_cfg.get("max_entries", 1000)
        else:
            threshold = 1.1  # 永不命中
            max_entries = 0
        self._query_cache = QueryCache(
            self.working_dir / "query_cache.json",
            threshold=threshold,
            max_entries=max_entries,
        )

        # Embedding 客户端（用于缓存查询的预嵌入）
        self._embed_cache_client: Any = None
        self._embed_cache_model: str = "text-embedding-v4"

        # BM25 多路召回
        bm25_cfg = kg_config.get("bm25", {})
        self._bm25 = ChineseBM25Index(self.working_dir) if bm25_cfg.get("enabled", True) else None
        self._bm25_top_k = bm25_cfg.get("top_k", 20)
        self._bm25_rrf_k = bm25_cfg.get("rrf_k", 60)
        # 预加载 jieba 词典，避免首次查询时卡顿
        if self._bm25:
            jieba.cut("")

        # LLM 函数（懒初始化后赋值，用于两阶段查询）
        self._llm_func: Any = None

    # ------------------------------------------------------------------
    # 懒初始化
    # ------------------------------------------------------------------

    def _ensure_initialized(self) -> None:
        """确保 LightRAG 已初始化（懒加载）。

        Streamlit 每次 rerun 创建新的事件循环，而 LightRAG 使用模块级
        asyncio.Lock，跨事件循环会报 "bound to a different event loop"。

        这里的策略是：每次初始化前先重置共享存储状态，让 LightRAG 在
        当前事件循环中重新创建所有锁；然后替换绑定到 asyncio.run() 临时
        循环的锁，确保同一次 rerun 中后续操作使用持久事件循环。
        """
        if self._initialized:
            return

        # ---- 步骤 1：重置 LightRAG 共享存储模块状态 ----
        # 确保 initialize_share_data() 在下个 LightRAG 实例中重新创建锁
        from lightrag.kg.shared_storage import finalize_share_data

        finalize_share_data()

        try:
            from lightrag import LightRAG
            import openai
        except ImportError as e:
            raise KnowledgeGraphError(
                f"LightRAG 未安装，请执行: pip install lightrag-hku (错误: {e})"
            ) from e

        try:
            llm_config = self.config.get("llm", {})
            embed_config = self.config.get("embedding", {})
            kg_config = self.config.get("knowledge_graph", {})

            api_key = llm_config.get("api_key", "")

            # ---- 建立持久事件循环 ----
            # 必须在任何 asyncio 操作之前创建并安装，确保 LightRAG 初始化
            # 和后续 _run_sync() 使用同一个循环，避免 Lock / PriorityQueue
            # 跨事件循环失效。
            import asyncio

            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._loop = loop

            # ---- LLM 函数 ----
            llm_client = openai.AsyncOpenAI(
                api_key=api_key,
                base_url=llm_config.get("base_url", "https://api.deepseek.com"),
            )

            async def llm_model_func(
                prompt: str,
                system_prompt: str | None = None,
                **kwargs: Any,
            ) -> str:
                messages: list[dict[str, str]] = []
                if system_prompt:
                    messages.append({"role": "system", "content": system_prompt})
                messages.append({"role": "user", "content": prompt})

                # LightRAG 有时会传 response_format，但不是所有 provider 都支持。
                # 这里跳过 response_format，因为 prompt 本身已引导 JSON 输出。
                api_kwargs: dict[str, Any] = {
                    "model": llm_config.get("model", "deepseek-v4-flash"),
                    "messages": messages,
                    "max_tokens": llm_config.get("max_tokens", 4096),
                    "temperature": llm_config.get("temperature", 0.7),
                }

                response = await llm_client.chat.completions.create(**api_kwargs)
                result = response.choices[0].message.content or ""
                return result

            self._llm_func = llm_model_func

            # ---- Embedding 函数 ----
            # 使用 embedding 独立配置
            embed_api_key = embed_config.get("api_key")
            embed_base_url = embed_config.get("base_url")
            embed_model = embed_config.get("model", "text-embedding-v4")

            embed_client = openai.AsyncOpenAI(
                api_key=embed_api_key,
                base_url=embed_base_url,
            )
            self._embed_cache_client = embed_client
            self._embed_cache_model = embed_model

            from lightrag.utils import wrap_embedding_func_with_attrs
            import numpy as np

            @wrap_embedding_func_with_attrs(
                embedding_dim=1024,
                max_token_size=8192,
                model_name=embed_model,
            )
            async def embedding_func(
                texts: list[str],
            ) -> np.ndarray:
                response = await embed_client.embeddings.create(
                    model=embed_model,
                    input=texts,
                )
                return np.array([item.embedding for item in response.data])

            # ---- LightRAG 初始化 ----
            # 根据 provider 决定并发度
            provider = llm_config.get("provider", "")
            if provider == "xiaomi":
                # 小米 Token Plan 有速率限制，降低并发
                role_llm_configs = {
                    "extract": {"max_async": 4},
                    "keyword": {"max_async": 4},
                    "query": {"max_async": 4},
                }
            else:
                role_llm_configs = None

            self._rag = LightRAG(
                working_dir=str(self.working_dir),
                llm_model_func=llm_model_func,
                embedding_func=embedding_func,
                chunk_token_size=kg_config.get("chunk_size", 1200),
                chunk_overlap_token_size=kg_config.get("chunk_overlap", 200),
                role_llm_configs=role_llm_configs,
            )

            # ---- 步骤 2：初始化存储（在同一持久循环上） ----
            self._loop.run_until_complete(self._rag.initialize_storages())

            self._initialized = True

        except Exception as e:
            raise KnowledgeGraphError(f"LightRAG 初始化失败: {e}") from e

    # ------------------------------------------------------------------
    # 核心接口实现
    # ------------------------------------------------------------------

    def index_document(self, doc_id: str, content: str, metadata: dict | None = None) -> int:
        """索引一篇文档到知识图谱中。

        Args:
            doc_id: 文档唯一标识符。
            content: 文档内容（Markdown 文本）。
            metadata: 可选的附加元数据（暂时未持久化，留作扩展）。

        Returns:
            提取的实体数量（不可靠时返回 1 表示成功）。

        分块策略由 config.yaml 的 knowledge_graph.chunk_strategy 控制：
        - "F": 固定 token 窗口（默认），沿袭 LightRAG insert() 行为
        - "R": 递归字符分割，优先在中文句子/段落边界切割
        """
        self._ensure_initialized()

        # 注入实体词典（Task 3.4）
        if self._entity_dict:
            enhanced_content = self._build_entity_prompt() + "\n\n" + content
        else:
            enhanced_content = content

        try:
            kg_config = self.config.get("knowledge_graph", {})
            chunk_strategy = kg_config.get("chunk_strategy", "F")
            logger = get_logger("knowledge_graph")

            if chunk_strategy == "R":
                async def _do_index():
                    await self._rag.apipeline_enqueue_documents(
                        enhanced_content, ids=[doc_id], process_options="R",
                    )
                    await self._rag.apipeline_process_enqueue_documents()
                asyncio.run(_do_index())
                logger.info(f"文档索引已提交 [{doc_id}]，分块策略: R")
            else:
                _track_id = self._rag.insert(enhanced_content, ids=[doc_id])
                logger.info(f"文档索引已提交 [{doc_id}], track_id={_track_id}，分块策略: F")

            # 重建 BM25 索引
            if self._bm25:
                count = self._bm25.rebuild()
                logger.info(f"BM25 索引已重建 ({count} 个块)")

            return 1
        except Exception as e:
            raise KnowledgeGraphError(str(e)) from e

    def query(self, question: str, mode: str = "hybrid") -> str:
        """查询知识图谱。

        Args:
            question: 自然语言查询。
            mode: 检索模式 — "local"（实体级）/ "global"（关系级）/ "hybrid"（混合，默认）/ "mix"（混合 + 原文块向量检索）。

        Returns:
            包含上下文的回答文本。
        """
        self._ensure_initialized()
        logger = get_logger("knowledge_graph")
        import time as _time

        _t0 = _time.time()

        # —— 语义缓存：预嵌入问题，查缓存 ——
        embedding: list[float] | None = None
        if self._embed_cache_client:
            embedding = self._get_question_embedding(question)
            if embedding:
                cached = self._query_cache.lookup(embedding, mode)
                if cached is not None:
                    elapsed = _time.time() - _t0
                    logger.info(f"缓存命中 ({elapsed:.1f}s) | mode={mode} | query=\"{question[:60]}\"")
                    return cached

        try:
            from lightrag import QueryParam

            kg_config = self.config.get("knowledge_graph", {})
            bm25_cfg = kg_config.get("bm25", {})
            query_timeout = kg_config.get("query_timeout", 60)

            # —— 阶段 1: LightRAG 只出 prompt，不调 LLM ——
            if _time.time() - _t0 >= query_timeout:
                raise TimeoutError(
                    f"查询超时（{query_timeout} 秒），可调大 config.yaml 中 knowledge_graph.query_timeout"
                )

            prompt_param = QueryParam(
                mode=mode,
                top_k=kg_config.get("top_k", 60),
                chunk_top_k=kg_config.get("chunk_top_k", 30),
                max_entity_tokens=kg_config.get("max_entity_tokens", 12000),
                max_relation_tokens=kg_config.get("max_relation_tokens", 16000),
                max_total_tokens=kg_config.get("max_total_tokens", 60000),
                only_need_prompt=True,
                enable_rerank=False,
            )
            result = self._rag.query(question, param=prompt_param)
            prompt_str = str(result)

            # —— 阶段 2: BM25 RRF 重排序（不引入新块） ——
            if self._bm25 and bm25_cfg.get("enabled", True):
                prompt_str = self._rerank_chunks_with_bm25(prompt_str, question)

            # —— 阶段 3: 自行调用 LLM 生成回答 ——
            elapsed = _time.time() - _t0
            llm_remaining = max(5, min(
                self.config.get("llm", {}).get("timeout", 60),
                query_timeout - elapsed,
            ))
            logger.info("LLM 生成回答中（最长等待 %s 秒）…", llm_remaining)

            async def _call_llm() -> str:
                return await self._llm_func(prompt=prompt_str, system_prompt=None)

            try:
                result_str = asyncio.run(
                    asyncio.wait_for(_call_llm(), timeout=llm_remaining)
                )
            except asyncio.TimeoutError:
                logger.error(
                    f"查询超时（{query_timeout} 秒，阶段 3 LLM 调用用时 {elapsed:.0f}s），"
                    f"可调大 config.yaml 中 knowledge_graph.query_timeout"
                )
                raise TimeoutError(
                    f"查询超时（{query_timeout} 秒），"
                    f"可调大 config.yaml 中 knowledge_graph.query_timeout"
                )

            # —— 语义缓存 ——
            if embedding:
                self._query_cache.store(question, embedding, mode, result_str)

            elapsed = _time.time() - _t0
            logger.info(f"查询完成 ({elapsed:.1f}s) | mode={mode} | query=\"{question[:60]}\"")
            return result_str
        except Exception as e:
            elapsed = _time.time() - _t0
            logger.error(f"查询失败 ({elapsed:.1f}s): {e}")
            raise KnowledgeGraphError(str(e)) from e

    def _get_question_embedding(self, text: str) -> list[float]:
        """用缓存中保存的 embedding 客户端获取问题向量。"""
        import numpy as np

        async def _do_embed() -> list[float]:
            response = await self._embed_cache_client.embeddings.create(
                model=self._embed_cache_model,
                input=[text],
            )
            return response.data[0].embedding

        try:
            result = asyncio.run(_do_embed())
            if isinstance(result, (list, np.ndarray)):
                return list(result)
            return []
        except Exception:
            logger = get_logger("knowledge_graph")
            logger.warning(f"缓存嵌入失败，跳过缓存: {text[:40]}")
            return []

    def _rerank_chunks_with_bm25(self, prompt: str, question: str) -> str:
        """用 BM25 RRF 重排序 prompt 中 Document Chunks 的顺序。

        只重排已有块，不引入新块。通过 content 前缀匹配 chunk_id，
        再从 BM25 索引中获取对应分数。
        """
        try:
            start_marker = "Document Chunks"
            start = prompt.find(start_marker)
            if start < 0:
                return prompt

            json_start = prompt.find("```json\n", start)
            json_end = prompt.find("\n```", json_start + 8 if json_start > 0 else start)
            if json_start < 0 or json_end < 0:
                return prompt

            block_text = prompt[json_start + 8:json_end]
            entries = [json.loads(line) for line in block_text.strip().split("\n") if line.strip()]
            if not entries or not self._bm25 or not self._bm25._bm25:
                return prompt

            # 构建 content_prefix → chunk_id 映射
            chunks_path = self.working_dir / "kv_store_text_chunks.json"
            if not chunks_path.exists():
                return prompt
            all_chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
            prefix_map: dict[str, str] = {}
            for cid, cdata in all_chunks.items():
                prefix = cdata.get("content", "")[:60]
                prefix_map[prefix] = cid

            # 一次获取所有文档的 BM25 分数
            q_tokens = list(jieba.cut(question))
            all_scores = self._bm25._bm25.get_scores(q_tokens)

            # 通过 content 前缀匹配到 chunk_id，再查 BM25 分数
            entry_bm25_scores: list[float] = []
            for entry in entries:
                prefix = entry.get("content", "")[:60]
                cid = prefix_map.get(prefix)
                if cid:
                    idx = self._bm25._chunk_ids.index(cid) if cid in self._bm25._chunk_ids else -1
                    score = all_scores[idx] if idx >= 0 and idx < len(all_scores) else 0.0
                else:
                    score = 0.0
                entry_bm25_scores.append(score)

            # BM25 排名（降序）
            ranked_idx = sorted(
                range(len(entry_bm25_scores)),
                key=lambda i: entry_bm25_scores[i],
                reverse=True,
            )
            bm25_rank_map: dict[int, int] = {}
            for pos, idx in enumerate(ranked_idx):
                bm25_rank_map[idx] = pos

            # RRF
            k = self._bm25_rrf_k
            scored: list[tuple[float, dict]] = []
            for v_rank in range(len(entries)):
                b_rank = bm25_rank_map.get(v_rank, len(entries))
                rrf = 1.0 / (k + v_rank) + 1.0 / (k + b_rank)
                scored.append((rrf, entries[v_rank]))

            scored.sort(key=lambda x: x[0], reverse=True)
            reordered = [e for _, e in scored]

            new_block = "\n".join(json.dumps(e, ensure_ascii=False) for e in reordered)
            return prompt[:json_start + 8] + new_block + prompt[json_end:]
        except Exception:
            logger = get_logger("knowledge_graph")
            logger.warning("BM25 重排序失败，跳过")
            return prompt

    def list_documents(self) -> list[dict]:
        """返回已索引文档列表。

        从 LightRAG 的 KV 存储文件 kv_store_full_docs.json 中读取文档信息。
        LightRAG v1.5+ 的文档数据存储在 KV 中，不按文件系统目录组织。
        """
        self._ensure_initialized()

        try:
            docs_file = self.working_dir / "kv_store_full_docs.json"
            if not docs_file.exists():
                return []

            data: dict[str, Any] = json.loads(
                docs_file.read_text(encoding="utf-8")
            )

            entries: list[dict[str, Any]] = []
            for doc_id, doc_info in data.items():
                entries.append({
                    "id": doc_id,
                    "path": doc_info.get("file_path", ""),
                })

            return entries
        except Exception:
            return []

    def get_entities(self, doc_id: str | None = None) -> list[dict]:
        """获取知识图谱中的实体列表。

        Args:
            doc_id: 可选，按文档 ID 筛选。

        Returns:
            实体字典列表，每项含 name、type、description（如有）。
        """
        self._ensure_initialized()

        try:
            # LightRAG 1.5+ 把实体存在 kv_store_full_entities.json
            entity_file = self.working_dir / "kv_store_full_entities.json"
            if entity_file.exists():
                data: dict = json.loads(entity_file.read_text(encoding="utf-8"))
                entities: list[dict] = []
                for doc_key, doc_entities in data.items():
                    if doc_id and doc_id not in doc_key:
                        continue
                    names = doc_entities.get("entity_names", [])
                    for name in names:
                        if isinstance(name, str):
                            entities.append({"name": name, "source": doc_key})
                        elif isinstance(name, dict):
                            entities.append(name)
                return entities
            return []
        except Exception:
            return []

    def delete_document(self, doc_id: str) -> bool:
        """从知识图谱中删除文档。

        执行以下清理：
        - 从 LightRAG 知识图谱移除实体、关系、文本块、向量索引
        - 删除 kb_data/imports/ 下的 Markdown 文件
        - 重建 BM25 索引

        Args:
            doc_id: 文档唯一标识符。

        Returns:
            删除成功返回 True。
        """
        self._ensure_initialized()
        logger = get_logger("knowledge_graph")

        try:
            asyncio.run(self._rag.adelete_by_doc_id(doc_id))
        except AttributeError:
            raise KnowledgeGraphError(
                "当前 LightRAG 版本不支持文档删除，请升级至 lightrag-hku >= 1.4"
            )
        except Exception as e:
            raise KnowledgeGraphError(f"文档删除失败 [{doc_id}]: {e}") from e

        # 删除 Markdown 文件
        md_path = self.working_dir / "imports" / f"{doc_id}.md"
        if md_path.exists():
            md_path.unlink()
            logger.info(f"已删除 Markdown 文件: {md_path}")

        # 重建 BM25 索引
        if self._bm25:
            count = self._bm25.rebuild()
            logger.info(f"BM25 索引已重建 ({count} 个块)")

        return True

    def export_json(self, output_path: str | Path) -> Path:
        """将知识图谱导出为 JSON 文件。

        Args:
            output_path: 输出文件路径。

        Returns:
            写出的文件路径（Path 对象）。
        """
        self._ensure_initialized()

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        export_data: dict[str, Any] = {
            "version": "1.0",
            "entities": [],
            "relations": [],
        }

        # 从 LightRAG 工作目录中读取图谱数据
        graph_dir = self.working_dir / "graph_data"
        if graph_dir.exists():
            try:
                entity_file = graph_dir / "entities.json"
                if entity_file.exists():
                    export_data["entities"] = json.loads(
                        entity_file.read_text(encoding="utf-8")
                    )
            except Exception:
                pass

            try:
                relation_file = graph_dir / "relations.json"
                if relation_file.exists():
                    export_data["relations"] = json.loads(
                        relation_file.read_text(encoding="utf-8")
                    )
            except Exception:
                pass

        output_path.write_text(
            json.dumps(export_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return output_path

    # ------------------------------------------------------------------
    # Task 3.4: 自定义实体词典
    # ------------------------------------------------------------------

    def set_entity_dict(self, entities: dict[str, dict]) -> None:
        """设置自定义实体词典，用于增强 LLM 的实体提取。

        Args:
            entities: 格式为 {实体名: {"type": str, "aliases": list[str]}}
                      例如 {"Python": {"type": "编程语言", "aliases": ["python", "py"]}}
        """
        self._entity_dict = entities

    def _build_entity_prompt(self) -> str:
        """构建实体注入提示词，附加到文档内容前。

        Returns:
            提示词文本（包含换行）。
        """
        if not self._entity_dict:
            return ""

        lines = [
            "在以下文本中，请特别注意以下实体并确保它们被正确提取:",
        ]
        for name, info in self._entity_dict.items():
            entity_type = info.get("type", "概念")
            aliases = info.get("aliases", [])
            alias_str = f"（别名: {', '.join(aliases)}）" if aliases else ""
            lines.append(f"- {name}（类型: {entity_type}）{alias_str}")

        return "\n".join(lines) + "\n\n"

    # ------------------------------------------------------------------
    # Task 3.5: 实体别名解析
    # ------------------------------------------------------------------

    def resolve_aliases(self, text: str) -> str:
        """将文本中的别名替换为标准实体名。

        Args:
            text: 原始文本。

        Returns:
            替换别名后的文本。
        """
        if not self._entity_dict:
            return text

        result = text
        # 按名称长度降序排列，避免短名错误替换（如 "py" 替换 "python" 的子串）
        sorted_entities = sorted(
            self._entity_dict.items(),
            key=lambda x: len(x[0]),
            reverse=True,
        )

        for name, info in sorted_entities:
            for alias in info.get("aliases", []):
                if alias in result:
                    result = result.replace(alias, name)

        return result
