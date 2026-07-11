"""查询语义缓存 — 基于向量余弦相似度识别同义问题。"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any


class QueryCache:
    """语义查询缓存。

    存储（问题向量, mode, 回答）三元组，通过余弦相似度匹配同义问题。
    持久化到 JSON 文件，重启后可用。
    """

    def __init__(
        self,
        path: str | Path,
        threshold: float = 0.95,
        max_entries: int = 1000,
    ) -> None:
        self.path = Path(path)
        self.threshold = threshold
        self.max_entries = max_entries
        self._entries: list[dict[str, Any]] = self._load()

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def lookup(self, embedding: list[float], mode: str) -> str | None:
        """查缓存：返回与 embedding 最相似的条目的回答，或 None。

        Args:
            embedding: 查询问题的向量。
            mode: 查询模式，仅匹配同 mode 的缓存。

        Returns:
            缓存的回答文本，或 None。
        """
        best_sim = 0.0
        best_idx = -1
        for i, entry in enumerate(self._entries):
            if entry.get("mode") != mode:
                continue
            sim = _cosine_similarity(embedding, entry.get("embedding", []))
            if sim > best_sim:
                best_sim = sim
                best_idx = i
        if best_idx >= 0 and best_sim >= self.threshold:
            return self._entries[best_idx].get("response")
        return None

    def store(
        self,
        question: str,
        embedding: list[float],
        mode: str,
        response: str,
    ) -> None:
        """存入一条缓存。

        Args:
            question: 原始问题文本。
            embedding: 问题向量。
            mode: 查询模式。
            response: LLM 回答。
        """
        self._entries.append({
            "question": question,
            "embedding": embedding,
            "mode": mode,
            "response": response,
            "timestamp": time.time(),
        })
        # 超限淘汰最旧
        if len(self._entries) > self.max_entries:
            self._entries.sort(key=lambda x: x.get("timestamp", 0))
            self._entries = self._entries[-self.max_entries:]
        self._save()

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def _load(self) -> list[dict[str, Any]]:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        return []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._entries, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


# ------------------------------------------------------------------
# 工具
# ------------------------------------------------------------------

def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
