"""第 3b 步：从官方论文集网站补摘要（针对没有 DOI、或 OpenAlex 查不到摘要的论文）。

支持站点：NeurIPS、PMLR（ICML）、CVF（CVPR/ICCV）、JMLR、USENIX（Security/OSDI/NSDI/ATC/FAST）、
IJCAI、NDSS、Springer（按 DOI 拼文章页）；其余站点尝试通用的 <meta name="citation_abstract">。

输入：_work/dblp_papers.jsonl（+ _work/openalex_abstracts.jsonl，用于跳过已有摘要的论文）
输出：_work/site_abstracts.jsonl（缓存：每篇论文一行 {key, url, abstract}，可断点续跑）

- 用 curl 抓取（部分站点会拦截 Python 默认请求）；
- 每个站点独立线程池（4 并发、请求间隔 0.3s），不同站点之间互不阻塞。

用法：python -m pipeline.site_abstracts [--limit N]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from pipeline.common import ABSTRACT_CACHE, DBLP_PAPERS, WORK_DIR, read_jsonl

SITE_CACHE = WORK_DIR / "site_abstracts.jsonl"
PER_HOST = 4
PAUSE = 0.3
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def _text(node) -> str | None:
    if node is None:
        return None
    text = " ".join(node.get_text(" ", strip=True).split())
    return re.sub(r"^abstract[:.\s]*", "", text, flags=re.IGNORECASE) or None


def _longest_p(node) -> str | None:
    if node is None:
        return None
    paragraphs = [" ".join(p.get_text(" ", strip=True).split()) for p in node.find_all("p")]
    return max(paragraphs, key=len, default=None)


def _neurips(soup: BeautifulSoup) -> str | None:
    """新版页面为 p.paper-abstract；旧版（2020-2021、Datasets & Benchmarks）为 <h4>Abstract</h4> 后接 <p>。"""
    node = soup.select_one(".paper-abstract")
    if node is None:
        heading = soup.find(["h2", "h3", "h4"], string=re.compile(r"^\s*Abstract\s*$", re.IGNORECASE))
        node = heading.find_next("p") if heading else None
    return _text(node)


# 站点 -> 提取函数（入参为 BeautifulSoup）
EXTRACTORS = {
    "proceedings.neurips.cc": _neurips,
    "datasets-benchmarks-proceedings.neurips.cc": _neurips,
    "proceedings.mlr.press": lambda s: _text(s.select_one("#abstract")),
    "openaccess.thecvf.com": lambda s: _text(s.select_one("#abstract")),
    "jmlr.org": lambda s: _text(s.select_one(".abstract")),
    "www.usenix.org": lambda s: _text(s.select_one(".field-name-field-paper-description")),
    "www.ijcai.org": lambda s: _text(s.select_one(".article__abstract")),
    "www.ndss-symposium.org": lambda s: _longest_p(s.select_one(".paper-data")),
    # Springer（IJCV、SCIS、CRYPTO 等）：OpenAlex 拿不到摘要，但文章页公开展示
    "link.springer.com": lambda s: _meta(s, "dc.description") or _text(s.select_one("#Abs1-content")),
}


def _meta(soup: BeautifulSoup, name: str) -> str | None:
    meta = soup.find("meta", attrs={"name": name})
    return " ".join(meta["content"].split()) if meta and meta.get("content") else None


def generic(soup: BeautifulSoup) -> str | None:
    meta = soup.find("meta", attrs={"name": "citation_abstract"})
    return " ".join(meta["content"].split()) if meta and meta.get("content") else None


def normalize(url: str) -> str:
    # papers.nips.cc 的跳转会产生 "//paper_files" 导致 404，直接改写到新域名
    url = re.sub(r"^https?://papers\.nips\.cc/", "https://proceedings.neurips.cc/", url)
    return re.sub(r"^http://", "https://", url)


def pick_url(urls: list[str], doi: str | None = None) -> str | None:
    """优先选择有专用提取规则的站点链接；PDF 链接无法解析，跳过。
    Springer 的 DOI 直接拼成文章页地址（经 doi.org 跳转需要 Cookie，curl 会失败）。"""
    candidates = [normalize(u) for u in urls if not u.lower().endswith(".pdf")]
    if doi and doi.startswith("10.1007/"):
        candidates.append(f"https://link.springer.com/{doi}")
    for url in candidates:
        if urlparse(url).netloc in EXTRACTORS:
            return url
    return None


def fetch_abstract(url: str) -> str | None:
    result = subprocess.run(
        ["curl", "-s", "-L", "-m", "40", "--retry", "2", "-A", USER_AGENT, url],
        capture_output=True, check=False,
    )
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError(f"curl 失败（{result.returncode}）")
    soup = BeautifulSoup(result.stdout.decode("utf-8", errors="replace"), "html.parser")
    abstract = EXTRACTORS[urlparse(url).netloc](soup) or generic(soup)
    return abstract if abstract and len(abstract) >= 50 else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="最多抓取多少篇（0 = 全部，用于试跑）")
    args = parser.parse_args()

    queried = {row["doi"]: bool(row.get("abstract")) for row in read_jsonl(ABSTRACT_CACHE)}
    done = {row["key"] for row in read_jsonl(SITE_CACHE)}
    tasks: dict[str, list[tuple[str, str]]] = defaultdict(list)  # host -> [(key, url)]
    for paper in read_jsonl(DBLP_PAPERS):
        # 有 DOI 的论文先交给 OpenAlex；OpenAlex 查过且没有摘要的，才来官方站点补
        if paper["key"] in done or (paper["doi"] and queried.get(paper["doi"], True)):
            continue
        url = pick_url(paper["urls"], paper["doi"])
        if url:
            tasks[urlparse(url).netloc].append((paper["key"], url))
    if args.limit:
        tasks = {h: t[: max(1, args.limit // len(tasks))] for h, t in tasks.items()}
    total = sum(len(t) for t in tasks.values())
    print(f"已缓存 {len(done)} 篇；待抓取 {total} 篇：" + "，".join(f"{h} {len(t)}" for h, t in tasks.items()))

    write_lock = threading.Lock()
    stats: Counter = Counter()
    started = time.time()

    def work(key: str, url: str) -> None:
        try:
            abstract = fetch_abstract(url)
        except Exception:  # noqa: BLE001 - 网络错误不写缓存，下次重跑再试
            abstract, failed = None, True
        else:
            failed = False
        finally:
            time.sleep(PAUSE)
        with write_lock:
            if failed:
                stats["error"] += 1
            else:
                fh.write(json.dumps({"key": key, "url": url, "abstract": abstract}, ensure_ascii=False) + "\n")
                stats["ok" if abstract else "empty"] += 1
            finished = stats["ok"] + stats["empty"] + stats["error"]
            if finished % 100 == 0:
                fh.flush()
            if finished % 1000 == 0:
                rate = finished / (time.time() - started)
                print(f"  {finished}/{total}：成功 {stats['ok']}，无摘要 {stats['empty']}，出错 {stats['error']}，"
                      f"约 {rate:.1f} 篇/秒", flush=True)

    SITE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    # 每个站点一个独立线程池：慢站点只占用自己的线程，不会拖住其他站点
    pools = {host: ThreadPoolExecutor(PER_HOST, thread_name_prefix=host) for host in tasks}
    with SITE_CACHE.open("a", encoding="utf-8") as fh:
        futures = [pools[host].submit(work, key, url) for host, items in tasks.items() for key, url in items]
        for _ in as_completed(futures):
            pass
    for pool in pools.values():
        pool.shutdown()
    print(f"完成：成功 {stats['ok']}，无摘要 {stats['empty']}，出错 {stats['error']}（出错的下次重跑会重试）")


if __name__ == "__main__":
    main()
