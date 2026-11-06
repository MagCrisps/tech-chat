"""FastAPI 入口：注册路由、打开会话记忆、启动时建库、启动后自动打开浏览器。"""

from __future__ import annotations

import logging
import threading
import webbrowser

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

# 注意：必须先导入 config（它会设置 HF_ENDPOINT 镜像地址），再导入会触发
# huggingface_hub / sentence-transformers 的模块，否则镜像设置不会生效。
from app.config import settings
from app.api.chat import router as chat_router
from app.api.papers import router as papers_router
from app.graph import build_graph, open_checkpointer
from app.rag.store import build_index

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("tech-chat")

_browser_opened = False


def _open_browser() -> None:
    """延迟打开浏览器（放在子线程里，避免阻塞服务启动）。"""
    global _browser_opened
    if _browser_opened:
        return
    _browser_opened = True
    url = f"http://{settings.host}:{settings.port}"
    try:
        webbrowser.open(url)
        logger.info("已尝试自动打开浏览器：%s", url)
    except Exception as exc:  # pragma: no cover
        logger.warning("自动打开浏览器失败（请手动访问 %s）：%s", url, exc)


def _prepare() -> None:
    """启动前的同步准备工作：编译图 + 增量建库。"""
    build_graph()
    try:
        summary = build_index(force=False)
        logger.info(
            "知识库就绪：新增/更新 %s，跳过 %s，失败 %s，向量总数 %s",
            summary["indexed"],
            summary["skipped"],
            summary["failed"],
            summary["total_vectors"],
        )
        if summary["total_vectors"] == 0:
            logger.warning("向量库为空，请把技术文档放入 %s 目录后重启或通过页面上传。", settings.data_dir_path)
    except Exception:
        logger.exception("建库失败，服务仍会启动；可通过页面上传文件重试。")


async def lifespan(_: FastAPI):
    """应用生命周期：打开会话记忆 + 建库 + 自动打开浏览器；退出时关闭记忆库连接。"""
    logger.info("正在初始化…（数据目录：%s）", settings.data_dir_path)
    async with open_checkpointer():
        await run_in_threadpool(_prepare)

        if settings.open_browser:
            threading.Timer(1.5, _open_browser).start()

        yield
    logger.info("服务已停止。")


app = FastAPI(
    title="个人技术知识库聊天助手",
    description="LangGraph + RAG + FastAPI 的本地知识库问答服务",
    version="0.1.0",
    lifespan=lifespan,
)

# 业务接口（必须在静态资源挂载之前注册）
app.include_router(chat_router)
app.include_router(papers_router)


@app.get("/health")
async def health() -> dict[str, str]:
    """健康检查。"""
    return {"status": "ok"}


# 前端静态资源：html=True 时访问 "/" 会自动返回 index.html
if settings.static_dir.exists():
    app.mount("/", StaticFiles(directory=str(settings.static_dir), html=True), name="frontend")
else:  # pragma: no cover - 正常仓库里 frontend/ 一定存在
    logger.warning("未找到前端目录：%s", settings.static_dir)

    @app.get("/")
    async def index_placeholder() -> HTMLResponse:
        return HTMLResponse("<h1>前端目录缺失</h1><p>请确认 frontend/index.html 存在。</p>")
