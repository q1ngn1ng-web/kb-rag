"""BM25 关键词检索引擎 — 中文分词 + BM25Okapi 多路召回。"""

from __future__ import annotations

import json
import pickle
import time
from pathlib import Path
from typing import Any

import jieba
from rank_bm25 import BM25Okapi


class ChineseBM25Index:
    """基于 jieba 分词 + BM25Okapi 的中文关键词检索引擎。

    从 kv_store_text_chunks.json 读取所有块，构建 BM25 索引。
    持久化到 bm25_index.pkl，避免每次重启重建。
    """

    def __init__(self, working_dir: str | Path) -> None:
        self.working_dir = Path(working_dir)
        self.index_path = self.working_dir / "bm25_index.pkl"

        self._chunk_ids: list[str] = []
        self._bm25: BM25Okapi | None = None

        self._tokenized_corpus: list[list[str]] = []
        if self.index_path.exists():
            self._load()

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        """搜索与查询最相关的 top_k 个块。

        Args:
            query: 用户查询文本。
            top_k: 返回的候选块数。

        Returns:
            [(chunk_id, bm25_score), ...]，按分数降序排列。
        """
        if self._bm25 is None:
            return []
        tokens = self._tokenize(query)
        scores = self._bm25.get_scores(tokens)
        ranked = sorted(
            zip(self._chunk_ids, scores),
            key=lambda x: x[1],
            reverse=True,
        )
        return ranked[:top_k]

    def rebuild(self) -> int:
        """从 kv_store_text_chunks.json 重建 BM25 索引。

        Returns:
            索引的块数。
        """
        chunks_path = self.working_dir / "kv_store_text_chunks.json"
        if not chunks_path.exists():
            self._bm25 = None
            self._chunk_ids = []
            return 0

        data: dict[str, Any] = json.loads(chunks_path.read_text(encoding="utf-8"))
        self._chunk_ids = list(data.keys())
        corpus = [self._tokenize(v.get("content", "")) for v in data.values()]
        self._bm25 = BM25Okapi(corpus)
        self._save()
        return len(self._chunk_ids)
    
    def incremental_update_for_doc(self, doc_id: str) -> int:
        """增量更新：只对新出现的 chunk 做 jieba，只重建 BM25 内部结构。
        避免重复 tokenize 历史 chunk（这是 CPU 热点）。
        """
        chunks_path = self.working_dir / "kv_store_text_chunks.json"
        if not chunks_path.exists():
            return 0

        data: dict[str, Any] = json.loads(chunks_path.read_text(encoding="utf-8"))
        current_set = set(self._chunk_ids)
        new_ids = [cid for cid in data.keys() if cid not in current_set]
        if not new_ids:
            return 0

        # 只 tokenize 新 chunk（核心优化点）
        new_corpus = [self._tokenize(data[cid].get("content", "")) for cid in new_ids]
        self._tokenized_corpus.extend(new_corpus)
        self._chunk_ids.extend(new_ids)

        # 重建 BM25Okapi（必须全量传 corpus，但 tokenized 已经缓存）
        self._bm25 = BM25Okapi(self._tokenized_corpus)
        self._save()
        return len(new_ids)
    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _tokenize(self, text: str) -> list[str]:
        return list(jieba.cut(text))

    def _save(self) -> None:
        payload = {
            "chunk_ids": self._chunk_ids,
            "bm25": self._bm25,
            "tokenized_corpus": self._tokenized_corpus,
            "updated_at": time.time(),
        }
        ...

    def _load(self) -> None:
        try:
            payload = pickle.loads(self.index_path.read_bytes())
            self._chunk_ids = payload.get("chunk_ids", [])
            self._bm25 = payload.get("bm25")
            self._tokenized_corpus = payload.get("tokenized_corpus", [])
        except (pickle.UnpicklingError, EOFError, KeyError):
            self._bm25 = None
            self._chunk_ids = []
            self._tokenized_corpus = []

