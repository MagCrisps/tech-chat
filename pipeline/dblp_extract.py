"""第 2 步：从 DBLP 全量数据中抽取 CCF-A 论文清单。

输入：datasets/dblp/dblp.xml.gz + dblp.dtd（https://dblp.org/xml/，CC0 许可）
输出：datasets/ccf_a/_work/dblp_papers.jsonl（中间产物，含 DOI 用于第 3 步补摘要）

筛选规则：
- DBLP 键前缀属于 CCF-A 刊物（如 conf/cvpr/、journals/pami/），年份 >= MIN_YEAR；
- 剔除 Workshop / Findings / Companion / Extended Abstracts 等非主会论文集（含 CEUR-WS、非 ICML 的 PMLR 卷）；
- 剔除前言、目录、社论、勘误、撤稿等非研究论文条目。

用法：python -m pipeline.dblp_extract
"""

from __future__ import annotations

import gzip
import os
import re
import time
from collections import Counter

from lxml import etree

from pipeline.common import DBLP_DIR, DBLP_DUMP, DBLP_PAPERS, MIN_YEAR, load_a_venues, load_journal_routes, write_jsonl

RECORD_TAGS = {
    "article", "inproceedings", "proceedings", "book", "incollection",
    "phdthesis", "mastersthesis", "www", "person", "data",
}
EXCLUDED_BOOKTITLE = re.compile(
    r"workshop|findings|companion|extended abstracts|adjunct|demo|tutorial|doctoral|student research|poster",
    re.IGNORECASE,
)
EXCLUDED_TITLE = re.compile(
    r"^(front ?matter|preface|proceedings|table of contents|contents|message from|editorial|guest editorial|"
    r"editor'?s? note|corrections? to|correction:|erratum|errata|retraction|retracted|author index|index|"
    r"reviewers|list of reviewers|keynote|in memoriam|obituary|introduction to the special)(?=\W|$)",
    re.IGNORECASE,
)


def is_workshop_venue(venue: str, urls: list[str]) -> bool:
    """DBLP 把部分 Workshop 论文集挂在主会键下：CEUR-WS 均为 Workshop；PMLR 上非 ICML/JMLR 的也是 Workshop / 竞赛轨道。"""
    hosts = " ".join(urls)
    if "ceur-ws.org" in hosts or re.search(r"wkshps?|workshop", hosts, re.IGNORECASE):
        return True  # 如 10.1109/INFOCOMWKSHPS…：卷名不含 workshop，但 DOI 中有
    return "proceedings.mlr.press" in hosts and venue not in ("ICML", "JMLR")


def clean_title(element: etree._Element) -> str:
    title = " ".join("".join(element.itertext()).split())
    return title[:-1] if title.endswith(".") else title


def extract_doi(urls: list[str]) -> str | None:
    for url in urls:
        match = re.search(r"doi\.org/(10\..+)$", url)
        if match:
            return match.group(1).lower()
    return None


def iter_records(venues: dict, routes: dict, stats: Counter):
    """流式解析 DBLP，逐条产出符合条件的论文。"""
    # 用 gzip 流式解压；切到 DBLP 目录，使文档中相对引用的 dblp.dtd 能被找到（用于解析 &uuml; 等实体）
    os.chdir(DBLP_DIR)
    with gzip.open(DBLP_DUMP, "rb") as source:
        context = etree.iterparse(source, events=("end",), load_dtd=True, resolve_entities=True, huge_tree=True)
        yield from _filter(context, venues, routes, stats)


def resolve_venue(prefix: str, elem: etree._Element, venues: dict, routes: dict):
    """按 DBLP 键前缀定位刊物；期刊键（如 PACMPL）再按期号分到具体会议。"""
    if prefix in venues:
        return venues[prefix]
    number = elem.findtext("number") or ""
    for rule, venue in routes.get(prefix, []):
        if rule is None or rule.search(number):
            return venue
    return None


def _filter(context, venues: dict, routes: dict, stats: Counter):
    for _, elem in context:
        if elem.tag not in RECORD_TAGS:
            continue
        stats["records"] += 1
        try:
            if elem.tag not in ("article", "inproceedings") or elem.get("publtype") in ("informal", "withdrawn"):
                continue
            key = elem.get("key", "")
            venue = resolve_venue("/".join(key.split("/")[:2]), elem, venues, routes)
            if venue is None:
                continue
            year = int(elem.findtext("year") or 0)
            if year < MIN_YEAR:
                continue
            stats["candidates"] += 1

            container = elem.findtext("booktitle") or elem.findtext("journal") or ""
            if EXCLUDED_BOOKTITLE.search(container):
                stats["skip_volume"] += 1
                continue
            title_elem = elem.find("title")
            title = clean_title(title_elem) if title_elem is not None else ""
            if len(title) < 8 or EXCLUDED_TITLE.match(title):
                stats["skip_title"] += 1
                continue

            urls = [e.text.strip() for e in elem.findall("ee") if e.text]
            if is_workshop_venue(venue["short_name"], urls):
                stats["skip_volume"] += 1
                continue
            yield {
                "key": key,
                "venue": venue["short_name"],
                "year": year,
                "title": title,
                "doi": extract_doi(urls),
                "urls": urls,
            }
        finally:
            # 释放已处理节点，保证内存恒定
            elem.clear()
            while elem.getprevious() is not None:
                del elem.getparent()[0]
            if stats["records"] % 1_000_000 == 0:
                print(f"  已扫描 {stats['records'] // 1_000_000}M 条记录，命中 {stats['candidates']} 篇…", flush=True)


def main() -> None:
    venues = load_a_venues()
    routes = load_journal_routes()
    print(f"CCF-A 刊物 {len(venues)} 个，收录 {MIN_YEAR} 年起；开始解析 {DBLP_DUMP} …")
    started = time.time()
    stats: Counter = Counter()
    per_venue: Counter = Counter()
    with_doi: Counter = Counter()

    def tracked():
        for row in iter_records(venues, routes, stats):
            per_venue[row["venue"]] += 1
            with_doi[row["venue"]] += bool(row["doi"])
            yield row

    total = write_jsonl(DBLP_PAPERS, tracked())
    print(f"\n完成，用时 {time.time() - started:.0f}s：扫描 {stats['records']} 条，A 类候选 {stats['candidates']} 篇，"
          f"剔除非主会 {stats['skip_volume']}、非论文条目 {stats['skip_title']}，保留 {total} 篇")
    print(f"{'刊物':<18}{'论文数':>8}{'有DOI':>8}")
    for name, count in per_venue.most_common():
        print(f"{name:<18}{count:>8}{with_doi[name] / count:>8.0%}")
    missing = sorted({v["short_name"] for v in venues.values()} - set(per_venue))
    if missing:
        print(f"\n未命中任何论文的刊物（需检查 DBLP 键）：{', '.join(missing)}")
    print(f"已写入 {DBLP_PAPERS}")


if __name__ == "__main__":
    main()
