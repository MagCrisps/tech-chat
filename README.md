# 个人技术知识库聊天助手

把你自己的技术笔记 / 博客 / PDF 放进 `data/` 目录，就可以在网页里和它对话：
问技术问题会**先检索原文、再基于原文回答并标注来源**；问「你好」这类寒暄则**直接回答、不触发检索**；
支持多轮追问，找不到答案时会明确说「知识库中未找到相关内容」而不是编造。

技术栈：FastAPI + LangGraph（StateGraph / MessagesState）+ Chroma + HuggingFace Embeddings + 原生 HTML/CSS/JS。

---

## 一、目录结构

```text
tech-chat/
├── app/
│   ├── __init__.py
│   ├── main.py            # FastAPI 入口：挂载静态资源、启动时建库、自动打开浏览器
│   ├── config.py          # pydantic-settings 集中读取 .env
│   ├── api/
│   │   ├── __init__.py
│   │   └── chat.py        # /api/chat（SSE）、/api/upload、/api/history
│   ├── graph/
│   │   ├── __init__.py
│   │   ├── builder.py     # 构建并编译 StateGraph（唯一入口 build_graph()）
│   │   └── nodes.py       # query_or_respond / generate 节点
│   ├── rag/
│   │   ├── __init__.py
│   │   ├── loader.py      # 按后缀分发加载器（md/txt/pdf/html）
│   │   ├── splitter.py    # RecursiveCharacterTextSplitter 切分
│   │   └── store.py       # 向量库单例 + 增量建库
│   ├── tools.py           # @tool 定义的 retrieve
│   └── llm.py             # LLM 工厂
├── frontend/              # index.html / style.css / app.js
├── data/                  # 你的文档放这里（含示例 sample.md）
├── chroma_db/             # 向量库持久化目录（首次运行自动生成）
├── .env.example
├── requirements.txt
├── run.py                 # 一行启动
└── README.md
```

---

## 二、安装

需要 **Python 3.10+**。

```bash
cd tech-chat

# 建议使用虚拟环境
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

> 首次运行会从 HuggingFace 下载 embedding 模型（几十 MB），请保持网络畅通；
> 也可以把模型提前下载到本地，然后把 `EMBEDDING_MODEL_PATH` 指向本地目录。

---

## 三、配置 `.env`

复制模板并填写真实值（**不要把真实 Key 提交到 git**）：

```bash
copy .env.example .env      # Windows
cp .env.example .env        # macOS / Linux
```

```dotenv
# ---------- LLM（OpenAI 兼容接口）----------
OPENAI_BASE_URL=https://api.openai.com/v1      # 也可换成 DeepSeek / 本地 vLLM / Ollama 的地址
OPENAI_API_KEY=sk-你的真实key
LLM_MODEL=gpt-4o-mini
LLM_TEMPERATURE=0
LLM_TIMEOUT=60
LLM_MAX_RETRIES=2

# ---------- Embedding ----------
# 支持 HuggingFace 模型名，也支持本地路径（如 D:/models/bge-small-zh-v1.5）
EMBEDDING_MODEL_PATH=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
EMBEDDING_DEVICE=auto          # auto 会自动判断 cuda 是否可用
EMBEDDING_NORMALIZE=true

# ---------- 向量库 ----------
CHROMA_PERSIST_DIR=./chroma_db
CHROMA_COLLECTION=tech_knowledge

# ---------- RAG ----------
DATA_DIR=./data
CHUNK_SIZE=1000
CHUNK_OVERLAP=200
RETRIEVAL_K=4

# ---------- 服务 ----------
HOST=127.0.0.1
PORT=8000
OPEN_BROWSER=true              # 启动后自动打开浏览器
```

| 变量 | 说明 |
| --- | --- |
| `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `LLM_MODEL` | 任意 OpenAI 兼容服务的地址、密钥、模型名 |
| `EMBEDDING_MODEL_PATH` | HuggingFace 模型名或本地模型目录；中文资料推荐 `BAAI/bge-small-zh-v1.5` |
| `EMBEDDING_DEVICE` | `auto` / `cpu` / `cuda` |
| `RETRIEVAL_K` | 每次检索返回的片段数量 |
| `OPEN_BROWSER` | 是否在启动后自动打开 `http://HOST:PORT` |

---

## 四、启动

```bash
python run.py
```

等价于 `uvicorn app.main:app --host 127.0.0.1 --port 8000`。
启动过程会：

1. 编译 LangGraph 状态图（`query_or_respond → tools → generate`）；
2. 扫描 `data/` 目录，把新增 / 变更的文件切分并写入 `./chroma_db`（未变化的文件自动跳过）；
3. 延迟约 1.5 秒后用 `webbrowser.open("http://127.0.0.1:8000")` 自动打开浏览器。

