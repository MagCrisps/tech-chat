# 前端接口文档（个人技术知识库助手）

> 本文档面向前端重构。后端已固定，前端只需按此契约调用接口即可。
> 请保持「原生 HTML / CSS / JavaScript（无构建工具、无框架）」实现：前端三个文件
> `frontend/index.html`、`frontend/style.css`、`frontend/app.js` 由后端以静态资源方式
> 挂载在站点根路径 `/`，因此 `index.html` 里引用 `/style.css`、`/app.js` 即可。

---

## 0. 基础信息

| 项目 | 说明 |
| --- | --- |
| 服务地址 | `http://127.0.0.1:8000`（同源部署，前端直接用相对路径调用，如 `/api/chat`） |
| 鉴权 | 无（本地个人使用） |
| 编码 | 全部 UTF-8；请求/响应体为 JSON（上传接口为 `multipart/form-data`） |
| 跨域 | 同源，无需处理 CORS |
| 静态资源 | `/` 指向 `frontend/` 目录，`index.html` 为入口 |

端点总览：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/chat` | 发送提问，SSE 流式返回回答 |
| `POST` | `/api/upload` | 上传文档并立即向量化入库 |
| `GET` | `/api/history` | 按会话 ID 拉取历史消息 |
| `GET` | `/health` | 健康检查 |

---

## 1. POST /api/chat —— 流式问答（核心）

### 请求

- **Method**: `POST`
- **Headers**: `Content-Type: application/json`
- **Body**:

```json
{
  "thread_id": "thread-1730730000000-ab12cd34",
  "message": "RAG 的检索流程是怎样的？"
}
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `thread_id` | string | 是 | 会话 ID，长度 ≥ 1。用于多轮对话记忆隔离（后端按此 ID 保存上下文）。 |
| `message` | string | 是 | 用户本轮输入，长度 ≥ 1。 |

### 响应

- **Status**: `200`
- **Content-Type**: `text/event-stream`
- **响应头**: `Cache-Control: no-cache`、`Connection: keep-alive`、`X-Accel-Buffering: no`
- **Body**: SSE 流。每个事件为一帧，格式固定为：

```
data: {JSON}\n\n
```

即：以 `data: ` 开头的一行 JSON，后跟一个空行（`\n\n`）作为帧分隔。
**前端请用 `fetch` + `response.body.getReader()` 读取，按 `\n\n` 切分缓冲区分帧，
对每个 `data:` 行做 `JSON.parse`。**

### SSE 事件类型（按可能出现的顺序）

**① `status` —— 阶段提示（流开始时立即下发）**

```json
{ "type": "status", "stage": "thinking", "message": "正在思考…" }
```

