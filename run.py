"""一行启动：python run.py

等价于 `uvicorn app.main:app --host 127.0.0.1 --port 8000`，
并在服务就绪后自动打开浏览器（逻辑在 app/main.py 的 lifespan 中）。
"""

from __future__ import annotations

import uvicorn

from app.config import settings

if __name__ == "__main__":
    print(f"启动服务： http://{settings.host}:{settings.port}  （Ctrl+C 停止）")
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level="info",
    )
