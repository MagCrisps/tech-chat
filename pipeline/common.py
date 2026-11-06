"""数据管线公共部分：路径、CCF-A 刊物加载、JSONL 读写。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Iterator

ROOT = Path(__file__).resolve().parent.parent
DATASETS = ROOT / "datasets"
VENUES_FILE = DATASETS / "ccf" / "venues.json"
DBLP_DIR = DATASETS / "dblp"
DBLP_DUMP = DBLP_DIR / "dblp.xml.gz"

# 最终数据集：datasets/ccf_a/{年份}/{会议名}/papers.jsonl
OUTPUT_DIR = DATASETS / "ccf_a"
# 中间产物（可随时删除重建）
WORK_DIR = OUTPUT_DIR / "_work"
DBLP_PAPERS = WORK_DIR / "dblp_papers.jsonl"
ABSTRACT_CACHE = WORK_DIR / "openalex_abstracts.jsonl"

MIN_YEAR = 2020  # 收录起始年份


# 部分会议近年改为在 ACM / VLDB 期刊上发表，DBLP 中归在期刊键下：
# {期刊键前缀: [(期号 <number> 的匹配规则, 会议简称)]}，规则为 None 表示整本期刊都归该会议
JOURNAL_ROUTES: dict[str, list[tuple[str | None, str]]] = {
    "journals/pacmpl": [(r"^POPL", "POPL"), (r"^OOPSLA", "OOPSLA")],
    "journals/pacmhci": [(r"^CSCW", "CSCW")],
    "journals/pvldb": [(None, "VLDB")],
}


def load_a_venues() -> dict[str, dict[str, Any]]:
    """返回 {DBLP 键前缀: 刊物信息}，如 {"conf/cvpr": {...}}，仅 A 类。"""
    venues = json.loads(VENUES_FILE.read_text(encoding="utf-8"))
    return {v["dblp_key"]: v for v in venues if v["tier"] == "A" and v["dblp_key"]}


def load_journal_routes() -> dict[str, list[tuple[re.Pattern | None, dict[str, Any]]]]:
    """把 JOURNAL_ROUTES 中的会议简称解析为刊物信息。"""
    by_name = {v["short_name"]: v for v in load_a_venues().values()}
    return {
        prefix: [(re.compile(rule) if rule else None, by_name[name]) for rule, name in routes]
        for prefix, routes in JOURNAL_ROUTES.items()
    }


def venue_dir_name(short_name: str) -> str:
    """把刊物简称转成安全的目录名：如 'UbiComp/IMWUT' -> 'UbiComp_IMWUT'。"""
    return re.sub(r"[^\w&+-]+", "_", short_name).strip("_")


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue  # 进程被中断时可能留下半行，跳过即可


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count
