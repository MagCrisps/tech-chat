# CCF-A 论文发现助手

用一句中文描述研究兴趣，从 **CCF-A 类期刊 / 会议**论文库中找出相关论文：按子主题分组、逐篇说明为什么相关，并标注出处。支持按年份、刊物、领域、期刊 / 会议筛选，可在多轮对话中继续缩小范围。

当前在线库：**人工智能 · 软件工程 · 数据库/数据挖掘** 三个领域，2023–2025 年，约 **2 万篇**，摘要覆盖 100%。全量库（2020 年至今、93 个 A 类刊物、约 22 万篇）的构建脚本也已提供。

```
输入：帮我找一些用大语言模型做自动程序修复的论文
输出：按「免训练提示驱动 / 微调与混合方法」分组的论文清单，
      每篇带（刊物 年份）与 [Pn] 编号，页面下方对应论文卡片，可核对原文摘要。
```

---

## 一、整体结构

```
【离线：数据管线 pipeline/】
  CCF 官网目录 ──► DBLP 全量 XML ──► 筛出 A 类论文 ──► OpenAlex + 官方站点补摘要
        └──► 数据集 datasets/ccf_a_mini/{年份}/{会议}/papers.jsonl ──► 检索索引 storage/paper_index_mini/

【在线：FastAPI + LangGraph】
  前端(原生 JS) ──SSE──► /api/chat ──► LangGraph 图（决策 → 工具 → 生成）
                                        └──► search_papers 工具 ──► BM25 + 向量混合检索（RRF 融合）
```

技术栈：Python 3.10+ · FastAPI · LangGraph / LangChain · SQLite（FTS5 全文索引 + 会话记忆）· NumPy · Sentence-Transformers · SSE · 原生 HTML/CSS/JS（无框架、无构建）。

## 二、目录

```text
tech-chat/
├── app/                    # 在线服务
│   ├── main.py             # FastAPI 入口（挂载前端、启动时打开会话记忆）
│   ├── config.py           # pydantic-settings 读取 .env
│   ├── api/chat.py         # /api/chat（SSE）、/api/upload、/api/history
│   ├── api/papers.py       # /api/papers/search、/api/papers/facets
│   ├── papers/search.py    # 混合检索：FTS5(BM25) + 向量，RRF 融合，筛选
│   ├── graph/              # LangGraph：决策节点 / 工具节点 / 生成节点
│   ├── tools.py            # search_papers（论文库）、retrieve（个人文档）
│   ├── rag/                # 个人文档入库（加载 / 切分 / 向量库）
│   └── llm.py              # LLM 工厂
├── pipeline/               # 离线数据管线（按序号执行）
│   ├── ccf_venues.py       # ① 抓 CCF 官方目录 → 93 个 A 类刊物
│   ├── dblp_extract.py     # ② 流式解析 DBLP 全量数据 → 论文清单
│   ├── openalex_abstracts.py  # ③ 按 DOI 批量补摘要（OpenAlex）
│   ├── site_abstracts.py   # ③b 官方论文集站点补摘要（NeurIPS/PMLR/CVF/USENIX/Springer…）
│   ├── build_dataset.py    # ④ 合并 → datasets/ccf_a/{年份}/{会议}/papers.jsonl
│   ├── build_mini.py       # ④b 按「领域 × 年份」抽取小数据集
│   └── build_index.py      # ⑤ 建检索索引（SQLite FTS5 + 向量矩阵，增量）
├── frontend/               # index.html / style.css / app.js
├── data/                   # 个人文档（可选，用于文档问答）
└── run.py                  # 一行启动
```

## 三、快速开始

```bash
pip install -r requirements.txt      # 或 pip install -r requirements-lock.txt 锁定版本
cp .env.example .env                 # 填入 LLM 的 base_url / api_key / model
python run.py                        # 默认 http://127.0.0.1:8000
```

启动前需要论文检索索引。若只克隆本仓库，先跑一次数据管线（见下节），或用 `PAPER_DATASET_DIR` 指向已有数据集再执行 `python -m pipeline.build_index`。

## 四、构建数据集与索引

