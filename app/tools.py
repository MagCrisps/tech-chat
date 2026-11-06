"""Agent 可调用工具：`search_papers`（CCF-A 论文库检索）与 `retrieve`（个人文档知识库检索）。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from langchain_core.tools import tool

from app.config import settings
from app.rag.store import similarity_search

logger = logging.getLogger(__name__)


def _serialize_documents(docs: list[dict[str, Any]]) -> str:
    """把检索结果序列化成给 LLM 阅读的纯文本。"""
    blocks: list[str] = []
    for idx, doc in enumerate(docs, start=1):
        name = doc.get("file_name") or Path(str(doc.get("source", "unknown"))).name
        start = doc.get("start_index", 0)
        blocks.append(f"[片段 {idx}] 来源：{name}（chunk 起始位置：{start}）\n{doc.get('page_content', '')}")
    return "\n\n".join(blocks)


@tool(response_format="content_and_artifact")
def retrieve(query: str) -> tuple[str, list[dict[str, Any]]]:
    """搜索用户个人技术知识库（技术笔记 / 博客 / PDF）。

    何时使用：当用户提问涉及技术概念、原理、实现细节、代码写法、配置方法、框架用法、
    报错排查，或任何可能出现在其技术文档里的具体知识点时，必须调用本工具检索原文后再回答，
    不要凭记忆作答。

    何时不要使用：当用户只是打招呼、寒暄、致谢、询问你是谁，或提出与知识库无关的通用
    常识 / 简单计算时，直接回答即可，不要调用本工具。

    Args:
        query: 检索用的自然语言问题或关键词，尽量保留用户原问题中的技术名词。

    Returns:
        (给模型阅读的检索文本, 命中的文档片段列表) 二元组。
        结构化的片段列表包含 file_name / source / start_index / page_content，
        用于在界面下方展示引用来源。
    """
    logger.info("retrieve 被调用，query=%s", query)
    try:
        docs = similarity_search(query, k=settings.retrieval_k)
    except Exception as exc:  # 检索失败时不要把异常抛给图，交给模型兜底回答
        logger.exception("检索异常：%s", exc)
        return "检索服务暂时不可用，请稍后再试。", []

    if not docs:
        return "知识库中未找到相关内容（向量库为空或没有匹配到片段）。", []

    payload: list[dict[str, Any]] = [
        {
            "file_name": doc.metadata.get("file_name") or Path(str(doc.metadata.get("source", ""))).name,
            "source": doc.metadata.get("source", "unknown"),
            "start_index": doc.metadata.get("start_index", 0),
            "page_content": doc.page_content,
        }
        for doc in docs
    ]
    return _serialize_documents(payload), payload


# --------------------------------------------------------------------------- #
# 论文检索工具
# --------------------------------------------------------------------------- #
# 常见别名 -> CCF 目录中的标准简称（其余按大小写不敏感精确匹配）
VENUE_ALIASES = {
    "nips": "NeurIPS", "kdd": "SIGKDD", "mm": "ACM MM", "acmmm": "ACM MM", "oakland": "S&P",
    "ieee s&p": "S&P", "sp": "S&P", "pami": "TPAMI", "usenix sec": "USENIX Security", "atc": "USENIX ATC",
    "imwut": "UbiComp/IMWUT", "ubicomp": "UbiComp/IMWUT", "vis": "IEEE VIS", "esec/fse": "FSE",
}


def _normalize(values: Optional[list[str]], known: list[str], aliases: dict[str, str]) -> tuple[list[str], list[str]]:
    """把模型给出的刊物 / 领域名规整为索引中的标准写法，返回 (有效值, 无法识别的值)。"""
    lookup = {k.lower(): k for k in known}
    valid, unknown = [], []
    for value in values or []:
        key = value.strip().lower()
        name = lookup.get(key) or lookup.get(aliases.get(key, "").lower())
        (valid if name else unknown).append(name or value)
    return valid, unknown


def _serialize_papers(papers: list[dict[str, Any]]) -> str:
    """把检索到的论文序列化成给 LLM 阅读的纯文本（编号 [P1]…，便于回答中引用）。"""
    blocks = []
    for i, p in enumerate(papers, start=1):
        abstract = p["abstract"] or ""
        if len(abstract) > 900:
            abstract = abstract[:900] + "…"
        blocks.append(
            f"[P{i}] {p['title']}\n刊物：{p['venue']} {p['year']}（CCF-A · {p['category']}）\n"
            f"摘要：{abstract or '（暂无摘要，仅能依据标题判断）'}"
        )
    return "\n\n".join(blocks)


@tool(response_format="content_and_artifact")
def search_papers(
    query: str,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    venues: Optional[list[str]] = None,
    categories: Optional[list[str]] = None,
    types: Optional[list[str]] = None,
) -> tuple[str, list[dict[str, Any]]]:
    """在 CCF-A 类期刊 / 会议论文库中检索相关论文（当前库：人工智能、软件工程、数据库/数据挖掘三个领域，2023–2025 年，约 2 万篇）。

    何时使用：用户想找某个研究方向 / 问题 / 方法的论文、了解某方向有哪些工作、
    或在上一轮结果基础上调整条件（换年份、换刊物、换角度）时调用。

    参数说明：
    - query：**必须用英文**，写成学术检索式的短语，包含核心术语及常见同义说法，
      例如用户说「用大模型做代码修复」→ "LLM-based automated program repair, code repair with large language models"。
    - year_from / year_to：年份范围（含边界），用户没提就不要填。
    - venues：刊物简称列表，如 ["CVPR", "ICCV"]、["ACL"]、["NeurIPS", "ICML"]，用户没指定就不要填。
    - categories：CCF 领域名列表，可选值：计算机体系结构/并行与分布计算/存储系统、计算机网络、网络与信息安全、
      软件工程/系统软件/程序设计语言、数据库/数据挖掘/内容检索、计算机科学理论、计算机图形学与多媒体、
      人工智能、人机交互与普适计算、交叉/综合/新兴。
    - types：["conference"] 或 ["journal"]，用户明确只要会议或期刊时才填。

    多轮对话中，用户只调整条件时，沿用上一轮的检索主题。
    """
    from app.papers.search import as_filters, get_paper_index  # 延迟导入：索引较大，用到时才加载

    logger.info("search_papers 被调用：query=%s years=%s-%s venues=%s categories=%s types=%s",
                query, year_from, year_to, venues, categories, types)
    try:
        index = get_paper_index()
    except FileNotFoundError:
        return "论文库索引尚未构建，暂时无法检索。", []

    venue_names, bad_venues = _normalize(venues, sorted(set(index.venues.tolist())), VENUE_ALIASES)
    category_names, bad_categories = _normalize(categories, sorted(set(index.categories.tolist())), {})
    type_names, _ = _normalize(types, ["journal", "conference"], {"期刊": "journal", "会议": "conference"})
    try:
        result = index.search(
            query, k=settings.paper_search_k,
            filters=as_filters(year_from, year_to, venue_names, category_names, type_names),
        )
    except Exception as exc:  # 检索失败时不要把异常抛给图，交给模型兜底回答
        logger.exception("论文检索异常：%s", exc)
        return "论文检索服务暂时不可用，请稍后再试。", []

    papers = [{"kind": "paper", **p} for p in result["results"]]
    notes = []
    if bad_venues or bad_categories:
        notes.append(f"注意：以下筛选条件不在 CCF-A 目录中，已忽略：{', '.join(bad_venues + bad_categories)}")
    if not papers:
        return "\n".join(notes + ["在当前筛选条件下没有找到相关论文。"]), []
    header = f"共返回 {len(papers)} 篇候选论文（按相关度排序，未必全部相关，请甄别）："
    return "\n".join(notes + [header, "", _serialize_papers(papers)]), papers


# 供 `llm.bind_tools([...])` / `ToolNode([...])` 使用的工具清单（函数名唯一，勿重复定义）
TOOLS = [search_papers, retrieve]
