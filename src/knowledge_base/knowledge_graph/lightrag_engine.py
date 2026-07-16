"""基于 LightRAG 的知识图谱引擎实现（重构版 - Dedicated Worker Loop）。

设计原则（底层）：
- LightRAG 内部使用模块级 asyncio.Lock / PriorityQueue / 共享存储。
  这些对象绑定到「创建它们的 event loop」。
- 任何跨 loop 调用（asyncio.run、run_in_executor、不同线程的 get_event_loop）
  都会导致 "bound to a different event loop" 或 RuntimeError。
- 正确做法：整个进程生命周期内只有 **一个** 持久 event loop，运行在独立 daemon thread 上。
  所有对 LightRAG 的操作通过 asyncio.run_coroutine_threadsafe 提交到该 loop。
- 同步入口（CLI / Streamlit）只做「提交 + 等待结果」。
- 异步入口（MCP tools）直接 await 原生 aquery / aindex 等。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Coroutine, TypeVar

import jieba
import numpy as np
from openai import AsyncOpenAI

from knowledge_base.exceptions import KnowledgeGraphError
from knowledge_base.logger import get_logger

from .bm25_index import ChineseBM25Index
from .engine import KnowledgeGraphEngine
from .query_cache import QueryCache

T = TypeVar("T")
logger = get_logger("knowledge_graph")


class LightRAGEngine(KnowledgeGraphEngine):
    """基于 LightRAG 的知识图谱引擎（Dedicated Worker Thread + Persistent Loop）。

    对外同时提供：
    - 同步 API（query / index_document / ...）—— CLI / Streamlit 直接调用
    - 异步 API（aquery / aindex_document / ...）—— MCP / 其他 async 代码 await
    """

    def __init__(self, config: dict) -> None:
        self.config = config
        kg_config = config.get("knowledge_graph", {})
        self.working_dir = Path(kg_config.get("working_dir", "./kb_data"))
        self.working_dir.mkdir(parents=True, exist_ok=True)

        # 自定义实体词典
        self._entity_dict: dict[str, dict] = {}

        # ---------- Dedicated Loop & Worker Thread ----------
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()          # 初始化完成信号
        self._init_error: BaseException | None = None
        self._rag: Any = None
        self._llm_func: Any = None
        self._embed_cache_client: AsyncOpenAI | None = None
        self._embed_cache_model: str = "text-embedding-v4"
        self._initialized = False
        self._lock = threading.Lock()            # 保护 _start_worker 只执行一次

        # 查询语义缓存（纯同步，不依赖 loop）
        cache_cfg = kg_config.get("query_cache", {})
        if cache_cfg.get("enabled", True):
            threshold = cache_cfg.get("threshold", 0.92)
            max_entries = cache_cfg.get("max_entries", 1000)
        else:
            threshold = 1.1
            max_entries = 0
        self._query_cache = QueryCache(
            self.working_dir / "query_cache.json",
            threshold=threshold,
            max_entries=max_entries,
        )

        # BM25（纯同步）
        bm25_cfg = kg_config.get("bm25", {})
        self._bm25 = (
            ChineseBM25Index(self.working_dir)
            if bm25_cfg.get("enabled", True)
            else None
        )
        self._bm25_top_k = bm25_cfg.get("top_k", 20)
        self._bm25_rrf_k = bm25_cfg.get("rrf_k", 60)
        if self._bm25:
            jieba.cut("")  # 预加载词典，避免首次查询卡顿

        # 启动 worker（懒，但保证第一次操作前已 ready）
        self._start_worker()

    # ------------------------------------------------------------------
    # Worker Thread 核心
    # ------------------------------------------------------------------

    def _start_worker(self) -> None:
        """启动唯一的 daemon 线程，内部持有持久 event loop。"""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return

            self._ready.clear()
            self._init_error = None
            self._thread = threading.Thread(
                target=self._run_loop,
                name="LightRAG-Worker",
                daemon=True,
            )
            self._thread.start()

            # 等待初始化完成（或失败）
            if not self._ready.wait(timeout=120):
                raise KnowledgeGraphError(
                    "LightRAG worker 线程初始化超时（120s）。检查 LLM/Embedding API 连通性。"
                )
            if self._init_error is not None:
                raise KnowledgeGraphError(
                    f"LightRAG worker 初始化失败: {self._init_error}"
                ) from self._init_error

    def _run_loop(self) -> None:
        """Worker 线程入口：创建 loop → 初始化 LightRAG → run_forever。"""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop

            # 在 loop 上执行异步初始化
            loop.run_until_complete(self._async_initialize())
            self._initialized = True
            self._ready.set()

            # 永久运行，直到进程退出
            loop.run_forever()
        except BaseException as e:
            self._init_error = e
            self._ready.set()
            logger.exception("LightRAG worker 线程崩溃")
        finally:
            try:
                if self._loop and not self._loop.is_closed():
                    self._loop.close()
            except Exception:
                pass

    async def _async_initialize(self) -> None:
        """在 worker loop 内完成 LightRAG 的全部异步初始化。"""
        from lightrag import LightRAG
        from lightrag.utils import wrap_embedding_func_with_attrs

        llm_config = self.config.get("llm", {})
        embed_config = self.config.get("embedding", {})
        kg_config = self.config.get("knowledge_graph", {})

        api_key = llm_config.get("api_key", "")
        if not api_key:
            raise KnowledgeGraphError("llm.api_key 未配置")

        # ---- LLM ----
        llm_client = AsyncOpenAI(
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

            api_kwargs: dict[str, Any] = {
                "model": llm_config.get("model", "deepseek-v4-flash"),
                "messages": messages,
                "max_tokens": llm_config.get("max_tokens", 4096),
                "temperature": llm_config.get("temperature", 0.7),
            }
            # 跳过 response_format（部分 provider 不支持）
            response = await llm_client.chat.completions.create(**api_kwargs)
            return response.choices[0].message.content or ""

        self._llm_func = llm_model_func

        # ---- Embedding ----
        embed_api_key = embed_config.get("api_key") or api_key
        embed_base_url = embed_config.get("base_url")
        embed_model = embed_config.get("model", "text-embedding-v4")

        embed_client = AsyncOpenAI(
            api_key=embed_api_key,
            base_url=embed_base_url,
        )
        self._embed_cache_client = embed_client
        self._embed_cache_model = embed_model

        @wrap_embedding_func_with_attrs(
            embedding_dim=1024,
            max_token_size=8192,
            model_name=embed_model,
        )
        async def embedding_func(texts: list[str]) -> np.ndarray:
            response = await embed_client.embeddings.create(
                model=embed_model,
                input=texts,
            )
            return np.array([item.embedding for item in response.data])

        # ---- LightRAG 实例 ----
        provider = llm_config.get("provider", "")
        if provider == "xiaomi":
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

        # 关键：在同一个 loop 上初始化 storages（创建所有内部 Lock）
        await self._rag.initialize_storages()
        logger.info("LightRAG storages 初始化完成（dedicated loop）")

    # ------------------------------------------------------------------
    # 核心桥接：任意线程安全地提交协程到 worker loop
    # ------------------------------------------------------------------

    def _submit(self, coro: Coroutine[Any, Any, T], timeout: float | None = 120) -> T:
        """从任意线程提交协程到 worker loop，阻塞等待结果。

        这是所有同步 API 的唯一正确路径。
        """
        if self._loop is None or not self._loop.is_running():
            raise KnowledgeGraphError("LightRAG worker loop 未运行")

        future: Future[T] = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=timeout)
        except TimeoutError as e:
            future.cancel()
            raise KnowledgeGraphError(f"操作超时（{timeout}s）") from e
        except Exception as e:
            raise KnowledgeGraphError(str(e)) from e

    async def _ensure_ready(self) -> None:
        """async 路径使用：确保已初始化。"""
        if not self._initialized:
            raise KnowledgeGraphError("LightRAG 尚未初始化")

    # ------------------------------------------------------------------
    # 异步主 API（MCP / 未来 async 代码应直接 await 这些）
    # ------------------------------------------------------------------

    async def aindex_document(
        self, doc_id: str, content: str, metadata: dict | None = None
    ) -> int:
        """异步索引文档。"""
        await self._ensure_ready()

        if self._entity_dict:
            enhanced = self._build_entity_prompt() + "\n\n" + content
        else:
            enhanced = content

        kg_config = self.config.get("knowledge_graph", {})
        chunk_strategy = kg_config.get("chunk_strategy", "F")

        if chunk_strategy == "R":
            await self._rag.apipeline_enqueue_documents(
                enhanced, ids=[doc_id], process_options="R"
            )
            await self._rag.apipeline_process_enqueue_documents()
            logger.info(f"文档索引已提交 [{doc_id}]，分块策略: R")
        else:
            if hasattr(self._rag, "ainsert"):
                await self._rag.ainsert(enhanced, ids=[doc_id])
            else:
                # 回退：把同步 insert 放到 executor，避免阻塞 loop
                await self._loop.run_in_executor(
                    None, lambda: self._rag.insert(enhanced, ids=[doc_id])
                )
            logger.info(f"文档索引已提交 [{doc_id}]，分块策略: F")

        # BM25 重建（同步但轻量，放到 executor）
        if self._bm25:
            count = await self._loop.run_in_executor(None, self._bm25.rebuild)
            logger.info(f"BM25 索引已重建 ({count} 个块)")

        return 1

    async def aquery(self, question: str, mode: str = "hybrid") -> str:
        """异步查询知识图谱（推荐 MCP 使用）。"""
        await self._ensure_ready()
        t0 = time.time()

        # 1. 语义缓存
        embedding: list[float] | None = None
        if self._embed_cache_client:
            embedding = await self._aget_question_embedding(question)
            if embedding:
                cached = self._query_cache.lookup(embedding, mode)
                if cached is not None:
                    logger.info(
                        f"缓存命中 ({time.time() - t0:.1f}s) | mode={mode} | "
                        f'query="{question[:60]}"'
                    )
                    return cached

        try:
            from lightrag import QueryParam

            kg_config = self.config.get("knowledge_graph", {})
            bm25_cfg = kg_config.get("bm25", {})
            query_timeout = kg_config.get("query_timeout", 60)

            # 2. LightRAG 只生成 prompt（不调 LLM）
            if time.time() - t0 >= query_timeout:
                raise TimeoutError(f"查询超时（{query_timeout}s）")

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

            # 优先使用 aquery，否则回退到 run_in_executor 包装同步 query
            if hasattr(self._rag, "aquery"):
                result = await self._rag.aquery(question, param=prompt_param)
            else:
                result = await self._loop.run_in_executor(
                    None, lambda: self._rag.query(question, param=prompt_param)
                )
            prompt_str = str(result)

            # 3. BM25 RRF 重排序（纯 CPU，同步即可）
            if self._bm25 and bm25_cfg.get("enabled", True):
                prompt_str = self._rerank_chunks_with_bm25(prompt_str, question)

            # 4. 调用 LLM 生成最终回答
            elapsed = time.time() - t0
            llm_remaining = max(
                5,
                min(
                    self.config.get("llm", {}).get("timeout", 60),
                    query_timeout - elapsed,
                ),
            )
            logger.info("LLM 生成回答中（最长等待 %s 秒）…", llm_remaining)

            result_str = await asyncio.wait_for(
                self._llm_func(prompt=prompt_str, system_prompt=None),
                timeout=llm_remaining,
            )

            # 5. 写缓存
            if embedding:
                self._query_cache.store(question, embedding, mode, result_str)

            logger.info(
                f"查询完成 ({time.time() - t0:.1f}s) | mode={mode} | "
                f'query="{question[:60]}"'
            )
            return result_str

        except Exception as e:
            logger.error(f"查询失败 ({time.time() - t0:.1f}s): {e}")
            raise KnowledgeGraphError(str(e)) from e

    async def adelete_document(self, doc_id: str) -> bool:
        """异步删除文档。"""
        await self._ensure_ready()

        try:
            await self._rag.adelete_by_doc_id(doc_id)
        except AttributeError:
            raise KnowledgeGraphError(
                "当前 LightRAG 版本不支持 adelete_by_doc_id，请升级 lightrag-hku >= 1.4"
            )
        except Exception as e:
            raise KnowledgeGraphError(f"文档删除失败 [{doc_id}]: {e}") from e

        # 清理 Markdown
        md_path = self.working_dir / "imports" / f"{doc_id}.md"
        if md_path.exists():
            md_path.unlink()
            logger.info(f"已删除 Markdown 文件: {md_path}")

        if self._bm25:
            count = await self._loop.run_in_executor(None, self._bm25.rebuild)
            logger.info(f"BM25 索引已重建 ({count} 个块)")

        return True

    async def _aget_question_embedding(self, text: str) -> list[float]:
        """异步获取问题向量（用于语义缓存）。"""
        if not self._embed_cache_client:
            return []
        try:
            response = await self._embed_cache_client.embeddings.create(
                model=self._embed_cache_model,
                input=[text],
            )
            emb = response.data[0].embedding
            return list(emb) if emb is not None else []
        except Exception as e:
            logger.warning(f"缓存嵌入失败，跳过缓存: {text[:40]} | {e}")
            return []

    # ------------------------------------------------------------------
    # 同步 API（CLI / Streamlit 使用，内部全部走 _submit）
    # ------------------------------------------------------------------

    def index_document(
        self, doc_id: str, content: str, metadata: dict | None = None
    ) -> int:
        """同步索引文档。"""
        return self._submit(self.aindex_document(doc_id, content, metadata))

    def query(self, question: str, mode: str = "hybrid") -> str:
        """同步查询。"""
        timeout = self.config.get("knowledge_graph", {}).get("query_timeout", 60) + 10
        return self._submit(self.aquery(question, mode=mode), timeout=timeout)

    def delete_document(self, doc_id: str) -> bool:
        """同步删除。"""
        return self._submit(self.adelete_document(doc_id))

    def list_documents(self) -> list[dict]:
        """返回已索引文档列表（纯文件 I/O，无需 loop）。"""
        try:
            docs_file = self.working_dir / "kv_store_full_docs.json"
            if not docs_file.exists():
                return []
            data: dict[str, Any] = json.loads(docs_file.read_text(encoding="utf-8"))
            return [
                {"id": doc_id, "path": info.get("file_path", "")}
                for doc_id, info in data.items()
            ]
        except Exception:
            return []

    def get_entities(self, doc_id: str | None = None) -> list[dict]:
        """获取实体列表（纯文件 I/O）。"""
        try:
            entity_file = self.working_dir / "kv_store_full_entities.json"
            if not entity_file.exists():
                return []
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
        except Exception:
            return []

    def export_json(self, output_path: str | Path) -> Path:
        """导出图谱为 JSON。"""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        export_data: dict[str, Any] = {
            "version": "1.0",
            "entities": [],
            "relations": [],
        }

        graph_dir = self.working_dir / "graph_data"
        if graph_dir.exists():
            for name, key in [
                ("entities.json", "entities"),
                ("relations.json", "relations"),
            ]:
                f = graph_dir / name
                if f.exists():
                    try:
                        export_data[key] = json.loads(f.read_text(encoding="utf-8"))
                    except Exception:
                        pass

        output_path.write_text(
            json.dumps(export_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return output_path

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def set_entity_dict(self, entities: dict[str, dict]) -> None:
        self._entity_dict = entities

    def _build_entity_prompt(self) -> str:
        if not self._entity_dict:
            return ""
        lines = ["在以下文本中，请特别注意以下实体并确保它们被正确提取:"]
        for name, info in self._entity_dict.items():
            entity_type = info.get("type", "概念")
            aliases = info.get("aliases", [])
            alias_str = f"（别名: {', '.join(aliases)}）" if aliases else ""
            lines.append(f"- {name}（类型: {entity_type}）{alias_str}")
        return "\n".join(lines) + "\n\n"

    def resolve_aliases(self, text: str) -> str:
        if not self._entity_dict:
            return text
        result = text
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

    def _rerank_chunks_with_bm25(self, prompt: str, question: str) -> str:
        """用 BM25 RRF 重排序 prompt 中 Document Chunks 的顺序。"""
        try:
            start_marker = "Document Chunks"
            start = prompt.find(start_marker)
            if start < 0:
                return prompt

            json_start = prompt.find("```json\n", start)
            json_end = prompt.find("\n```", json_start + 8 if json_start > 0 else start)
            if json_start < 0 or json_end < 0:
                return prompt

            block_text = prompt[json_start + 8 : json_end]
            entries = [
                json.loads(line)
                for line in block_text.strip().split("\n")
                if line.strip()
            ]
            if not entries or not self._bm25 or not getattr(self._bm25, "_bm25", None):
                return prompt

            chunks_path = self.working_dir / "kv_store_text_chunks.json"
            if not chunks_path.exists():
                return prompt
            all_chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
            prefix_map: dict[str, str] = {}
            for cid, cdata in all_chunks.items():
                prefix = cdata.get("content", "")[:60]
                prefix_map[prefix] = cid

            q_tokens = list(jieba.cut(question))
            all_scores = self._bm25._bm25.get_scores(q_tokens)

            entry_bm25_scores: list[float] = []
            for entry in entries:
                prefix = entry.get("content", "")[:60]
                cid = prefix_map.get(prefix)
                if cid and cid in self._bm25._chunk_ids:
                    idx = self._bm25._chunk_ids.index(cid)
                    score = all_scores[idx] if 0 <= idx < len(all_scores) else 0.0
                else:
                    score = 0.0
                entry_bm25_scores.append(score)

            ranked_idx = sorted(
                range(len(entry_bm25_scores)),
                key=lambda i: entry_bm25_scores[i],
                reverse=True,
            )
            bm25_rank_map = {idx: pos for pos, idx in enumerate(ranked_idx)}

            k = self._bm25_rrf_k
            scored: list[tuple[float, dict]] = []
            for v_rank in range(len(entries)):
                b_rank = bm25_rank_map.get(v_rank, len(entries))
                rrf = 1.0 / (k + v_rank) + 1.0 / (k + b_rank)
                scored.append((rrf, entries[v_rank]))

            scored.sort(key=lambda x: x[0], reverse=True)
            reordered = [e for _, e in scored]

            new_block = "\n".join(
                json.dumps(e, ensure_ascii=False) for e in reordered
            )
            return prompt[: json_start + 8] + new_block + prompt[json_end:]
        except Exception:
            logger.warning("BM25 重排序失败，跳过")
            return prompt

    def shutdown(self) -> None:
        """优雅关闭 worker（可选，进程退出时 daemon 会自动清理）。"""
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)