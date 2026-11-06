"""第 1 步：抓取 CCF 官方推荐目录，生成结构化的期刊 / 会议列表。

数据来源：https://www.ccf.org.cn/Academic_Evaluation/By_category/ 下的 10 个领域页面。
每个页面按「期刊 / 会议」×「A / B / C 类」分节，每行包含简称、全称、出版社和 DBLP 地址。

输出：datasets/ccf/venues.json（全部 A/B/C 类，后续步骤按 tier 过滤）
     datasets/ccf/raw/*.html（官网原始页面缓存；删除后重跑即重新抓取）

用法：python -m pipeline.ccf_venues
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

BASE_URL = "https://www.ccf.org.cn/Academic_Evaluation/"
CATEGORIES = [
    "ARCH_DCP_SS", "CN", "NIS", "TCSE_SS_PDL", "DM_CS",
    "TCS", "CGAndMT", "AI", "HCIAndPC", "Cross_Compre_Emerging",
]
DATASET_DIR = Path(__file__).resolve().parent.parent / "datasets" / "ccf"
RAW_DIR = DATASET_DIR / "raw"  # 官网原始页面缓存，保证可复现
OUTPUT = DATASET_DIR / "venues.json"
DBLP_KEY = re.compile(r"/db/((?:conf|journals)/[^/]+)")

# 官网数据中的已知错误（按 DBLP 键或简称定位），解析后统一修正
CORRECTIONS = {
    ("short_name", "HPCA"): {"dblp_key": "conf/hpca"},   # 官网链接误指向 conf/cnhpca（另一个会议）
    ("dblp_key", "journals/joc"): {"short_name": "JoC"},  # 官网 Journal of Cryptology 简称为空
}


def apply_corrections(venue: dict[str, Any]) -> dict[str, Any]:
    for (field, value), patch in CORRECTIONS.items():
        if venue.get(field) == value:
            venue.update(patch)
    return venue


def fetch(slug: str, retries: int = 3) -> str:
    """获取领域页面：优先读本地缓存，否则用 curl 下载并缓存。

    说明：CCF 官网的 WAF 会拦截 Python urllib/requests（返回 JS 校验页），curl 可正常访问；
    若 curl 也被拦截，可在浏览器中「另存为」到 datasets/ccf/raw/{slug}.html 后重跑。
    """
    cache = RAW_DIR / f"{slug}.html"
    if cache.exists():
        return cache.read_text(encoding="utf-8", errors="replace")

    url = f"{BASE_URL}{slug}/"
    for attempt in range(1, retries + 1):
        result = subprocess.run(
            ["curl", "-s", "-m", "30", "-A", "Mozilla/5.0", url],
            capture_output=True, check=False,
        )
        html = result.stdout.decode("utf-8", errors="replace")
        if result.returncode == 0 and "dblp" in html:
            RAW_DIR.mkdir(parents=True, exist_ok=True)
            cache.write_text(html, encoding="utf-8")
            time.sleep(1)  # 对官网友好一点
            return html
        print(f"  第 {attempt} 次获取 {url} 失败（curl 退出码 {result.returncode}，可能被 WAF 拦截）", file=sys.stderr)
        time.sleep(2 * attempt)
    raise RuntimeError(f"无法获取 {url}；请用浏览器另存为 {cache} 后重跑")


def parse_category(slug: str, html: str) -> list[dict[str, Any]]:
    """按文档顺序扫描标题与表格行，解析出该领域的全部期刊 / 会议。"""
    soup = BeautifulSoup(html, "html.parser")
    venues: list[dict[str, Any]] = []
    category_name = slug
    kind: str | None = None
    tier: str | None = None

    for node in soup.find_all(["h4", "h3", "li"]):
        text = node.get_text(" ", strip=True)
        if node.name == "h4":
            if "刊物" in text or "期刊" in text:  # 官网标题为「推荐国际学术刊物」
                kind = "journal"
            elif "会议" in text:
                kind = "conference"
            match = re.search(r"[（(]\s*[●·]?\s*(.+?)\s*[)）]", text)
            if match:
                category_name = match.group(1)
            continue
        if node.name == "h3":
            match = re.match(r"\s*([ABC])\s*类", text)
            if match:
                tier = match.group(1)
            continue

        cells = node.find_all("div", recursive=False)
        if not (kind and tier) or len(cells) < 5 or not cells[0].get_text(strip=True).isdigit():
            continue  # 表头或无关列表
        link = cells[4].find("a")
        dblp_url = (link.get("href") if link else cells[4].get_text(strip=True)).strip()
        key = DBLP_KEY.search(dblp_url)
        venues.append({
            "category": slug,
            "category_name": category_name,
            "type": kind,
            "tier": tier,
            "short_name": cells[1].get_text(" ", strip=True),
            "full_name": cells[2].get_text(" ", strip=True),
            "publisher": cells[3].get_text(" ", strip=True),
            "dblp_url": dblp_url,
            "dblp_key": key.group(1) if key else None,
        })
    return venues


def main() -> None:
    all_venues: list[dict[str, Any]] = []
    for slug in CATEGORIES:
        venues = parse_category(slug, fetch(slug))
        name = venues[0]["category_name"] if venues else slug
        print(f"{slug:<22} {name:<24} 共 {len(venues):>3} 个，A 类 {sum(v['tier'] == 'A' for v in venues):>2} 个")
        all_venues.extend(apply_corrections(v) for v in venues)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(all_venues, ensure_ascii=False, indent=2), encoding="utf-8")

    a_class = [v for v in all_venues if v["tier"] == "A"]
    by_type = Counter(v["type"] for v in a_class)
    missing = [v["short_name"] for v in a_class if not v["dblp_key"]]
    print(f"\n合计 {len(all_venues)} 个；A 类 {len(a_class)} 个（期刊 {by_type['journal']}，会议 {by_type['conference']}）")
    if missing:
        print(f"A 类中缺少 DBLP 键的：{', '.join(missing)}")
    print(f"已写入 {OUTPUT}")


if __name__ == "__main__":
    main()
