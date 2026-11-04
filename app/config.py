"""集中式配置：所有可调参数都从 .env 读取。"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（tech-chat/），用于把相对路径解析成绝对路径，避免受启动时 cwd 影响
BASE_DIR: Path = Path(__file__).resolve().parent.parent

# HuggingFace 国内镜像地址：国内直连 huggingface.co 会超时，导致首次下载 embedding
# 模型失败。默认走 hf-mirror.com；如需改回官方源，设置环境变量 HF_ENDPOINT 即可。
DEFAULT_HF_ENDPOINT: str = "https://hf-mirror.com"


class Settings(BaseSettings):
    """运行时配置。

    所有字段都可以通过环境变量或 `.env` 文件覆盖，字段名大小写不敏感。
    """

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------- LLM ----------
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.0
    llm_timeout: int = 60
    llm_max_retries: int = 2

    # ---------- Embedding ----------
    # 是否使用本地已有的 embedding 模型：true 时完全不联网（适合内网/无外网环境），
    # 只从 embedding_local_path 加载；false 时按 embedding_model_path 从 HF 下载。
    embedding_use_local: bool = False
    # 本地模型目录（仅当 embedding_use_local=true 时生效）
    embedding_local_path: str = ""
    # 远程模型名（仅当 embedding_use_local=false 时生效）
    embedding_model_path: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_device: str = "auto"  # auto | cpu | cuda
    embedding_normalize: bool = True
    hf_endpoint: str = DEFAULT_HF_ENDPOINT  # HuggingFace 下载源（国内建议用镜像）

    # ---------- 向量库 ----------
    chroma_persist_dir: str = "./chroma_db"
    chroma_collection: str = "tech_knowledge"

    # ---------- RAG ----------
    data_dir: str = "./data"
    chunk_size: int = 1000
    chunk_overlap: int = 200
    retrieval_k: int = 4

    # ---------- 服务 ----------
    host: str = "127.0.0.1"
    port: int = 8000
    open_browser: bool = True

    # ---------- 派生路径（只读属性）----------
    @property
    def data_dir_path(self) -> Path:
        """`data/` 目录的绝对路径。"""
        return self._resolve(self.data_dir)

    @property
    def chroma_persist_path(self) -> Path:
        """Chroma 持久化目录的绝对路径。"""
        return self._resolve(self.chroma_persist_dir)

    @property
    def indexed_registry_path(self) -> Path:
        """已入库文件清单（用于增量建库时跳过未变化的文件）。"""
        return self.chroma_persist_path / "indexed_files.json"

    @property
    def static_dir(self) -> Path:
        """前端静态资源目录。"""
        return BASE_DIR / "frontend"

    @staticmethod
    def _resolve(raw: str) -> Path:
        """把配置里的相对路径按项目根目录展开为绝对路径。"""
        path = Path(raw)
        return path if path.is_absolute() else (BASE_DIR / path).resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回全局唯一的 Settings 实例（懒加载）。"""
    return Settings()


settings: Settings = get_settings()

# 必须在导入 huggingface_hub / sentence-transformers 之前写入镜像地址，
# 因此这里放在模块顶层（config 总是最先被导入）。setdefault 不覆盖用户已设的值。
os.environ.setdefault("HF_ENDPOINT", settings.hf_endpoint)
