"""文本切分：把长文档切成带位置信息的小块，便于检索与来源定位。"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_splitter() -> RecursiveCharacterTextSplitter:
    """返回全局唯一的切分器（chunk_size / chunk_overlap 来自配置）。"""
    return RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        add_start_index=True,  # 记录每个 chunk 在原文中的起始偏移，用于来源展示
        separators=["\n\n", "\n", "。", "！", "？", "；", ". ", " ", ""],
    )


def split_documents(documents: list[Document]) -> list[Document]:
    """切分文档并补齐元数据。

    Args:
        documents: 原始 Document 列表。

    Returns:
        切分后的 Document 列表，每个元素的 metadata 含
        `source` / `file_name` / `start_index`。
    """
    if not documents:
        return []

    chunks = get_splitter().split_documents(documents)
    for chunk in chunks:
        source = str(chunk.metadata.get("source", "unknown"))
        chunk.metadata["source"] = source
        chunk.metadata.setdefault("file_name", Path(source).name)
        chunk.metadata.setdefault("start_index", 0)

    logger.info("切分完成：%d 个文档 -> %d 个 chunk", len(documents), len(chunks))
    return chunks
