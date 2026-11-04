/**
 * 个人技术知识库助手 —— 前端逻辑（原生 JS）
 * 通过 fetch + ReadableStream 解析后端 SSE，实时渲染 token、状态与来源。
 */

const API = {
  chat: '/api/chat',
  upload: '/api/upload',
  history: (id) => `/api/history?thread_id=${encodeURIComponent(id)}`,
};

const STORAGE_KEY = 'tech_chat_thread_id';

const els = {
  messages: document.getElementById('messages'),
  emptyState: document.getElementById('empty-state'),
  input: document.getElementById('input'),
  sendBtn: document.getElementById('send-btn'),
  newChatBtn: document.getElementById('new-chat-btn'),
  uploadBtn: document.getElementById('upload-btn'),
  fileInput: document.getElementById('file-input'),
  statusDot: document.getElementById('status-dot'),
};

let threadId = localStorage.getItem(STORAGE_KEY) || createThreadId();
localStorage.setItem(STORAGE_KEY, threadId);
let streaming = false;

/* ============================ 工具函数 ============================ */

/** 生成一个新的会话 ID。 */
function createThreadId() {
  const rand = Math.random().toString(36).slice(2, 10);
  return `thread-${Date.now()}-${rand}`;
}

/** 设置右上角状态指示灯。 */
function setStatus(state, title) {
  els.statusDot.className = `dot ${state || ''}`.trim();
  if (title) els.statusDot.title = title;
}

/** 是否已经滚到底部（用于判断要不要自动跟随）。 */
function nearBottom() {
  const box = els.messages;
  return box.scrollHeight - box.scrollTop - box.clientHeight < 160;
}

/** 滚动到底部。 */
function scrollToBottom(force = false) {
  if (force || nearBottom()) {
    els.messages.scrollTop = els.messages.scrollHeight;
  }
}

/** HTML 转义，防止注入。 */
function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

/** 行内 Markdown（粗体 / 行内代码）。 */
function renderInline(text) {
  return text
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
}

/** 轻量 Markdown 渲染（标题 / 列表 / 引用 / 代码块 / 加粗）。 */
function renderMarkdown(source) {
  if (!source) return '';
  const segments = escapeHtml(source).split('```');
  let html = '';
  segments.forEach((segment, index) => {
    if (index % 2 === 1) {
      const newline = segment.indexOf('\n');
      const code = newline >= 0 ? segment.slice(newline + 1) : segment;
      html += `<pre><code>${code.replace(/\n+$/, '')}</code></pre>`;
      return;
    }
    html += renderBlocks(segment);
  });
  return html;
}

/** 块级元素渲染。 */
function renderBlocks(text) {
  const lines = text.split('\n');
  let html = '';
  let list = null;
  const closeList = () => { if (list) { html += `</${list}>`; list = null; } };

  for (const raw of lines) {
    const line = raw.trim();
    if (!line) { closeList(); continue; }

    let m = line.match(/^#{1,6}\s+(.*)$/);
    if (m) { closeList(); html += `<h4>${renderInline(m[1])}</h4>`; continue; }

    m = line.match(/^[-*+]\s+(.*)$/);
    if (m) {
      if (list !== 'ul') { closeList(); html += '<ul>'; list = 'ul'; }
      html += `<li>${renderInline(m[1])}</li>`;
      continue;
    }

    m = line.match(/^\d+[.)]\s+(.*)$/);
    if (m) {
      if (list !== 'ol') { closeList(); html += '<ol>'; list = 'ol'; }
      html += `<li>${renderInline(m[1])}</li>`;
      continue;
    }

    m = line.match(/^>\s?(.*)$/);
    if (m) { closeList(); html += `<blockquote>${renderInline(m[1])}</blockquote>`; continue; }

    closeList();
    html += `<p>${renderInline(line)}</p>`;
  }
  closeList();
  return html;
}

/* ============================ DOM 构建 ============================ */

/** 隐藏空状态占位。 */
function hideEmptyState() {
  if (els.emptyState) els.emptyState.style.display = 'none';
}

/** 创建一条消息行，返回内部引用。 */
function createRow(role) {
  hideEmptyState();
  const row = document.createElement('div');
  row.className = `row ${role}`;

  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.textContent = role === 'user' ? '🙋' : '🤖';

  const bubble = document.createElement('div');
  bubble.className = 'bubble';

  row.append(avatar, bubble);
  els.messages.appendChild(row);
  scrollToBottom(true);
  return { row, bubble };
}

