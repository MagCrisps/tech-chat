"""HTTP 接口子包。"""

from app.api.chat import router as chat_router

__all__ = ["chat_router"]
