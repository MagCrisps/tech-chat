"""向量库单例：Embedding、Chroma 持久化、建库与增量入库。"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Optional

import chromadb
from chromadb.config import Settings as ChromaSettings
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStoreRetriever
from langchain_huggingface import HuggingFaceEmbeddings

from app.config import settings
from app.rag.loader import is_supported, load_file
from app.rag.splitter import split_documents

logger = logging.getLogger(__name__)

_lock = threading.RLock()  # 可重入锁：add_file 会在持锁状态下再次获取向量库单例
_embeddings: Optional[Embeddings] = None
_store: Optional[Chroma] = None


# --------------------------------------------------------------------------- #
# Embedding
# --------------------------------------------------------------------------- #
def _resolve_device() -> str:
    """解析 embedding 运行设备，`auto` 时自动判断 cuda 是否可用。"""
    if settings.embedding_device != "auto":
        return settings.embedding_device
    try:
        import torch  # 延迟导入，避免无 torch 环境直接报错

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:  # pragma: no cover - 取决于运行环境
        return "cpu"


def get_embeddings() -> Embeddings:
    """返回全局唯一的 Embedding 实例。

    通过 `EMBEDDING_USE_LOCAL` 切换模型来源：
    - true：只从本地目录 `EMBEDDING_LOCAL_PATH` 加载，`local_files_only=True`，绝不联网下载；
    - false：按 `EMBEDDING_MODEL_PATH`（HF 模型名）下载/加载。
    """
    global _embeddings
    if _embeddings is None:
        with _lock:
            if _embeddings is None:
                device = _resolve_device()
                model_kwargs: dict[str, Any] = {"device": device}

                if settings.embedding_use_local:
                    if not settings.embedding_local_path:
                        raise ValueError(
                            "EMBEDDING_USE_LOCAL=true 但未配置 EMBEDDING_LOCAL_PATH，请填写本地模型目录。"
                        )
                    local_dir = Path(settings.embedding_local_path).expanduser()
                    if not local_dir.exists():
                        raise FileNotFoundError(
                            f"本地 Embedding 模型目录不存在：{local_dir}（请检查 EMBEDDING_LOCAL_PATH）"
                        )
                    model_name = str(local_dir)
                    model_kwargs["local_files_only"] = True  # 只读本地，禁止联网下载
                    logger.info("加载本地 Embedding 模型：%s（device=%s）", model_name, device)
                else:
                    model_name = settings.embedding_model_path
                    logger.info("加载 Embedding 模型：%s（device=%s）", model_name, device)

                _embeddings = HuggingFaceEmbeddings(
                    model_name=model_name,
                    model_kwargs=model_kwargs,
                    encode_kwargs={"normalize_embeddings": settings.embedding_normalize},
                )
    return _embeddings


# --------------------------------------------------------------------------- #
# 向量库
# --------------------------------------------------------------------------- #
def get_vector_store() -> Chroma:
    """返回全局唯一的 Chroma 实例（数据持久化到配置目录）。"""
    global _store
    if _store is None:
        with _lock:
            if _store is None:
                settings.chroma_persist_path.mkdir(parents=True, exist_ok=True)
                _store = Chroma(
                    collection_name=settings.chroma_collection,
                    embedding_function=get_embeddings(),
                    persist_directory=str(settings.chroma_persist_path),
                    client_settings=ChromaSettings(anonymized_telemetry=False, allow_reset=True),
                )
    return _store


def get_retriever(k: int | None = None) -> VectorStoreRetriever:
    """返回相似度检索器。"""
    return get_vector_store().as_retriever(
        search_type="similarity",
        search_kwargs={"k": k or settings.retrieval_k},
    )


def count_documents() -> int:
    """返回当前集合中的向量条数。"""
    try:
        return int(get_vector_store()._collection.count())
    except Exception as exc:  # pragma: no cover
        logger.warning("统计向量条数失败：%s", exc)
        return 0


def similarity_search(query: str, k: int | None = None) -> list[Document]:
    """按查询语句检索最相关的文档块。"""
    try:
        return get_vector_store().similarity_search(query, k=k or settings.retrieval_k)
    except Exception as exc:
        logger.warning("检索失败：%s", exc)
        return []


# --------------------------------------------------------------------------- #
# 入库登记表（用于增量建库 / 跳过未变化文件）
# --------------------------------------------------------------------------- #
def _load_registry() -> dict[str, Any]:
    """读取已入库文件登记表。"""
    path = settings.indexed_registry_path
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover
        logger.warning("读取入库登记表失败，将按全量处理：%s", exc)
        return {}


def _save_registry(registry: dict[str, Any]) -> None:
    """写回已入库文件登记表。"""
    settings.indexed_registry_path.parent.mkdir(parents=True, exist_ok=True)
    settings.indexed_registry_path.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _signature(path: Path) -> dict[str, Any]:
    """文件指纹：用大小 + 修改时间判断内容是否变化。"""
    stat = path.stat()
    return {"size": stat.st_size, "mtime": round(stat.st_mtime, 3)}


def _delete_by_source(source: str) -> None:
    """删除某个来源已入库的全部向量（重新索引前调用，避免重复）。

    `langchain_chroma` 的 `delete` 只接受向量 ID，因此先用 `get(where=...)` 查出 ID。
    """
    try:
        store = get_vector_store()
        existing = store.get(where={"source": source})
        ids = existing.get("ids") or []
        if ids:
            store.delete(ids=ids)
            logger.info("已清理 %s 的 %d 条旧向量", Path(source).name, len(ids))
    except Exception as exc:  # pragma: no cover
        logger.warning("清理旧向量失败（忽略）：%s", exc)


# --------------------------------------------------------------------------- #
# 建库 / 增量入库
# --------------------------------------------------------------------------- #
def add_file(path: Path, reindex: bool = True) -> dict[str, Any]:
    """把单个文件加载、切分并写入向量库。

    Args:
        path: 本地文件路径。
        reindex: 为 True 时先删除该文件已入库的旧向量，保证不重复。

    Returns:
        形如 `{"source": ..., "file_name": ..., "chunks": n}` 的结果字典。
    """
    resolved = Path(path).resolve()
    documents = load_file(resolved)
    chunks = split_documents(documents)
    source = str(resolved)

    if not chunks:
        logger.warning("文件内容为空，未入库：%s", resolved)
        return {"source": source, "file_name": resolved.name, "chunks": 0}

    with _lock:
        if reindex:
            _delete_by_source(source)
        get_vector_store().add_documents(chunks)
        registry = _load_registry()
        registry[source] = {"signature": _signature(resolved), "chunks": len(chunks), "file_name": resolved.name}
        _save_registry(registry)

    logger.info("已入库 %s（%d 个 chunk）", resolved.name, len(chunks))
    return {"source": source, "file_name": resolved.name, "chunks": len(chunks)}


def build_index(force: bool = False) -> dict[str, Any]:
    """启动时扫描 `data/` 目录并增量建库。

    - 集合中已存在且文件指纹未变化的文件会被跳过（避免重复 embedding）。
    - `force=True` 时清空集合后重建。

    Returns:
        建库摘要：{"indexed": [...], "skipped": [...], "failed": [...], "total_vectors": n}
    """
    data_dir = settings.data_dir_path
    data_dir.mkdir(parents=True, exist_ok=True)

    store = get_vector_store()
    if force:
        with _lock:
            store.reset_collection()
            _save_registry({})
        logger.info("已清空向量集合，开始全量重建")

    registry = _load_registry()
    indexed: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []

    for path in sorted(data_dir.glob("**/*")):
        if not path.is_file() or not is_supported(path):
            continue
        resolved = path.resolve()
        key = str(resolved)
        if not force and registry.get(key, {}).get("signature") == _signature(resolved):
            skipped.append(resolved.name)
            continue
        try:
            add_file(resolved)
            indexed.append(resolved.name)
        except Exception as exc:
            logger.error("入库失败 %s：%s", resolved.name, exc)
            failed.append(resolved.name)

    summary = {
        "indexed": indexed,
        "skipped": skipped,
        "failed": failed,
        "total_vectors": count_documents(),
    }
    logger.info(
        "建库完成：新增/更新 %d 个文件，跳过 %d 个，失败 %d 个，当前向量总数 %d",
        len(indexed),
        len(skipped),
        len(failed),
        summary["total_vectors"],
    )
    return summary


def reset_store_cache() -> None:
    """清空模块级单例缓存（测试或切换配置时使用）。"""
    global _store, _embeddings
    with _lock:
        _store = None
        _embeddings = None
