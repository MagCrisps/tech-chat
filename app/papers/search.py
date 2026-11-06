"""论文混合检索：关键词（SQLite FTS5 / BM25）+ 语义向量（余弦相似度），RRF 融合。

索引由 `python -m pipeline.build_index` 生成；检测到索引文件更新后会自动重新加载。
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np

from app.config import settings
from app.rag.store import get_embeddings

logger = logging.getLogger(__name__)

RRF_K = 60          # RRF 平滑常数（常用取值）
CANDIDATES = 200    # 每一路检索取多少候选参与融合
TITLE_WEIGHT = 5.0  # BM25 中标题相对摘要的权重

STOPWORDS = frozenset("""
a an and are as at be by for from how in into is it of on or that the this to via what which with
using based towards toward approach method methods paper papers study studies about
""".split())


@dataclass
class Filters:
    year_from: Optional[int] = None
    year_to: Optional[int] = None
    venues: Optional[list[str]] = None
    categories: Optional[list[str]] = None
    types: Optional[list[str]] = None  # "journal" / "conference"

    def sql(self) -> tuple[str, list[Any]]:
        clauses, params = [], []
        if self.year_from:
            clauses.append("p.year >= ?"); params.append(self.year_from)
        if self.year_to:
            clauses.append("p.year <= ?"); params.append(self.year_to)
        for column, values in (("venue", self.venues), ("category", self.categories), ("type", self.types)):
            if values:
                clauses.append(f"p.{column} IN ({','.join('?' * len(values))})"); params.extend(values)
        return (" AND " + " AND ".join(clauses)) if clauses else "", params


def fts_query(text: str) -> str | None:
    """把自然语言问题转成 FTS5 查询：抽取英文词，去停用词，OR 连接（召回优先，排序交给 BM25）。"""
    words = [w for w in re.findall(r"[a-z0-9][a-z0-9\-]*", text.lower()) if w not in STOPWORDS and len(w) > 1]
    words = list(dict.fromkeys(words))[:24]
    return " OR ".join(f'"{w}"' for w in words) if words else None


class PaperIndex:
    """加载一次、常驻内存：向量矩阵 + 用于筛选的元数据数组；标题摘要等正文按需从 SQLite 读取。"""

    def __init__(self, index_dir: Path) -> None:
        self.db_path = index_dir / "papers.sqlite"
        emb_path = index_dir / "embeddings.npy"
        self.version = emb_path.stat().st_mtime
        self.vectors = np.load(emb_path).astype(np.float32)  # float16 在 CPU 上做矩阵乘很慢，加载时转 float32
        with self._connect() as conn:
            rows = conn.execute("SELECT year, venue, category, type FROM papers ORDER BY id").fetchall()
        self.years = np.array([r[0] for r in rows], dtype=np.int16)
        self.venues = np.array([r[1] for r in rows], dtype=object)
        self.categories = np.array([r[2] for r in rows], dtype=object)
        self.types = np.array([r[3] for r in rows], dtype=object)
        logger.info("论文索引已加载：%d 篇，向量维度 %d", len(rows), self.vectors.shape[1])

    def _connect(self) -> sqlite3.Connection:
        # 每次查询新建只读连接：SQLite 连接不能跨线程共享，而建立连接本身只需毫秒级
        return sqlite3.connect(f"file:{self.db_path.as_posix()}?mode=ro", uri=True)

    # ------------------------------------------------------------------ #
    def _mask(self, f: Filters) -> np.ndarray:
        mask = np.ones(len(self.years), dtype=bool)
        if f.year_from:
            mask &= self.years >= f.year_from
        if f.year_to:
            mask &= self.years <= f.year_to
        for values, column in ((f.venues, self.venues), (f.categories, self.categories), (f.types, self.types)):
            if values:
                mask &= np.isin(column, values)
        return mask

    def _dense(self, query: str, f: Filters) -> list[int]:
        model = get_embeddings()._client  # 与建索引时使用同一个模型
        q = model.encode(query, normalize_embeddings=True).astype(np.float32)
        scores = self.vectors @ q
        scores[~self._mask(f)] = -np.inf
        n = min(CANDIDATES, int(np.isfinite(scores).sum()))
        if n == 0:
            return []
        top = np.argpartition(-scores, n - 1)[:n]
        return [int(i) + 1 for i in top[np.argsort(-scores[top])]]  # 行号 -> papers.id

    def _bm25(self, query: str, f: Filters, conn: sqlite3.Connection) -> list[int]:
        match = fts_query(query)
        if not match:
            return []  # 纯中文等无英文词的查询：只走语义检索
        where, params = f.sql()
        sql = (
            "SELECT p.id FROM papers_fts JOIN papers p ON p.id = papers_fts.rowid "
            f"WHERE papers_fts MATCH ?{where} ORDER BY bm25(papers_fts, {TITLE_WEIGHT}, 1.0) LIMIT {CANDIDATES}"
        )
        return [row[0] for row in conn.execute(sql, [match, *params])]

    def search(self, query: str, k: int = 10, filters: Filters | None = None) -> dict[str, Any]:
        """混合检索，返回 {results, took_ms}；每条结果附带两路检索中的名次，便于调试与评测。"""
        f = filters or Filters()
        started = time.perf_counter()
        with self._connect() as conn:
            dense = self._dense(query, f)
            bm25 = self._bm25(query, f, conn)
            fused: dict[int, float] = {}
            for ranking in (dense, bm25):
                for rank, pid in enumerate(ranking, start=1):
                    fused[pid] = fused.get(pid, 0.0) + 1.0 / (RRF_K + rank)
            top = sorted(fused, key=fused.get, reverse=True)[:k]
            rows = {
                r[0]: r for r in conn.execute(
                    f"SELECT id, title, abstract, venue, year, category, type FROM papers "
                    f"WHERE id IN ({','.join('?' * len(top))})", top,
                )
            } if top else {}
        dense_rank = {pid: i for i, pid in enumerate(dense, start=1)}
        bm25_rank = {pid: i for i, pid in enumerate(bm25, start=1)}
        results = [
            {
                "id": pid, "title": rows[pid][1], "abstract": rows[pid][2], "venue": rows[pid][3],
                "year": rows[pid][4], "category": rows[pid][5], "type": rows[pid][6],
                "score": round(fused[pid], 5), "dense_rank": dense_rank.get(pid), "bm25_rank": bm25_rank.get(pid),
            }
            for pid in top if pid in rows
        ]
        return {"results": results, "took_ms": round((time.perf_counter() - started) * 1000, 1)}

    def facets(self) -> dict[str, Any]:
        """筛选面板所需的元数据：年份范围、领域及其下各刊物的论文数。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT category, venue, type, COUNT(*) FROM papers GROUP BY category, venue, type ORDER BY category, COUNT(*) DESC"
            ).fetchall()
        categories: dict[str, list[dict[str, Any]]] = {}
        for category, venue, kind, count in rows:
            categories.setdefault(category, []).append({"name": venue, "type": kind, "count": count})
        return {
            "total": int(len(self.years)),
            "years": [int(self.years.min()), int(self.years.max())],
            "categories": [{"name": name, "venues": venues} for name, venues in categories.items()],
        }


_index: Optional[PaperIndex] = None
_lock = threading.Lock()


def get_paper_index() -> PaperIndex:
    """返回常驻的索引实例；索引文件被 build_index 重建后自动重新加载。"""
    global _index
    emb_path = settings.paper_index_path / "embeddings.npy"
    if not emb_path.exists():
        raise FileNotFoundError(f"论文索引不存在：{settings.paper_index_path}（请先运行 python -m pipeline.build_index）")
    if _index is None or _index.version != emb_path.stat().st_mtime:
        with _lock:
            if _index is None or _index.version != emb_path.stat().st_mtime:
                _index = PaperIndex(settings.paper_index_path)
    return _index


def as_filters(year_from: Optional[int] = None, year_to: Optional[int] = None,
               venues: Optional[Iterable[str]] = None, categories: Optional[Iterable[str]] = None,
               types: Optional[Iterable[str]] = None) -> Filters:
    """把接口 / 工具参数整理成 Filters（空列表视为不筛选）。"""
    return Filters(
        year_from=year_from or None, year_to=year_to or None,
        venues=list(venues) or None if venues else None,
        categories=list(categories) or None if categories else None,
        types=list(types) or None if types else None,
    )
