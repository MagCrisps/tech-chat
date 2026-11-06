"""LangGraph 图节点：工具决策节点（论文检索 / 文档检索 / 直接回答）+ 答案生成节点。"""

from __future__ import annotations

import logging
from functools import lru_cache

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langgraph.graph import MessagesState

from app.llm import get_llm, get_llm_with_tools
from app.tools import TOOLS

logger = logging.getLogger(__name__)

# 决策节点指令：告诉模型「什么时候查论文、什么时候查个人文档、什么时候直接回答」
ROUTER_PROMPT = """你是「CCF-A 论文发现助手」的前置决策模块。你有两个工具：

- `search_papers`：检索 CCF-A 类期刊 / 会议论文库（人工智能、软件工程、数据库/数据挖掘三个领域，2023–2025 年）。
  用户问到库外的领域或年份时，仍然检索，但在回答里说明当前库的范围。
- `retrieve`：检索用户自己上传的文档（笔记 / PDF）。

规则：
1. 用户想找论文、了解某个研究方向有哪些工作、寻找相关工作 / 基线方法时，**必须调用 `search_papers`**，
   不要凭记忆列举论文（容易编造）。`query` 参数必须是英文学术检索短语。
2. 用户只是调整条件（例如「只要 2023 年以后的」「只看 CVPR」「换成期刊」「更偏理论一些」）时，
   **沿用上一轮的检索主题**，带上新条件重新调用 `search_papers`。
3. 用户询问的是他自己上传的文档里的内容时，调用 `retrieve`。
4. 打招呼、寒暄、询问你是谁，或与论文 / 文档无关的通用问题，直接简短回答，不要调用工具。
5. 不要解释你正在做什么，只需给出回答或工具调用。
"""

# 生成节点指令：只依据检索结果作答，论文推荐必须可追溯
ANSWER_PROMPT = """你是「CCF-A 论文发现助手」。你会收到用户的问题，以及工具返回的结果：
论文检索结果以 [P1]、[P2]… 编号；个人文档检索结果以【片段 N】编号。

论文检索结果的回答要求：
1. 只推荐结果中真实存在的论文，**绝不编造论文、作者或结论**。
2. 先用一两句话概括结果中这个方向的主要研究思路，再挑出真正相关的论文，按子主题分组；
   与需求无关的结果直接略过，不要硬凑。
3. 每篇论文的格式：**论文标题**（刊物 年份）[Pn] —— 一句话说明它做了什么、为什么与用户需求相关。
   说明必须依据摘要；没有摘要的论文注明「仅根据标题判断」。
4. 相关论文很少时如实说明，并建议换关键词、放宽年份或刊物范围。
5. 最后给出 1～2 个可以继续细化的方向（例如「只看 2024 年以后」「只看 CVPR / ICCV」）。

个人文档检索结果的回答要求：只依据原文作答，用【片段 N】标注来源；原文没有答案就明确说「知识库中未找到相关内容」。

统一使用中文和 Markdown，简洁专业。
"""


@lru_cache(maxsize=1)
def _llm_with_tools() -> BaseChatModel:
    """返回绑定工具后的模型（单例，避免重复绑定）。"""
    return get_llm_with_tools(TOOLS)


def query_or_respond(state: MessagesState) -> dict:
    """决策节点：让模型自行判断直接回答，还是调用 search_papers / retrieve。

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
