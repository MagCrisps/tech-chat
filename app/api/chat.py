"""对话相关接口：SSE 流式问答、文件上传入库、历史消息拉取。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, AsyncGenerator

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from pydantic import BaseModel, Field

from app.config import settings
from app.graph import build_graph, get_thread_config
from app.rag.loader import is_supported
from app.rag.store import add_file

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])


# --------------------------------------------------------------------------- #
# 请求 / 响应模型
# --------------------------------------------------------------------------- #
class ChatRequest(BaseModel):
    """对话请求体。"""

    thread_id: str = Field(..., min_length=1, description="会话 ID，用于多轮记忆隔离")
    message: str = Field(..., min_length=1, description="用户输入")


def _sse(payload: dict[str, Any]) -> str:
    """把字典包装成 SSE 数据帧。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _chunk_text(chunk: BaseMessage) -> str:
    """从模型流式分片中提取纯文本内容（兼容 str / 内容块列表）。"""
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return ""


def _preview(text: str, limit: int = 300) -> str:
    """截取片段预览文本。"""
    clean = " ".join((text or "").split())
    return clean if len(clean) <= limit else clean[:limit] + "…"


def _collect_sources(update: dict[str, Any]) -> list[dict[str, Any]]:
    """从 tools 节点的更新里提取检索到的文档片段。"""
    sources: list[dict[str, Any]] = []
    for message in (update or {}).get("messages") or []:
        artifact = getattr(message, "artifact", None)
        if not artifact:
            continue
        for doc in artifact:
            if isinstance(doc, dict):
                sources.append(
                    {
                        "file_name": doc.get("file_name"),
                        "start_index": doc.get("start_index", 0),
                        "preview": _preview(str(doc.get("page_content", ""))),
                    }
                )
            else:  # 兼容 Document 对象
                metadata = getattr(doc, "metadata", {}) or {}
                sources.append(
                    {
                        "file_name": metadata.get("file_name") or Path(str(metadata.get("source", ""))).name,
                        "start_index": metadata.get("start_index", 0),
                        "preview": _preview(getattr(doc, "page_content", "")),
                    }
                )
    return sources


# --------------------------------------------------------------------------- #
# 1) 流式对话
# --------------------------------------------------------------------------- #
@router.post("/chat")
async def chat(request: ChatRequest) -> StreamingResponse:
    """以 SSE 流式返回回答。

    事件类型：
      - `{"type": "status", "stage": "thinking|retrieving", ...}` 阶段提示
      - `{"type": "sources", "sources": [...]}` 本次回答引用的文档来源
      - `{"type": "token", "content": "..."}` 逐 token 文本
      - `{"type": "done"}` 结束
      - `{"type": "error", "message": "..."}` 异常
    """
    graph = build_graph()
    config = get_thread_config(request.thread_id)
    inputs = {"messages": [HumanMessage(content=request.message)]}

    async def event_stream() -> AsyncGenerator[str, None]:
        yield _sse({"type": "status", "stage": "thinking", "message": "正在思考…"})
        try:
            async for mode, chunk in graph.astream(inputs, config=config, stream_mode=["messages", "updates"]):
                if mode == "updates":
                    for node, update in (chunk or {}).items():
                        if node == "query_or_respond":
                            produced = (update or {}).get("messages") or []
                            if any(getattr(m, "tool_calls", None) for m in produced):
                                yield _sse(
                                    {"type": "status", "stage": "retrieving", "message": "正在检索知识库…"}
                                )
                        elif node == "tools":
                            sources = _collect_sources(update or {})
                            if sources:
                                yield _sse({"type": "sources", "sources": sources})
                elif mode == "messages":
                    message_chunk, metadata = chunk
                    if metadata.get("langgraph_node") not in {"query_or_respond", "generate"}:
                        continue
                    if getattr(message_chunk, "tool_call_chunks", None) or getattr(
                        message_chunk, "tool_calls", None
                    ):
                        continue
                    text = _chunk_text(message_chunk)
                    if text:
                        yield _sse({"type": "token", "content": text})
            yield _sse({"type": "done"})
        except Exception as exc:  # noqa: BLE001 - 统一降级为前端可展示的错误
            logger.exception("对话流处理失败")
            yield _sse({"type": "error", "message": f"服务异常：{exc}"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# --------------------------------------------------------------------------- #
# 2) 上传文件并立即入库
# --------------------------------------------------------------------------- #
@router.post("/upload")
async def upload(file: UploadFile = File(...)) -> JSONResponse:
    """接收上传文件，保存到 `data/` 后立即向量化入库。"""
    try:
        raw_name = Path(file.filename or "").name
        if not raw_name:
            raise ValueError("文件名无效")
        target = settings.data_dir_path / raw_name
        if not is_supported(target):
            raise ValueError(f"不支持的文件类型：{target.suffix}（支持 md / txt / pdf / html 等）")

        content = await file.read()
        if not content:
            raise ValueError("文件内容为空")

        settings.data_dir_path.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

        result = await run_in_threadpool(add_file, target)
        return JSONResponse(
            {
                "ok": True,
                **result,
                "message": f"《{result['file_name']}》已入库，共 {result['chunks']} 个片段，现在就可以针对它提问了。",
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("上传失败")
        return JSONResponse(status_code=400, content={"error": f"上传失败：{exc}"})
    finally:
        await file.close()


# --------------------------------------------------------------------------- #
# 3) 拉取历史消息
# --------------------------------------------------------------------------- #
@router.get("/history")
async def history(thread_id: str = Query(..., min_length=1)) -> dict[str, Any]:
    """按 `thread_id` 返回该会话的历史消息（只保留 user / assistant）。"""
    try:
        graph = build_graph()
        state = await run_in_threadpool(graph.get_state, get_thread_config(thread_id))
        messages: list[BaseMessage] = list((state.values or {}).get("messages", [])) if state else []

        result: list[dict[str, str]] = []
        for message in messages:
            if isinstance(message, HumanMessage):
                result.append({"role": "user", "content": _chunk_text(message)})
            elif isinstance(message, AIMessage) and not message.tool_calls:
                text = _chunk_text(message)
                if text:
                    result.append({"role": "assistant", "content": text})
            elif isinstance(message, ToolMessage):
                continue  # 工具调用结果不返回给前端
        return {"thread_id": thread_id, "messages": result}
    except Exception as exc:  # noqa: BLE001
        logger.exception("拉取历史失败")
        raise HTTPException(status_code=500, detail=f"拉取历史失败：{exc}") from exc
