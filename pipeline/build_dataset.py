"""第 4 步：合并论文清单与摘要，按「年份/会议名/papers.jsonl」写出最终数据集。

输入：_work/dblp_papers.jsonl + _work/openalex_abstracts.jsonl（按 DOI）+ _work/site_abstracts.jsonl（按 DBLP 键）
输出：datasets/ccf_a/{年份}/{会议名}/papers.jsonl，每行只含 4 个字段：
      {"title": ..., "abstract": ..., "venue": ..., "year": ...}
      暂未获取到摘要的论文 abstract 为空字符串（后续补全后重跑本步即可）。
      另生成 datasets/ccf_a/stats.json（每个刊物、每年的论文数与摘要覆盖率）。

用法：python -m pipeline.build_dataset
"""

from __future__ import annotations

import json
import shutil
from collections import defaultdict

from pipeline.common import ABSTRACT_CACHE, WORK_DIR, DBLP_PAPERS, OUTPUT_DIR, read_jsonl, venue_dir_name, write_jsonl


def main() -> None:
    by_doi = {row["doi"]: row["abstract"] for row in read_jsonl(ABSTRACT_CACHE) if row.get("abstract")}
    by_key = {row["key"]: row["abstract"] for row in read_jsonl(WORK_DIR / "site_abstracts.jsonl") if row.get("abstract")}

    groups: dict[tuple[int, str], list[dict]] = defaultdict(list)
    seen: set[tuple[str, int, str]] = set()
    for paper in read_jsonl(DBLP_PAPERS):
        dedup = (paper["venue"], paper["year"], paper["title"].lower())
        if dedup in seen:
            continue
        seen.add(dedup)
        groups[(paper["year"], paper["venue"])].append({
            "title": paper["title"],
            "abstract": by_doi.get(paper["doi"] or "") or by_key.get(paper["key"]) or "",
            "venue": paper["venue"],
            "year": paper["year"],
        })

    # 清理旧的年份目录后重写（_work 等以下划线开头的目录保留）
    for child in OUTPUT_DIR.glob("[0-9][0-9][0-9][0-9]"):
        shutil.rmtree(child)

    stats: dict[str, dict] = defaultdict(lambda: {"papers": 0, "with_abstract": 0, "by_year": {}})
    for (year, venue), papers in sorted(groups.items()):
        papers.sort(key=lambda p: p["title"])
        write_jsonl(OUTPUT_DIR / str(year) / venue_dir_name(venue) / "papers.jsonl", papers)
        with_abstract = sum(bool(p["abstract"]) for p in papers)
        entry = stats[venue]
        entry["papers"] += len(papers)
        entry["with_abstract"] += with_abstract
        entry["by_year"][str(year)] = {"papers": len(papers), "with_abstract": with_abstract}

    total = sum(s["papers"] for s in stats.values())
    covered = sum(s["with_abstract"] for s in stats.values())
    summary = {
        "total_papers": total,
        "with_abstract": covered,
        "venues": dict(sorted(stats.items(), key=lambda kv: -kv[1]["papers"])),
    }
    (OUTPUT_DIR / "stats.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"共 {total} 篇，{len(stats)} 个刊物，{len(groups)} 个「年份/会议」目录；摘要覆盖 {covered / max(total, 1):.1%}")
    print(f"{'刊物':<18}{'论文数':>8}{'摘要覆盖':>10}")
    for venue, entry in summary["venues"].items():
        print(f"{venue:<18}{entry['papers']:>8}{entry['with_abstract'] / entry['papers']:>10.0%}")
    print(f"已写入 {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