```json
{ "type": "status", "stage": "retrieving", "message": "正在检索知识库…" }
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `type` | string | 固定 `"status"` |
| `stage` | string | `"thinking"` = 正在思考；`"retrieving"` = 正在检索知识库 |
| `message` | string | 可直接展示给用户的中文文案 |

> `retrieving` 仅当模型决定调用知识库检索时才出现；寒暄类问题（如「你好」）不会检索。

**② `sources` —— 引用的文档来源（检索完成后下发，可能不出现）**

```json
{
  "type": "sources",
  "sources": [
    {
      "file_name": "sample.md",
      "start_index": 128,
      "preview": "检索增强生成（RAG）的核心流程可以分为三步：首先……"
    }
  ]
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `type` | string | 固定 `"sources"` |
| `sources` | array | 来源片段列表，可能为空数组（此时无需渲染） |
| `sources[].file_name` | string | 源文件名（可能为 `null`，请做兜底文案） |
| `sources[].start_index` | number | 该片段在原文中的起始字符位置（用于定位，可展示为「chunk 起始位置 N」） |
| `sources[].preview` | string | 片段内容预览，最长约 300 字符（超出以 `…` 结尾） |

**③ `token` —— 逐段文本（可多次出现，是回答正文）**

```json
{ "type": "token", "content": "RAG" }
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `type` | string | 固定 `"token"` |
| `content` | string | 增量文本片段。**需按到达顺序追加拼接**，然后整段做 Markdown 渲染显示。 |

**④ `done` —— 流正常结束**

```json
{ "type": "done" }
```

收到后应停止 loading 状态、移除光标/动画。

**⑤ `error` —— 服务异常（流可能在中途以该事件结束）**

```json
{ "type": "error", "message": "服务异常：……" }
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `type` | string | 固定 `"error"` |
| `message` | string | 错误描述，可直接展示 |

> 注意：即便发生错误，HTTP 状态码仍是 `200`（SSE 流已开始），错误通过 `error` 事件传递。
> 只有当请求在流开始前就失败（如后端未启动），才会是普通 HTTP 错误。

### 内容格式约定

- 回答正文是 **Markdown**，前端需要渲染（标题、有序/无序列表、引用块、代码块、
  加粗 `**x**`、行内代码 `` `x` ``）。可用轻量自研渲染器，无需引入第三方库。
- **安全**：渲染前必须先做 HTML 转义，防止注入。

---

## 2. POST /api/upload —— 上传文档入库

### 请求

- **Method**: `POST`
- **Headers**: `Content-Type: multipart/form-data`
- **Form 字段**:

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `file` | File | 是 | 待入库的文档 |

支持类型（扩展名）：`.md`、`.markdown`、`.txt`、`.pdf`、`.html`、`.htm`、`.rst`、`.json`、`.yaml`、`.yml`、`.log`
（`<input type="file">` 的 `accept` 建议与之一致）

### 成功响应（HTTP 200）

```json
{
  "ok": true,
  "source": "data/sample.md",
  "file_name": "sample.md",
  "chunks": 6,
  "message": "《sample.md》已入库，共 6 个片段，现在就可以针对它提问了。"
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `ok` | boolean | 是否成功，成功恒为 `true` |
| `source` | string | 文件落盘路径 |
| `file_name` | string | 文件名 |
| `chunks` | number | 切分后的片段数量 |
| `message` | string | 可直接展示的中文提示 |

### 失败响应（HTTP 400）

```json
{ "error": "上传失败：不支持的文件类型：.exe（支持 md / txt / pdf / html 等）" }
```

前端应同时判断 `response.ok` 与响应体里的 `error` 字段，二者任一表示失败。

---

## 3. GET /api/history —— 拉取历史消息

### 请求

- **Method**: `GET`
- **Query 参数**:

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `thread_id` | string | 是 | 会话 ID（长度 ≥ 1） |

示例：`GET /api/history?thread_id=thread-1730730000000-ab12cd34`

### 成功响应（HTTP 200）

```json
{
  "thread_id": "thread-1730730000000-ab12cd34",
  "messages": [
    { "role": "user", "content": "什么是任务分解？" },
    { "role": "assistant", "content": "任务分解是指……" }
  ]
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `thread_id` | string | 原样返回请求的会话 ID |
| `messages` | array | 按时间顺序的消息列表 |
| `messages[].role` | string | 仅 `"user"` 或 `"assistant"`（工具调用消息已被后端过滤，不会返回） |
| `messages[].content` | string | 消息内容；`assistant` 内容为 Markdown，需渲染 |

> 无历史时返回 `messages: []`。取历史失败时返回 HTTP 500（`detail` 字段）。

---

## 4. GET /health —— 健康检查

- **响应（HTTP 200）**：

```json
{ "status": "ok" }
```

可用于顶部状态指示灯（就绪 / 异常）。

---

## 5. 前端行为契约（重构后请务必保留）

这些是当前前端 `app.js` 已有的、必须延续的行为与约定：

1. **会话 ID 持久化**
   - `localStorage` key 固定为 **`tech_chat_thread_id`**。
   - 首次进入若不存在则生成新 ID，格式建议：`thread-{Date.now()}-{8位随机串}`。
   - 页面加载时用该 ID 调 `/api/history` 恢复历史。

2. **「新对话」**
   - 生成全新 `thread_id` 并写入 `localStorage`，清空消息区，回到空状态占位。

3. **SSE 解析**
   - 用 `fetch` (`POST /api/chat`) + `ReadableStream`；`TextDecoder('utf-8')` 增量解码；
   - 缓冲区按 `\n\n` 切分，最后一段可能不完整需保留到下一轮；
   - 每帧提取 `data:` 前缀行 → `JSON.parse`。

4. **流式渲染交互**
   - 发送后先插入「用户消息」，再插入「助手占位气泡」（带 loading / 阶段文字）；
   - 收到第一个 `token` 时切换到正文区并显示打字光标；
   - 收到 `done` 或流结束：移除光标与 loading；
   - 收到 `sources`：在回答下方渲染「引用来源（N）」列表，每项展示 `file_name`、
     `start_index`、`preview`；
   - 收到 `error`：把气泡标红并显示 `message`。

5. **上传交互**
   - 点击「上传文档」→ 触发隐藏的 `<input type="file">`；
   - 选择后立即 `POST /api/upload`，处理中显示「正在处理《文件名》…」，成功/失败给出提示。

6. **输入交互**
   - `Enter` 发送、`Shift + Enter` 换行；
   - 输入框随内容自动增高（有上限，例如 160px）；
   - 生成回答期间禁用发送按钮，避免并发请求。

7. **滚动**
   - 新内容到达时，若用户已接近底部则自动跟随滚动；用户上滑查看历史时不要强制拉回。

---

## 6. 现有 DOM 结构参考（可自由重构，仅列关键交互元素）

当前 `index.html` 的元素 ID（重构可改名，但请保证对应功能仍在）：

| ID / class | 用途 |
| --- | --- |
| `.topbar` / `.brand` | 顶部标题栏 |
| `#status-dot` | 服务状态指示灯（就绪 / 忙碌 / 异常） |
| `#upload-btn` | 上传文档按钮 |
| `#new-chat-btn` | 新对话按钮 |
| `#file-input` | 隐藏的文件选择框 |
| `#messages` | 消息滚动区 |
| `#empty-state` | 空状态占位（首次进入 / 新对话时显示） |
| `#input` | 问题输入框（`textarea`） |
| `#send-btn` | 发送按钮 |

---

## 7. 最小可用示例（前端调用参考）

```js
// 1) 流式问答
async function ask(threadId, text) {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ thread_id: threadId, message: text }),
  });
  const reader = res.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split('\n\n');
    buffer = blocks.pop();                       // 末段可能不完整，留到下一轮
    for (const block of blocks) {
      const line = block.split('\n').find((l) => l.startsWith('data:'));
      if (!line) continue;
      const event = JSON.parse(line.slice(5).trim()); // { type, ... }
      // event.type: status | sources | token | done | error
      console.log(event);
    }
  }
}

// 2) 上传
async function upload(file) {
  const form = new FormData();
  form.append('file', file);
  const res = await fetch('/api/upload', { method: 'POST', body: form });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || res.status);
  return data.message;   // 展示给用户
}

// 3) 历史
async function history(threadId) {
  const res = await fetch(`/api/history?thread_id=${encodeURIComponent(threadId)}`);
  return (await res.json()).messages;   // [{ role, content }]
}
```

---

## 8. 重构要求小结

- 纯前端实现，不改动后端；只用 `frontend/` 下三个文件，勿引入打包工具。
- 视觉上可以大幅美化（深色/浅色主题、更精致的卡片与气泡、动效等），但**必须完整实现
  上文第 5 节的交互契约**，否则会破坏流式回答、来源展示、上传入库等核心功能。
- 回答正文按 Markdown 渲染，渲染前必须 HTML 转义。
- 所有面向用户的文案使用中文。
