"""构建并编译 LangGraph 状态图（`build_graph()` 是唯一对外入口）。"""

from __future__ import annotations

import logging
from typing import Any, Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from app.graph.nodes import generate, query_or_respond
from app.tools import TOOLS

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# 图定义：只在模块导入时创建一次，节点名唯一，避免重复 add_node。
# 这里统一使用 MessagesState，不混用自定义 State。
# --------------------------------------------------------------------------- #
graph_builder: StateGraph = StateGraph(MessagesState)

graph_builder.add_node("query_or_respond", query_or_respond)
# 说明：langgraph 1.x 的 ToolNode 已移除 response_format 参数，
# 返回格式由工具自身声明（retrieve 用的是 content_and_artifact），
# 因此这里不要传 response_format，否则会 TypeError。
graph_builder.add_node("tools", ToolNode(TOOLS))
graph_builder.add_node("generate", generate)

graph_builder.add_edge(START, "query_or_respond")
# 决策节点 -> 需要检索则走 tools，否则直接结束（该节点产出的 AIMessage 即为答案）
graph_builder.add_conditional_edges(
    "query_or_respond",
    tools_condition,
    {END: END, "tools": "tools"},
)
graph_builder.add_edge("tools", "generate")
graph_builder.add_edge("generate", END)

# 多轮记忆：进程内 MemorySaver，按 thread_id 隔离会话
memory: MemorySaver = MemorySaver()

_compiled_graph: Optional[Any] = None


def build_graph() -> Any:
    """编译并返回全局唯一的状态图实例（首次调用时编译，之后复用）。

    Returns:
        已挂载 `MemorySaver` checkpointer 的可运行图。
    """
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = graph_builder.compile(checkpointer=memory)
        logger.info("LangGraph 状态图已编译完成，节点：query_or_respond / tools / generate")
    return _compiled_graph


def get_thread_config(thread_id: str) -> dict[str, Any]:
    """构造带 `thread_id` 的运行配置，供多轮记忆使用。"""
    return {"configurable": {"thread_id": thread_id}}