/** 追加用户消息。 */
function addUserMessage(text) {
  const { bubble } = createRow('user');
  bubble.textContent = text;
}

/**
 * 创建「回答中」的占位气泡。
 * @returns {{bubble: HTMLElement, setStatus: Function, appendToken: Function,
 *            setSources: Function, setError: Function, finish: Function}}
 */
function createAssistantMessage() {
  const { bubble } = createRow('assistant');

  const status = document.createElement('div');
  status.className = 'status';
  status.innerHTML = '<span class="spinner"></span><span class="status-text">正在思考…</span>';
  bubble.appendChild(status);
  const statusText = status.querySelector('.status-text');

  const body = document.createElement('div');
  body.className = 'content';
  body.style.display = 'none';
  bubble.appendChild(body);

  let raw = '';
  let cursor = null;

  return {
    bubble,
    setStatus(text) {
      status.querySelector('.spinner').style.display = '';
      statusText.textContent = text;
      scrollToBottom();
    },
    setStage(text) {
      status.querySelector('.spinner').style.display = 'none';
      statusText.textContent = text;
    },
    appendToken(token) {
      if (raw === '') {
        status.remove();
        body.style.display = '';
        cursor = document.createElement('span');
        cursor.className = 'cursor';
        body.after(cursor);
      }
      raw += token;
      body.innerHTML = renderMarkdown(raw);
      scrollToBottom();
    },
    setSources(sources) {
      if (!sources || !sources.length) return;
      const wrap = document.createElement('div');
      wrap.className = 'sources';
      const title = document.createElement('div');
      title.className = 'sources-title';
      title.textContent = `引用来源（${sources.length}）`;
      wrap.appendChild(title);

      for (const item of sources) {
        const box = document.createElement('div');
        box.className = 'source-item';
        const head = document.createElement('div');
        head.className = 'source-head';
        head.textContent = `📄 ${item.file_name || '未知文件'} · chunk 起始位置 ${item.start_index ?? 0}`;
        const preview = document.createElement('div');
        preview.className = 'source-preview';
        preview.textContent = item.preview || '';
        box.append(head, preview);
        wrap.appendChild(box);
      }
      bubble.appendChild(wrap);
      scrollToBottom();
    },
    setError(message) {
      status.remove();
      bubble.classList.add('error');
      bubble.textContent = message;
      scrollToBottom();
    },
    finish() {
      if (cursor) { cursor.remove(); cursor = null; }
      status.remove();
    },
    hasContent() { return raw !== ''; },
  };
}

/* ============================ 流式请求 ============================ */

/**
 * 解析一行 SSE 数据。
 * @param {string} block 以空行分隔的数据块
 * @returns {object|null} 解析后的 JSON 负载
 */
function parseSseBlock(block) {
  const dataLines = block
    .split('\n')
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).trim());
  if (!dataLines.length) return null;
  try {
    return JSON.parse(dataLines.join('\n'));
  } catch (err) {
    console.warn('SSE 数据解析失败：', err, block);
    return null;
  }
}

/** 发送一条消息并流式渲染回答。 */
async function sendMessage(text) {
  if (streaming) return;
  streaming = true;
  els.sendBtn.disabled = true;
  setStatus('busy', '正在生成回答…');

  addUserMessage(text);
  const view = createAssistantMessage();
  let gotSources = false;

  try {
    const response = await fetch(API.chat, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ thread_id: threadId, message: text }),
    });

    if (!response.ok || !response.body) {
      let detail = `请求失败（HTTP ${response.status}）`;
      try {
        const data = await response.json();
        if (data && data.error) detail = data.error;
      } catch (_) { /* 忽略非 JSON 响应 */ }
      throw new Error(detail);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';

    // eslint-disable-next-line no-constant-condition
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const blocks = buffer.split('\n\n');
      buffer = blocks.pop(); // 最后一段可能不完整，留到下一轮

      for (const block of blocks) {
        const payload = parseSseBlock(block);
        if (!payload) continue;
        handleEvent(payload, view, () => { gotSources = true; });
      }
    }

    view.finish();
    if (!view.hasContent() && !gotSources) {
      view.setStage('（本次没有返回内容）');
    }
    setStatus('ok', '就绪');
  } catch (error) {
    console.error(error);
    view.setError(`出错了：${error.message}。请检查后端服务与 .env 配置后重试。`);
    setStatus('error', '请求失败');
  } finally {
    streaming = false;
    els.sendBtn.disabled = false;
    els.input.focus();
  }
}

