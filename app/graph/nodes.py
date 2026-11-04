"""LangGraph 图节点：检索决策节点 + 答案生成节点。"""

from __future__ import annotations

import logging
from functools import lru_cache

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langgraph.graph import MessagesState

from app.llm import get_llm, get_llm_with_tools
from app.tools import TOOLS

logger = logging.getLogger(__name__)

# 决策节点指令：告诉模型「什么时候该检索、什么时候直接回答」
ROUTER_PROMPT = """你是一个个人技术知识库助手的前置决策模块。

你可以使用 `retrieve` 工具检索用户自己的技术笔记 / 博客 / PDF。

规则：
1. 当用户的问题涉及技术概念、原理、实现细节、代码写法、配置方法、框架用法、报错排查，
   或任何可能出现在其技术文档里的具体知识点时，**必须调用 `retrieve` 工具**，不要凭自己的记忆回答。
2. 当用户只是打招呼、寒暄、道谢、询问你是谁，或提出与知识库无关的通用常识问题时，
   **直接给出一句简短自然的回答，不要调用任何工具**。
3. 禁止编造知识库内容，也不要解释你正在做什么，只需给出回答或工具调用。
"""

# 生成节点指令：强制基于检索到的原文作答，并标注来源
ANSWER_PROMPT = """你是用户的个人技术知识库助手。你会收到用户的问题，以及（可能存在的）知识库检索结果片段。

回答要求：
1. 只依据检索到的原文内容作答，可以做归纳、整合和举例，但**不得编造原文中不存在的信息**。
2. 如果检索结果为空、或明显不包含答案，必须明确回答「知识库中未找到相关内容」，
   并简要说明还需要补充哪类文档，不要强行猜测。
3. 引用原文时使用 `【片段 N】` 标注对应来源，便于用户核对。
4. 使用 Markdown 组织答案（小标题 / 列表 / 代码块），保持简洁、专业。
5. 如果检索结果给出了多个片段，注意交叉验证，不要只依赖第一条。
"""


@lru_cache(maxsize=1)
def _llm_with_tools() -> BaseChatModel:
    """返回绑定工具后的模型（单例，避免重复绑定）。"""
    return get_llm_with_tools(TOOLS)


def query_or_respond(state: MessagesState) -> dict:
    """决策节点：让模型自行判断「直接回答」还是「调用 retrieve 检索」。

    Args:
        state: 当前对话状态（含完整历史消息）。

    Returns:
        `{"messages": [AIMessage]}`；若 AIMessage 带有 tool_calls，
        `tools_condition` 会把它路由到 tools 节点。
    """
    messages = [SystemMessage(content=ROUTER_PROMPT), *state["messages"]]
    response = _llm_with_tools().invoke(messages)
    return {"messages": [response]}


def generate(state: MessagesState) -> dict:
    """生成节点：基于检索到的原文片段生成最终答案。

    Args:
        state: 当前对话状态（此时 messages 中已包含 ToolMessage 检索结果）。

    Returns:
        `{"messages": [AIMessage]}` 作为最终回答。
    """
    messages = [SystemMessage(content=ANSWER_PROMPT), *state["messages"]]
    response = get_llm().invoke(messages)
    return {"messages": [response]}
