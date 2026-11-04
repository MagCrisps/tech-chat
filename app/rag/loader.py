"""文档加载器：按文件后缀把不同来源统一转换成 `Document` 列表。"""

from __future__ import annotations

import logging
from pathlib import Path

from langchain_community.document_loaders import PyPDFLoader, TextLoader, WebBaseLoader
from langchain_core.documents import Document

logger = logging.getLogger(__name__)

# 支持的本地文件后缀 -> 加载器
TEXT_SUFFIXES: set[str] = {".md", ".markdown", ".txt", ".text", ".rst", ".json", ".yaml", ".yml", ".log"}
HTML_SUFFIXES: set[str] = {".html", ".htm"}
PDF_SUFFIXES: set[str] = {".pdf"}

SUPPORTED_SUFFIXES: set[str] = TEXT_SUFFIXES | HTML_SUFFIXES | PDF_SUFFIXES


def is_supported(path: Path) -> bool:
    """判断文件后缀是否受支持。"""
    return path.suffix.lower() in SUPPORTED_SUFFIXES


def load_file(path: Path) -> list[Document]:
    """按后缀分发生成加载器，把单个本地文件读成 `Document` 列表。

    Args:
        path: 本地文件路径。

    Returns:
        Document 列表（元数据包含 `source` 绝对路径）。

    Raises:
        ValueError: 文件后缀不受支持。
    """
    resolved = Path(path).resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"文件不存在：{resolved}")

    suffix = resolved.suffix.lower()
    source = str(resolved)

    if suffix in PDF_SUFFIXES:
        loader = PyPDFLoader(source)
    elif suffix in HTML_SUFFIXES:
        # WebBaseLoader 支持传入本地文件路径（file:// 形式），内部用 BeautifulSoup 抽取正文
        loader = WebBaseLoader(resolved.as_uri())
    elif suffix in TEXT_SUFFIXES:
        loader = TextLoader(source, encoding="utf-8")
    else:
        raise ValueError(f"不支持的文件类型：{suffix}（支持 {sorted(SUPPORTED_SUFFIXES)}）")

    docs = loader.load()
    for doc in docs:
        # 统一元数据，便于切分和来源展示
        doc.metadata["source"] = source
        doc.metadata["file_name"] = resolved.name
        doc.metadata["file_type"] = suffix.lstrip(".")
    logger.info("已加载文件 %s，共 %d 个文档块", resolved.name, len(docs))
    return docs


def load_from_url(url: str) -> list[Document]:
    """用 `WebBaseLoader` 抓取网页正文。

    Args:
        url: 网页地址。

    Returns:
        Document 列表（元数据包含 `source` = url）。
    """
    docs = WebBaseLoader(url).load()
    for doc in docs:
        doc.metadata.setdefault("source", url)
        doc.metadata["file_name"] = url
        doc.metadata["file_type"] = "web"
    return docs


def load_directory(directory: Path, recursive: bool = True) -> list[Document]:
    """扫描目录下所有受支持的文件并加载。

    Args:
        directory: 待扫描目录。
        recursive: 是否递归子目录。

    Returns:
        所有文件合并后的 Document 列表。
    """
    directory = Path(directory)
    if not directory.exists():
        logger.warning("数据目录不存在，已跳过：%s", directory)
        return []

    pattern = "**/*" if recursive else "*"
    documents: list[Document] = []
    for path in sorted(directory.glob(pattern)):
        if not path.is_file() or not is_supported(path):
            continue
        try:
            documents.extend(load_file(path))
        except Exception as exc:  # 单个文件失败不应中断整库构建
            logger.warning("加载失败，已跳过 %s：%s", path, exc)
    return documents