/** 处理单条 SSE 事件。 */
function handleEvent(payload, view, markSources) {
  switch (payload.type) {
    case 'status':
      view.setStatus(payload.message || '处理中…');
      break;
    case 'sources':
      markSources();
      view.setSources(payload.sources);
      break;
    case 'token':
      view.appendToken(payload.content || '');
      break;
    case 'error':
      view.setError(payload.message || '服务异常');
      setStatus('error', '服务异常');
      break;
    case 'done':
      view.finish();
      break;
    default:
      break;
  }
}

/* ============================ 会话管理 ============================ */

/** 加载当前 thread 的历史消息。 */
async function loadHistory() {
  try {
    const response = await fetch(API.history(threadId));
    if (!response.ok) return;
    const data = await response.json();
    const messages = data.messages || [];
    if (!messages.length) return;

    hideEmptyState();
    for (const item of messages) {
      if (item.role === 'user') {
        addUserMessage(item.content);
      } else {
        const { bubble } = createRow('assistant');
        const body = document.createElement('div');
        body.className = 'content';
        body.innerHTML = renderMarkdown(item.content);
        bubble.appendChild(body);
      }
    }
    scrollToBottom(true);
  } catch (error) {
    console.warn('加载历史失败：', error);
  }
}

/** 开始新对话：清空历史并重新生成 thread_id。 */
async function startNewChat() {
  threadId = createThreadId();
  localStorage.setItem(STORAGE_KEY, threadId);
  els.messages.innerHTML = '';
  const placeholder = document.createElement('div');
  placeholder.className = 'empty-state';
  placeholder.id = 'empty-state';
  placeholder.innerHTML =
    '<h2>开始提问吧</h2>' +
    '<p>试着问：「什么是任务分解？」「RAG 的检索流程是怎样的？」</p>' +
    '<p class="hint">寒暄类问题（如「你好」）不会触发知识库检索。</p>';
  els.messages.appendChild(placeholder);
  els.emptyState = placeholder;
  setStatus('ok', '就绪');
  els.input.focus();
}

/* ============================ 文件上传 ============================ */

/** 上传文件并入库。 */
async function uploadFile(file) {
  const form = new FormData();
  form.append('file', file);
  setStatus('busy', `正在上传 ${file.name} …`);

  const { bubble } = createRow('assistant');
  bubble.appendChild(Object.assign(document.createElement('div'), {
    className: 'tool-line',
    textContent: `⏳ 正在处理《${file.name}》…`,
  }));

  try {
    const response = await fetch(API.upload, { method: 'POST', body: form });
    const data = await response.json();
    if (!response.ok || data.error) {
      throw new Error(data.error || `HTTP ${response.status}`);
    }
    bubble.innerHTML = '';
    bubble.appendChild(Object.assign(document.createElement('div'), {
      className: 'tool-line',
      textContent: `✅ ${data.message || '入库成功'}`,
    }));
    setStatus('ok', '就绪');
  } catch (error) {
    bubble.classList.add('error');
    bubble.textContent = `上传失败：${error.message}`;
    setStatus('error', '上传失败');
  }
}

/* ============================ 事件绑定 ============================ */

els.sendBtn.addEventListener('click', () => {
  const text = els.input.value.trim();
  if (!text || streaming) return;
  els.input.value = '';
  els.input.style.height = 'auto';
  sendMessage(text);
});

els.input.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    els.sendBtn.click();
  }
});

els.input.addEventListener('input', () => {
  els.input.style.height = 'auto';
  els.input.style.height = `${Math.min(els.input.scrollHeight, 160)}px`;
});

els.newChatBtn.addEventListener('click', startNewChat);

els.uploadBtn.addEventListener('click', () => els.fileInput.click());

els.fileInput.addEventListener('change', (event) => {
  const file = event.target.files && event.target.files[0];
  event.target.value = '';
  if (file) uploadFile(file);
});

/* ============================ 初始化 ============================ */
(async function init() {
  setStatus('ok', '就绪');
  await loadHistory();
  els.input.focus();
})();
