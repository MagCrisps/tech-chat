"""小数据集：从 CCF-A 全量清单中按「领域 × 年份」筛出子集，输出到 datasets/ccf_a_mini/。

默认条件：人工智能、软件工程/系统软件/程序设计语言、数据库/数据挖掘/内容检索 × 2023–2025 年。
只收录有摘要的论文（摘要来自 _work 中的 OpenAlex / 官方站点缓存）。

用法：python -m pipeline.build_mini [--max N]   # N：目标篇数上限，按刊物均匀抽样
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
from collections import Counter, defaultdict

from pipeline.common import (
    ABSTRACT_CACHE, DATASETS, DBLP_PAPERS, VENUES_FILE, WORK_DIR, read_jsonl, venue_dir_name, write_jsonl,
)

MINI_DIR = DATASETS / "ccf_a_mini"
CATEGORIES = {"人工智能", "软件工程/系统软件/程序设计语言", "数据库/数据挖掘/内容检索"}
YEARS = range(2023, 2026)


def load_abstracts() -> tuple[dict[str, str], dict[str, str]]:
    by_doi = {r["doi"]: r["abstract"] for r in read_jsonl(ABSTRACT_CACHE) if r.get("abstract")}
    by_key = {r["key"]: r["abstract"] for r in read_jsonl(WORK_DIR / "site_abstracts.jsonl") if r.get("abstract")}
    return by_doi, by_key


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max", type=int, default=0, help="篇数上限（0 = 不限制）")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写文件")
    args = parser.parse_args()

    venue_meta = {v["short_name"]: v for v in json.loads(VENUES_FILE.read_text(encoding="utf-8")) if v["tier"] == "A"}
    by_doi, by_key = load_abstracts()

    candidates: dict[str, list[dict]] = defaultdict(list)
    missing: Counter = Counter()
    seen: set[tuple] = set()
    for p in read_jsonl(DBLP_PAPERS):
        meta = venue_meta.get(p["venue"])
        if not meta or meta["category_name"] not in CATEGORIES or p["year"] not in YEARS:
            continue
        dedup = (p["venue"], p["year"], p["title"].lower())
        if dedup in seen:
            continue
        seen.add(dedup)
        abstract = by_doi.get(p["doi"] or "") or by_key.get(p["key"])
        if not abstract:
            missing[p["venue"]] += 1
            continue
        candidates[p["venue"]].append({"title": p["title"], "abstract": abstract, "venue": p["venue"], "year": p["year"]})

    total = sum(len(v) for v in candidates.values())
    print(f"候选（有摘要）{total} 篇；缺摘要 {sum(missing.values())} 篇")
    print(f"{'刊物':<16}{'有摘要':>8}{'缺摘要':>8}")
    for venue in sorted(set(candidates) | set(missing), key=lambda v: -len(candidates.get(v, []))):
        print(f"{venue:<16}{len(candidates.get(venue, [])):>8}{missing.get(venue, 0):>8}")

    if args.max and total > args.max:
        # 按刊物比例抽样，保证每个刊物都有代表
        random.seed(42)
        ratio = args.max / total
        for venue, papers in candidates.items():
            random.shuffle(papers)
            candidates[venue] = sorted(papers[: max(20, round(len(papers) * ratio))], key=lambda x: (x["year"], x["title"]))
        total = sum(len(v) for v in candidates.values())
        print(f"\n按比例抽样至 {total} 篇")
    if args.dry_run:
        return

    shutil.rmtree(MINI_DIR, ignore_errors=True)
    groups: dict[tuple, list] = defaultdict(list)
    for papers in candidates.values():
        for p in papers:
            groups[(p["year"], p["venue"])].append(p)
    stats: dict[str, dict] = {}
    for (year, venue), papers in sorted(groups.items()):
        write_jsonl(MINI_DIR / str(year) / venue_dir_name(venue) / "papers.jsonl", sorted(papers, key=lambda x: x["title"]))
        stats.setdefault(venue, {"papers": 0, "category": venue_meta[venue]["category_name"], "by_year": {}})
        stats[venue]["papers"] += len(papers)
        stats[venue]["by_year"][str(year)] = len(papers)
    (MINI_DIR / "stats.json").write_text(json.dumps({
        "total_papers": total, "years": [min(YEARS), max(YEARS)], "categories": sorted(CATEGORIES), "venues": stats,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {MINI_DIR}：{total} 篇，{len(stats)} 个刊物，{len(groups)} 个「年份/会议」目录")


if __name__ == "__main__":
    main()
