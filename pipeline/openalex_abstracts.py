"""第 3 步：用 DOI 批量查询 OpenAlex，补全论文摘要。

输入：datasets/ccf_a/_work/dblp_papers.jsonl
输出：datasets/ccf_a/_work/openalex_abstracts.jsonl（缓存：每个 DOI 一行，abstract 为 null 表示查不到）

- 每次请求查询 50 个 DOI，按 OpenAlex 计费约 $0.0001 / 次；
- 可断点续跑：已缓存的 DOI 不会重复查询；额度用尽时自动停止，次日重跑接着来；
- 设置环境变量或 .env 中的 OPENALEX_API_KEY 可获得独立额度（免费注册：openalex.org/settings/api）。

用法：python -m pipeline.openalex_abstracts [--limit N]
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from pipeline.common import ABSTRACT_CACHE, DBLP_PAPERS, ROOT, read_jsonl

API = "https://api.openalex.org/works"
BATCH = 50
PAUSE = 0.2  # 每轮请求之间的间隔（秒）
WORKERS = 4  # 并发批次数（OpenAlex 限制约 10 次/秒）


class BudgetExhausted(Exception):
    """OpenAlex 当日额度用尽。"""


def api_key() -> str | None:
    key = os.environ.get("OPENALEX_API_KEY")
    env_file = ROOT / ".env"
    if not key and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("OPENALEX_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    return key or None


def rebuild_abstract(inverted: dict[str, list[int]] | None) -> str | None:
    """OpenAlex 以倒排索引形式提供摘要，这里还原为原文。"""
    if not inverted:
        return None
    positions = [(pos, word) for word, poses in inverted.items() for pos in poses]
    return " ".join(word for _, word in sorted(positions)) or None


def fetch_batch(dois: list[str], key: str | None) -> dict[str, str | None]:
    params = {
        "filter": "doi:" + "|".join(dois),
        "select": "doi,abstract_inverted_index",
        "per-page": str(BATCH),
    }
    if key:
        params["api_key"] = key
    url = f"{API}?{urllib.parse.urlencode(params, safe=':|/')}"

    for attempt in range(1, 6):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                data = json.load(response)
            break
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:300]
            if exc.code in (402, 429) and ("budget" in body.lower() or attempt == 5):
                raise BudgetExhausted(body) from exc
            if exc.code == 400:  # 个别 DOI 含特殊字符导致整批失败：拆开逐个查
                if len(dois) == 1:
                    return {dois[0]: None}
                result: dict[str, str | None] = {}
                for doi in dois:
                    result.update(fetch_batch([doi], key))
                return result
            time.sleep(2 ** attempt)
        except (urllib.error.URLError, TimeoutError, http.client.HTTPException, ConnectionError, json.JSONDecodeError):  # 网络抖动、响应被截断：重试
            time.sleep(2 ** attempt)
    else:
        raise RuntimeError("OpenAlex 连续请求失败")

    found = {
        (w.get("doi") or "").lower().removeprefix("https://doi.org/"): rebuild_abstract(w.get("abstract_inverted_index"))
        for w in data.get("results", [])
    }
    return {doi: found.get(doi) for doi in dois}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="最多查询多少个 DOI（0 = 全部，用于试跑）")
    parser.add_argument("--dois", type=str, default="", help="只查询该文件中列出的 DOI（每行一个）")
    args = parser.parse_args()

    cached = {row["doi"] for row in read_jsonl(ABSTRACT_CACHE)}
    if args.dois:
        wanted = {line.strip() for line in open(args.dois, encoding="utf-8") if line.strip()}
    else:
        wanted = {p["doi"] for p in read_jsonl(DBLP_PAPERS) if p["doi"]}
    todo = sorted(wanted - cached)
    if args.limit:
        todo = todo[: args.limit]
    key = api_key()
    print(f"已缓存 {len(cached)} 个 DOI，待查询 {len(todo)} 个（{'使用 API key' if key else '未设置 API key，使用共享免费额度'}）")

    ABSTRACT_CACHE.parent.mkdir(parents=True, exist_ok=True)
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    done = hits = 0
    started = time.time()
    exhausted: BudgetExhausted | None = None
    with ABSTRACT_CACHE.open("a", encoding="utf-8") as fh, ThreadPoolExecutor(WORKERS) as pool:
        # 每轮并发 WORKERS 个批次；结果统一在主线程写入，额度用尽时停止提交新批次
        for round_start in range(0, len(batches), WORKERS):
            futures = [pool.submit(fetch_batch, b, key) for b in batches[round_start:round_start + WORKERS]]
            for future in futures:
                try:
                    result = future.result()
                except BudgetExhausted as exc:
                    exhausted = exc
                    continue
                for doi, abstract in result.items():
                    fh.write(json.dumps({"doi": doi, "abstract": abstract}, ensure_ascii=False) + "\n")
                    hits += abstract is not None
                done += len(result)
            fh.flush()
            if exhausted:
                print(f"\nOpenAlex 今日额度已用尽，已保存进度。明天重跑本命令会从断点继续。\n（{exhausted}）")
                break
            if (round_start // WORKERS) % 10 == 0:
                rate = done / max(time.time() - started, 1e-6)
                print(f"  {done}/{len(todo)}，摘要命中 {hits / max(done, 1):.0%}，约 {rate:.0f} 篇/秒", flush=True)
            time.sleep(PAUSE)
    print(f"本次查询 {done} 个 DOI，摘要命中 {hits} 个；缓存文件：{ABSTRACT_CACHE}")


if __name__ == "__main__":
    main()
