"""LLM 工厂：通过 OpenAI 兼容接口创建模型实例（模块级单例）。"""

from __future__ import annotations

from functools import lru_cache

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

from app.config import settings


def _common_kwargs() -> dict:
    """LLM 的公共构造参数（超时、重试、采样温度等）。"""
    if not settings.openai_api_key:
        raise RuntimeError(
            "未配置 OPENAI_API_KEY，请在项目根目录创建 .env（可参考 .env.example）并填写真实 Key。"
        )
    return {
        "model": settings.llm_model,
        "model_provider": "openai",
        "base_url": settings.openai_base_url,
        "api_key": settings.openai_api_key,
        "temperature": settings.llm_temperature,
        "timeout": settings.llm_timeout,
        "max_retries": settings.llm_max_retries,
    }


@lru_cache(maxsize=1)
def get_llm() -> BaseChatModel:
    """返回全局唯一的对话模型实例。"""
    return init_chat_model(**_common_kwargs())


def get_llm_with_tools(tools: list) -> BaseChatModel:
    """返回已绑定工具、可用于「是否检索」决策的模型实例。

    Args:
        tools: 需要绑定给模型的工具列表（`@tool` 装饰的函数）。

    Returns:
        绑定工具后的 ChatModel。
    """
    return get_llm().bind_tools(tools)


def reset_llm_cache() -> None:
    """清空模型缓存（修改 .env 后调用，便于热更新）。"""
    get_llm.cache_clear()