浏览器没有自动弹出时，手动访问 <http://127.0.0.1:8000> 即可。

---

## 五、使用说明

1. **问技术问题**：例如「什么是任务分解？」——机器人会先检索，再基于原文回答，答案下方展示
   `文件名 + chunk 起始位置` 的引用来源。
2. **寒暄**：例如「你好」——不会触发检索，直接回答。
3. **多轮追问**：例如接着问「能举个例子吗？」——同一 `thread_id` 下会带上历史上下文。
4. **知识库没有的内容**：会明确回答「知识库中未找到相关内容」，不会编造。
5. **新对话**：点击右上角「新对话」，会清空历史并重新生成 `thread_id`。
6. **上传文档**：点击「上传文档」选择 `.md / .txt / .pdf / .html` 文件，入库后即可立即提问
   （也可以在服务运行时直接把文件丢进 `data/` 目录，然后调用 `POST /api/upload` 或重启服务）。

### 接口一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/chat` | 请求体 `{"thread_id": "...", "message": "..."}`，返回 SSE 流 |
| `POST` | `/api/upload` | `multipart/form-data`，字段名 `file`，立即入库 |
| `GET` | `/api/history?thread_id=xxx` | 拉取该会话历史消息 |
| `GET` | `/health` | 健康检查 |

SSE 事件类型：

```text
{"type":"status","stage":"thinking","message":"正在思考…"}
{"type":"status","stage":"retrieving","message":"正在检索知识库…"}
{"type":"sources","sources":[{"file_name":"sample.md","start_index":0,"preview":"..."}]}
{"type":"token","content":"任务"}
{"type":"done"}
{"type":"error","message":"服务异常：..."}
```

---

## 六、常见问题（FAQ）

**1. 启动报 `未配置 OPENAI_API_KEY`**
没有创建 `.env` 或没有填写 `OPENAI_API_KEY`。复制 `.env.example` 为 `.env` 并填写即可。

**2. 页面能打开，但提问一直转圈 / 报错**
多为 `OPENAI_BASE_URL` 或 `OPENAI_API_KEY` 不正确，或网络无法访问该服务。
后端控制台会打印具体异常；可在 `.env` 中调大 `LLM_TIMEOUT`。

**3. 首次启动很慢**
首次运行需要下载 embedding 模型并做向量化。下载完成后会缓存在本地（HuggingFace cache），
之后再启动会快很多；已经入库的文件（指纹未变化）会被自动跳过。

**4. 明明有文档却回答「知识库中未找到相关内容」**
- 确认文件后缀受支持（`.md / .txt / .pdf / .html` 等）；
- 确认文件放进了 `DATA_DIR` 指定的目录，然后重启服务或通过页面上传；
- 中文资料建议换成中文 embedding 模型（如 `BAAI/bge-small-zh-v1.5`），并适当调大 `CHUNK_SIZE`；
- 调大 `RETRIEVAL_K` 让召回片段更多。

**5. 想让文件重新入库**
删掉对应的 `chroma_db/` 目录后重启，或在 Python 中调用
`from app.rag.store import build_index; build_index(force=True)`。

**6. 换了一个 embedding 模型后检索结果很怪 / 报维度错误**
Chroma 里存的向量与当前 embedding 模型维度不一致。删除 `chroma_db/` 目录后重启重建即可。

**7. 端口被占用**
修改 `.env` 里的 `PORT`（并同步修改 `HOST` 如需对外暴露）。

**8. 「新对话」之后历史还在**
历史保存在内存（`MemorySaver`）中并按 `thread_id` 隔离，「新对话」会生成新的 `thread_id`，
因此不会看到旧内容；服务重启后历史会全部清空（内存存储，不落盘）。

---

## 七、实现要点（开发参考）

- **图只构建一次**：`app/graph/builder.py` 中模块级创建 `StateGraph(MessagesState)` 并注册节点，
  `build_graph()` 内用 `graph_builder.compile(checkpointer=memory)` 编译并缓存，避免重复 `add_node`。
- **记忆**：`MemorySaver` 在编译时传入，每个请求都要带 `config={"configurable": {"thread_id": ...}}`。
- **提示词**：直接使用 `SystemMessage` 拼接，不依赖 `hub.pull`，避免联网与版本漂移。
- **工具**：`retrieve` 用 `@tool(response_format="content_and_artifact")`，返回
  `(给模型阅读的文本, 结构化片段列表)`，配合 `ToolNode(..., response_format="content_and_artifact")`
  把引用来源透传到前端。
- **单例**：向量库、Embedding、LLM、状态图都用模块级单例 + 工厂函数，避免重复初始化。
