"""Agent 可调用工具：`retrieve`（唯一的检索工具，函数名全局唯一）。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

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


# 供 `llm.bind_tools([...])` / `ToolNode([...])` 使用的工具清单（函数名唯一，勿重复定义）
TOOLS = [retrieve]