需要约 2 GB 磁盘、可访问 dblp.org。全流程在 CPU 上约 30 分钟（不含摘要补全的 API 额度等待）。

```bash
python -m pipeline.ccf_venues          # ① 93 个 CCF-A 刊物（结果缓存到 datasets/ccf/raw/）
# ② 下载 dblp.xml.gz 与 dblp.dtd 放到 datasets/dblp/，然后：
python -m pipeline.dblp_extract        # ③ 1300 万条记录 → 约 22 万篇 CCF-A 论文（约 3 分钟）
python -m pipeline.openalex_abstracts  # ④ 按 DOI 补摘要（可断点续跑；免费 key 每日额度足够）
python -m pipeline.site_abstracts      # ⑤ 官方站点补摘要（无 DOI / OpenAlex 缺失的部分）
python -m pipeline.build_mini --max 20000   # ⑥ 抽取小数据集（目标领域内摘要覆盖 97%）
python -m pipeline.build_index         # ⑦ 建索引（增量：只计算新增/变化的论文）
```

数据来源与许可：论文元数据来自 [DBLP](https://dblp.org/xml/)（CC0）；摘要来自 [OpenAlex](https://openalex.org)（CC0）与各会议官方论文集页面。**出版社拥有摘要版权**，因此本仓库不附带摘要文本，只提供抓取脚本；如需分发数据集，请只发布元数据（标题、刊物、年份、DOI）。

## 五、接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/chat` | `{"thread_id","message"}` → SSE 流 |
| `GET` | `/api/papers/search?q=&k=&year_from=&venue=&category=&type=` | 论文混合检索（`venue` / `category` / `type` 可重复） |
| `GET` | `/api/papers/facets` | 筛选面板元数据（总数、年份范围、领域与刊物） |
| `POST` | `/api/upload` | 上传个人文档（`multipart/form-data`，字段 `file`） |
| `GET` | `/api/history?thread_id=` | 会话历史 |
| `GET` | `/health` | 健康检查 |

SSE 事件：`status`（阶段提示）、`sources`（论文或文档片段，论文项带 `kind: "paper"`）、`token`（逐字文本）、`done`、`error`。

## 六、实现要点

- **数据**：DBLP 全量 XML 用 `gzip` 流式解压 + `lxml.iterparse` 逐条处理，内存恒定；处理了会议改为期刊发表（PACMPL/PACMHCI/PVLDB）、Workshop 混入主会、非论文条目等数据问题。
- **检索**：SQLite FTS5 做 BM25 关键词检索（标题权重 ×5），Sentence-Transformers 向量做语义检索，两路各取 200 候选，用 RRF（k=60）融合；筛选条件写进 SQL 与布尔掩码，两路同时生效。不引入向量数据库——2 万篇 × 384 维常驻内存，带筛选查询中位延迟约 61 ms。
- **Agent**：LangGraph 三节点（决策 / 工具 / 生成）。`search_papers` 的参数 schema 要求英文检索短语与结构化筛选条件，模型在工具调用中完成中→英改写与条件抽取；多轮追问沿用上一轮主题。
- **可追溯**：回答只能引用检索结果，论文带 `[Pn]` 编号，前端渲染为可核对的论文卡片。
- **记忆**：LangGraph `AsyncSqliteSaver` 持久化会话，重启不丢。

## 七、已知限制

- 在线库覆盖 3 个领域 × 2023–2025；库外领域/年份会如实说明而不是强行回答。
- 尚无正式检索评测集与重排序（当前效果只有人工抽查）。
- 嵌入模型以英文为主，中文查询依赖 LLM 改写。
- 依赖第三方 LLM 接口的稳定性。

## 八、配置项

`config.py` / `.env` 中的关键项：`OPENAI_BASE_URL`、`OPENAI_API_KEY`、`LLM_MODEL`、`EMBEDDING_MODEL_PATH`（或 `EMBEDDING_USE_LOCAL` + `EMBEDDING_LOCAL_PATH`）、`PAPER_DATASET_DIR`、`PAPER_INDEX_DIR`、`CHECKPOINT_DB`、`OPENALEX_API_KEY`（可选，提升补摘要速度）。完整列表见 `.env.example`。
