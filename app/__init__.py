"""CCF-A 论文发现助手（backend package）。"""

__version__ = "0.1.0"

# 尽早加载配置：app.config 会把 HF_ENDPOINT（HuggingFace 下载镜像）写入环境变量。
# 这一步必须早于 langchain_huggingface / sentence-transformers / huggingface_hub 的
# 导入（这些库在 import 时就会读取 HF_ENDPOINT），否则镜像设置不生效——国内直连
# huggingface.co 会连接超时，导致首次下载 embedding 模型失败。
# 由于任何 `app.xxx` 子模块被导入时都会先执行本文件，因此放在这里最稳妥。
from app.config import settings  # noqa: E402,F401

__all__ = ["settings", "__version__"]
