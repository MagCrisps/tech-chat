"""第 5 步：为论文数据集建立检索索引（关键词 BM25 + 语义向量）。

输入：datasets/ccf_a/{年份}/{会议名}/papers.jsonl + datasets/ccf/venues.json（补充领域、期刊/会议类型）
输出：storage/paper_index/
      papers.sqlite      论文表 + FTS5 全文索引（标题、摘要，BM25 排序）
      embeddings.npy     归一化向量（float16），第 i 行对应 papers.id = i + 1

- 增量：文本（标题 + 摘要）未变的论文直接复用旧向量，摘要补全后重建只计算变化部分；
- 先写到临时目录，完成后整体替换，建索引期间在线服务不受影响。

用法：python -m pipeline.build_index
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import time
from pathlib import Path

import numpy as np

from app.config import settings
from app.rag.store import get_embeddings
from pipeline.common import VENUES_FILE, read_jsonl

INDEX_DIR = settings.paper_index_path
BATCH = 256


def text_of(paper: dict) -> str:
    return f"{paper['title']}. {paper['abstract']}".strip() if paper["abstract"] else paper["title"]


def load_papers() -> list[dict]:
    venues = {v["short_name"]: v for v in json.loads(VENUES_FILE.read_text(encoding="utf-8")) if v["tier"] == "A"}
    papers = []
    for path in sorted(settings.paper_dataset_path.glob("[0-9][0-9][0-9][0-9]/*/papers.jsonl")):
        for row in read_jsonl(path):
            meta = venues.get(row["venue"], {})
            papers.append({
                **row,
                "category": meta.get("category_name", ""),
                "type": meta.get("type", ""),
                "hash": hashlib.sha1(text_of(row).encode("utf-8")).hexdigest(),
            })
    return papers


def load_previous() -> dict[str, np.ndarray]:
    """读取旧索引，返回 {文本哈希: 向量}，用于增量复用。"""
    db, emb = INDEX_DIR / "papers.sqlite", INDEX_DIR / "embeddings.npy"
    if not (db.exists() and emb.exists()):
        return {}
    vectors = np.load(emb)
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT id, text_hash FROM papers").fetchall()
    return {h: vectors[i - 1] for i, h in rows if i - 1 < len(vectors)}


def write_sqlite(path: Path, papers: list[dict]) -> None:
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE papers (
            id INTEGER PRIMARY KEY, title TEXT NOT NULL, abstract TEXT NOT NULL,
            venue TEXT NOT NULL, year INTEGER NOT NULL, category TEXT, type TEXT, text_hash TEXT
        );
        CREATE VIRTUAL TABLE papers_fts USING fts5(
            title, abstract, content='papers', content_rowid='id', tokenize='porter unicode61'
        );
    """)
    conn.executemany(
        "INSERT INTO papers VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(i + 1, p["title"], p["abstract"], p["venue"], p["year"], p["category"], p["type"], p["hash"])
         for i, p in enumerate(papers)],
    )
    conn.execute("INSERT INTO papers_fts(papers_fts) VALUES ('rebuild')")
    conn.executescript("""
        CREATE INDEX idx_papers_year ON papers(year);
        CREATE INDEX idx_papers_venue ON papers(venue);
    """)
    conn.commit()
    conn.close()


def main() -> None:
    started = time.time()
    papers = load_papers()
    previous = load_previous()
    todo = [i for i, p in enumerate(papers) if p["hash"] not in previous]
    print(f"论文 {len(papers)} 篇，可复用旧向量 {len(papers) - len(todo)} 篇，需新计算 {len(todo)} 篇")

    model = get_embeddings()._client  # 与在线查询使用同一个 SentenceTransformer
    dim = model.get_sentence_embedding_dimension()
    vectors = np.zeros((len(papers), dim), dtype=np.float16)
    for i, p in enumerate(papers):
        if p["hash"] in previous:
            vectors[i] = previous[p["hash"]]
    for start in range(0, len(todo), BATCH):
        idx = todo[start:start + BATCH]
        vectors[idx] = model.encode([text_of(papers[i]) for i in idx], batch_size=64, normalize_embeddings=True)
        done = start + len(idx)
        if (start // BATCH) % 40 == 0 or done == len(todo):
            rate = done / max(time.time() - started, 1e-6)
            print(f"  向量 {done}/{len(todo)}，约 {rate:.0f} 篇/秒", flush=True)

    tmp = INDEX_DIR.with_name(INDEX_DIR.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    write_sqlite(tmp / "papers.sqlite", papers)
    np.save(tmp / "embeddings.npy", vectors)
    shutil.rmtree(INDEX_DIR, ignore_errors=True)
    tmp.rename(INDEX_DIR)
    print(f"完成，用时 {time.time() - started:.0f}s；索引目录：{INDEX_DIR}")


if __name__ == "__main__":
    main()
