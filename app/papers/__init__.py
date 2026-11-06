"""论文检索子包：基于 storage/paper_index 的混合检索（BM25 + 语义向量）。"""

from app.papers.search import PaperIndex, get_paper_index

__all__ = ["PaperIndex", "get_paper_index"]
