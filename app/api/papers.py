"""论文检索接口：GET /api/papers/search、GET /api/papers/facets。"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from app.papers.search import as_filters, get_paper_index

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/papers", tags=["papers"])


@router.get("/search")
def search_papers(
    q: str = Query(..., min_length=1, max_length=500, description="研究兴趣 / 检索词（英文效果最好）"),
    k: int = Query(10, ge=1, le=50),
    year_from: Optional[int] = Query(None, ge=1900, le=2100),
    year_to: Optional[int] = Query(None, ge=1900, le=2100),
    venue: Optional[list[str]] = Query(None, description="刊物简称，可重复，如 venue=CVPR&venue=ICCV"),
    category: Optional[list[str]] = Query(None, description="CCF 领域名，可重复"),
    type: Optional[list[str]] = Query(None, description="journal / conference"),
) -> dict[str, Any]:
    """混合检索 CCF-A 论文（同步函数，FastAPI 会放到线程池执行，不阻塞事件循环）。"""
    try:
        index = get_paper_index()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    result = index.search(q, k=k, filters=as_filters(year_from, year_to, venue, category, type))
    return {"query": q, **result}


@router.get("/facets")
def facets() -> dict[str, Any]:
    """筛选面板元数据：总数、年份范围、领域与刊物。"""
    try:
        return get_paper_index().facets()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
