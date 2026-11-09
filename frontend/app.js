/**
 * CCF-A 论文发现助手 —— 前端逻辑（原生 JS，无框架、无构建）
 *
 * 后端接口契约见 API_DOC.md：
 *   POST /api/chat     SSE 流式问答（status / sources / token / done / error）
 *   POST /api/upload   上传文档并入库
 *   GET  /api/history  按 thread_id 拉取历史
 *   GET  /health       健康检查
 *
 * 纯前端增强（后端无感知，全部存于 localStorage）：
 *   tech_chat_thread_id  当前会话 ID（契约要求，保持不变）
 *   tech_chat_threads    本机会话列表 [{ id, title, updatedAt }]
 *   tech_chat_docs       本机上传记录 [{ name, chunks, uploadedAt }]
 *   tech_chat_theme      主题 'light' | 'dark'
 */

const API = {
  chat: '/api/chat',
  login: '/api/auth/login',
  logout: '/api/auth/logout',
  me: '/api/auth/me',
  upload: '/api/upload',
  health: '/health',
  history: (id) => `/api/history?thread_id=${encodeURIComponent(id)}`,
};

const KEYS = {
  thread: 'tech_chat_thread_id',
  threads: 'tech_chat_threads',
  docs: 'tech_chat_docs',
  theme: 'tech_chat_theme',
};

const MAX_THREADS = 50;
const MAX_DOCS = 30;
const INPUT_MAX_HEIGHT = 160;
const HEALTH_INTERVAL = 30000;

const $ = (id) => document.getElementById(id);

const els = {
  app: $('app'),
  messages: $('messages'),
  thread: $('thread'),
  input: $('input'),
  sendBtn: $('send-btn'),
  newChatBtn: $('new-chat-btn'),
  uploadBtn: $('upload-btn'),
  attachBtn: $('attach-btn'),
  exportBtn: $('export-btn'),
  fileInput: $('file-input'),
  statusDot: $('status-dot'),
  statusText: $('status-text'),
  threadList: $('thread-list'),
  threadTitle: $('thread-title'),
  docList: $('doc-list'),
  docCount: $('doc-count'),
  themeBtn: $('theme-btn'),
  sidebarBtn: $('sidebar-btn'),
  scrim: $('scrim'),
  jumpBtn: $('jump-btn'),
  dropOverlay: $('drop-overlay'),
  loginModal: $('login-modal'),
  loginForm: $('login-form'),
  loginUser: $('login-user'),
  loginPass: $('login-pass'),
  loginError: $('login-error'),
  loginSubmit: $('login-submit'),
  loginCancel: $('login-cancel'),
  userBadge: $('user-badge'),
  userName: $('user-name'),
  logoutBtn: $('logout-btn'),
  emptyTpl: $('empty-tpl'),
  toasts: $('toasts'),
};

const state = {
  threadId: localStorage.getItem(KEYS.thread) || createThreadId(),
  streaming: false,
  controller: null, // 当前流的 AbortController
  healthy: null, // null = 未知
  transcript: [], // 当前会话消息 [{ role, content, sources? }]，用于导出
  loadSeq: 0, // 防止切换会话时历史请求乱序
  uploadEnabled: true, // 由 /health 的 features.upload 决定；公网部署会关闭
  user: null, // 当前登录用户名；null 表示未登录
  pendingText: null, // 因未登录被拦下的那条消息，登录后自动发送
};
localStorage.setItem(KEYS.thread, state.threadId);

/* ============================ 通用工具 ============================ */

function createThreadId() {
  const rand = Math.random().toString(36).slice(2, 10).padEnd(8, '0');
  return `thread-${Date.now()}-${rand}`;
}

function readJson(key, fallback) {
  try {
    const value = JSON.parse(localStorage.getItem(key));
    return value ?? fallback;
  } catch (_) {
    return fallback;
  }
}

function writeJson(key, value) {
  localStorage.setItem(key, JSON.stringify(value));
}

function escapeHtml(text) {
  return String(text ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function icon(name, cls = '') {
  return `<svg class="icon ${cls}"><use href="#i-${name}"/></svg>`;
}

function el(tag, className, html) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (html !== undefined) node.innerHTML = html;
  return node;
}

function relativeTime(ts) {
  const diff = (Date.now() - ts) / 1000;
  if (diff < 60) return '刚刚';
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} 天前`;
  return new Date(ts).toLocaleDateString('zh-CN');
}

function toast(message, type = '') {
  const node = el('div', `toast ${type}`.trim());
  node.textContent = message;
  els.toasts.appendChild(node);
  setTimeout(() => {
    node.classList.add('out');
    node.addEventListener('animationend', () => node.remove(), { once: true });
  }, 2600);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (_) {
    const area = el('textarea');
    area.value = text;
    area.style.cssText = 'position:fixed;opacity:0';
    document.body.appendChild(area);
    area.select();
    document.execCommand('copy');
    area.remove();
  }
}

/** 复制后按钮短暂显示「已复制」。 */
function flashCopied(button) {
  const original = button.innerHTML;
  button.innerHTML = `${icon('check')}<span>已复制</span>`;
  setTimeout(() => { if (button.isConnected) button.innerHTML = original; }, 1500);
}

/* ============================ 状态指示灯 ============================ */

function setStatus(kind, text) {
  els.statusDot.className = `dot ${kind || ''}`.trim();
  els.statusDot.title = text;
  els.statusText.textContent = text;
}

/** 根据健康状态恢复指示灯（流式生成时由 busy 覆盖）。 */
function refreshStatus() {
  if (state.streaming) return setStatus('busy', '正在生成回答…');
  if (state.healthy === null) return setStatus('', '连接中…');
  return state.healthy ? setStatus('ok', '服务就绪') : setStatus('error', '无法连接服务');
}

/** 按服务端开放的功能显示或隐藏入口（目前只有文档上传）。 */
function applyFeatures(features) {
  state.uploadEnabled = features.upload !== false;
  for (const node of [els.uploadBtn, els.attachBtn, $('upload-hint')]) {
    if (node) node.hidden = !state.uploadEnabled;
  }
}

async function checkHealth() {
  try {
    const res = await fetch(API.health, { cache: 'no-store' });
    const data = await res.json();
    state.healthy = res.ok && data.status === 'ok';
    applyFeatures(data.features || {});
  } catch (_) {
    state.healthy = false;
  }
  refreshStatus();
}

/* ============================ Markdown 渲染 ============================ */
/*
 * 轻量渲染器：围栏代码块（带语言与复制按钮）、标题、有序/无序列表（支持缩进嵌套）、
 * 引用块、表格、分割线、加粗、斜体、删除线、行内代码、链接（仅 http/https/相对路径）。
 * 所有文本先 escapeHtml 再套标签，杜绝注入。
 */

const INLINE_TOKEN = '\u0000';

function renderInline(text) {
  const stash = [];
  const keep = (html) => `${INLINE_TOKEN}${stash.push(html) - 1}${INLINE_TOKEN}`;

  let out = escapeHtml(text.replaceAll(INLINE_TOKEN, ''));
  out = out.replace(/`([^`]+)`/g, (_, code) => keep(`<code>${code}</code>`));
  out = out.replace(/\[([^\]]+)\]\(((?:https?:\/\/|\/|#)[^\s)]*)\)/g,
    (_, label, url) => keep(`<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`));
  out = out
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/~~(.+?)~~/g, '<del>$1</del>')
    .replace(/(^|[^*\w])\*(?!\s)([^*]+?)\*(?!\*)/g, '$1<em>$2</em>');
  return out.replace(new RegExp(`${INLINE_TOKEN}(\\d+)${INLINE_TOKEN}`, 'g'), (_, i) => stash[i]);
}

function renderCodeBlock(code, lang) {
  return '<div class="code-block">'
    + `<div class="code-head"><span>${escapeHtml(lang || 'code')}</span>`
    + `<button class="copy-code" type="button">${icon('copy')}<span>复制</span></button></div>`
    + `<pre><code>${escapeHtml(code)}</code></pre></div>`;
}

const TABLE_SEP = /^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$/;

function splitRow(line) {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());
}

function renderTable(rows) {
  const [head, , ...body] = rows;
  const th = splitRow(head).map((c) => `<th>${renderInline(c)}</th>`).join('');
  const tb = body.map((r) => `<tr>${splitRow(r).map((c) => `<td>${renderInline(c)}</td>`).join('')}</tr>`).join('');
  return `<div class="table-wrap"><table><thead><tr>${th}</tr></thead><tbody>${tb}</tbody></table></div>`;
}

function renderMarkdown(source) {
  if (!source) return '';
  const lines = String(source).replace(/\r\n?/g, '\n').split('\n');
  let html = '';
  let para = [];
  const lists = []; // 栈：{ type, indent }

  const flushPara = () => {
    if (para.length) html += `<p>${para.map(renderInline).join('<br>')}</p>`;
    para = [];
  };
  const closeLists = (toIndent = -1) => {
    while (lists.length && lists[lists.length - 1].indent > toIndent) {
      html += `</li></${lists.pop().type}>`;
    }
  };
  const flushAll = () => { flushPara(); closeLists(); };

  for (let i = 0; i < lines.length; i += 1) {
    const raw = lines[i];
    const line = raw.trim();

    // 围栏代码块（未闭合时也渲染，适配流式输出）
    const fence = line.match(/^(`{3,}|~{3,})\s*([\w+#.-]*)/);
    if (fence) {
      flushAll();
      const marker = fence[1];
      const body = [];
      i += 1;
      while (i < lines.length && !lines[i].trim().startsWith(marker)) {
        body.push(lines[i]);
        i += 1;
      }
      html += renderCodeBlock(body.join('\n'), fence[2]);
      continue;
    }

    if (!line) { flushAll(); continue; }

    let m = line.match(/^(#{1,6})\s+(.*)$/);
    if (m) {
      flushAll();
      html += `<h${m[1].length}>${renderInline(m[2])}</h${m[1].length}>`;
      continue;
    }

    if (/^(-{3,}|\*{3,}|_{3,})$/.test(line)) { flushAll(); html += '<hr>'; continue; }

    if (line.startsWith('|') && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1].trim())) {
      flushAll();
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        rows.push(lines[i]);
        i += 1;
      }
      i -= 1;
      html += renderTable(rows);
      continue;
    }

    if (line.startsWith('>')) {
      flushAll();
      const quote = [];
      while (i < lines.length && lines[i].trim().startsWith('>')) {
        quote.push(lines[i].trim().replace(/^>\s?/, ''));
        i += 1;
      }
      i -= 1;
      html += `<blockquote>${renderMarkdown(quote.join('\n'))}</blockquote>`;
      continue;
    }

    m = raw.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
    if (m) {
      flushPara();
      const indent = m[1].replace(/\t/g, '    ').length;
      const type = /\d/.test(m[2]) ? 'ol' : 'ul';
      closeLists(indent);
      const top = lists[lists.length - 1];
      if (top && top.indent === indent && top.type !== type) {
        html += `</li></${lists.pop().type}>`;
      }
      const current = lists[lists.length - 1];
      if (!current || indent > current.indent) {
        const start = type === 'ol' ? parseInt(m[2], 10) : 1;
        html += type === 'ol' && start !== 1 ? `<ol start="${start}">` : `<${type}>`;
        lists.push({ type, indent });
      } else {
        html += '</li>';
      }
      html += `<li>${renderInline(m[3])}`;
      continue;
    }

    // 列表项的续行
    if (lists.length && /^\s/.test(raw)) { html += `<br>${renderInline(line)}`; continue; }

    closeLists();
    para.push(line);
  }
  flushAll();
  return html;
}

/* ============================ 滚动 ============================ */

function nearBottom() {
  const box = els.messages;
  return box.scrollHeight - box.scrollTop - box.clientHeight < 120;
}

function scrollToBottom(smooth = false) {
  els.messages.scrollTo({ top: els.messages.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
}

/** 先判断是否贴底，再执行 DOM 更新，最后按需跟随滚动。 */
function withAutoScroll(update) {
  const stick = nearBottom();
  update();
  if (stick) scrollToBottom();
}

/* ============================ 消息 DOM ============================ */

function showEmptyState() {
  els.thread.innerHTML = '';
  els.thread.appendChild(els.emptyTpl.content.cloneNode(true));
}

/** 本机列表里有该会话、但服务端没有历史（如旧版内存记忆在重启后被清空）。 */
function showMissingState() {
  const id = state.threadId;
  const box = el('div', 'empty-state missing',
    `<div class="empty-logo">${icon('alert')}</div>`
    + '<h2>这段对话的记录找不到了</h2>'
    + '<p>服务端没有这个会话的历史，可能是此前服务重启时记忆被清空。<br>'
    + '你仍可以在这里继续提问，或将它从列表中移除。</p>'
    + '<div class="missing-actions">'
    + `<button class="btn" type="button" data-act="remove">${icon('trash')}<span>从列表移除</span></button>`
    + `<button class="btn" type="button" data-act="new">${icon('plus')}<span>开始新对话</span></button>`
    + '</div>');
  box.id = 'empty-state';
  box.querySelector('[data-act="remove"]').addEventListener('click', () => removeThread(id));
  box.querySelector('[data-act="new"]').addEventListener('click', startNewChat);
  els.thread.innerHTML = '';
  els.thread.appendChild(box);
}

function hideEmptyState() {
  const empty = $('empty-state');
  if (empty) empty.remove();
}

function createRow(role) {
  hideEmptyState();
  const row = el('div', `row ${role}`);
  if (role === 'assistant') row.appendChild(el('div', 'avatar', icon('spark')));
  const msg = el('div', 'msg');
  const bubble = el('div', 'bubble');
  msg.appendChild(bubble);
  row.appendChild(msg);
  els.thread.appendChild(row);
  return { row, msg, bubble };
}

/** 消息下方的操作栏（复制 + 可选的附加信息）。 */
function addActions(msg, getText, meta) {
  const bar = el('div', 'msg-actions');
  const copyBtn = el('button', 'msg-action', `${icon('copy')}<span>复制</span>`);
  copyBtn.type = 'button';
  copyBtn.addEventListener('click', async () => {
    await copyText(getText());
    flashCopied(copyBtn);
  });
  bar.appendChild(copyBtn);
  if (meta) bar.appendChild(el('span', 'msg-action', escapeHtml(meta)));
  msg.appendChild(bar);
}

function addUserMessage(text) {
  const { msg, bubble } = createRow('user');
  bubble.textContent = text;
  addActions(msg, () => text);
}

function addAssistantStatic(content) {
  const { msg, bubble } = createRow('assistant');
  bubble.appendChild(el('div', 'content', renderMarkdown(content)));
  addActions(msg, () => content);
}

const INLINE_TAGS = new Set(['STRONG', 'EM', 'A', 'CODE', 'BR', 'DEL', 'SPAN', 'SVG', 'BUTTON']);

/** 找到正文最后一个块级元素，让打字光标紧跟在文字后面。 */
function cursorHost(root) {
  let node = root;
  for (;;) {
    const child = node.lastElementChild;
    if (!child) break;
    if (child.tagName === 'PRE') { node = child.firstElementChild || child; break; }
    if (INLINE_TAGS.has(child.tagName.toUpperCase())) break;
    node = child;
  }
  return node;
}

function renderSources(sources) {
  if (sources.every((s) => s.kind === 'paper')) return renderPapers(sources);
  const details = el('details', 'sources');
  const names = [...new Set(sources.map((s) => s.file_name || '未知文件'))];
  const chips = names.slice(0, 3).map((n) => `<span class="source-chip">${escapeHtml(n)}</span>`).join('')
    + (names.length > 3 ? `<span class="source-chip">+${names.length - 3}</span>` : '');
  details.innerHTML = `<summary>${icon('chevron', 'chev')}`
    + `<span class="sources-title">引用来源（${sources.length}）</span>`
    + `<span class="source-chips">${chips}</span></summary>`;

  const list = el('div', 'source-list');
  sources.forEach((item, index) => {
    const box = el('div', 'source-item');
    box.innerHTML = '<div class="source-head">'
      + `<span class="idx">${index + 1}</span>${icon('file')}`
      + `<span>${escapeHtml(item.file_name || '未知文件')}</span>`
      + `<span class="pos">chunk 起始位置 ${escapeHtml(item.start_index ?? 0)}</span></div>`;
    const preview = el('div', 'source-preview');
    preview.textContent = item.preview || '（无预览内容）';
    preview.title = '点击展开 / 收起';
    preview.addEventListener('click', () => preview.classList.toggle('expanded'));
    box.appendChild(preview);
    list.appendChild(box);
  });
  details.appendChild(list);
  return details;
}

/** 论文检索结果：默认展开的论文卡片列表（编号与回答中的 [Pn] 对应）。 */
function renderPapers(papers) {
  const details = el('details', 'sources papers');
  details.open = true;
  const venues = [...new Set(papers.map((p) => p.venue))];
  const chips = venues.slice(0, 4).map((v) => `<span class="source-chip">${escapeHtml(v)}</span>`).join('')
    + (venues.length > 4 ? `<span class="source-chip">+${venues.length - 4}</span>` : '');
  details.innerHTML = `<summary>${icon('chevron', 'chev')}`
    + `<span class="sources-title">检索到的论文（${papers.length}）</span>`
    + `<span class="source-chips">${chips}</span></summary>`;

  const list = el('div', 'source-list');
  papers.forEach((p, index) => {
    const card = el('div', 'source-item paper-item');
    const scholar = `https://scholar.google.com/scholar?q=${encodeURIComponent(`"${p.title}"`)}`;
    card.innerHTML = '<div class="paper-head">'
      + `<span class="idx">P${index + 1}</span><span class="paper-title"></span></div>`
      + '<div class="paper-meta">'
      + `<span class="badge venue">${escapeHtml(p.venue)}</span>`
      + `<span class="badge">${escapeHtml(p.year)}</span>`
      + `<span class="badge">${p.type === 'journal' ? '期刊' : '会议'}</span>`
      + `<span class="badge muted">${escapeHtml(p.category || '')}</span>`
      + `<a class="paper-link" href="${scholar}" target="_blank" rel="noopener noreferrer">搜索全文</a></div>`;
    card.querySelector('.paper-title').textContent = p.title;
    const preview = el('div', 'source-preview');
    preview.textContent = p.preview || '（暂无摘要）';
    preview.title = '点击展开 / 收起';
    preview.addEventListener('click', () => preview.classList.toggle('expanded'));
    card.appendChild(preview);
    list.appendChild(card);
  });
  details.appendChild(list);
  return details;
}

/** 创建流式回答的助手气泡，返回控制器。 */
function createAssistantMessage(record) {
  const { msg, bubble } = createRow('assistant');
  const startedAt = performance.now();

  const status = el('div', 'status',
    '<span class="typing"><i></i><i></i><i></i></span><span class="status-text">正在思考…</span>');
  const statusText = status.querySelector('.status-text');
  bubble.appendChild(status);

  const body = el('div', 'content');
  const cursor = el('span', 'cursor');
  let raw = '';
  let retrieved = false;
  let started = false;
  let finished = false;
  let frame = 0;

  const paint = () => {
    frame = 0;
    withAutoScroll(() => {
      body.innerHTML = renderMarkdown(raw);
      if (!finished) cursorHost(body).appendChild(cursor);
    });
  };

  return {
    setStatus(stage, text) {
      if (stage === 'retrieving') retrieved = true;
      if (!started) withAutoScroll(() => { statusText.textContent = text; });
    },
    appendToken(token) {
      if (!token) return;
      if (!started) {
        started = true;
        status.remove();
        if (retrieved) {
          bubble.appendChild(el('div', 'stage-steps',
            `<span class="stage-step">${icon('check')}已检索知识库</span>`));
        }
        bubble.appendChild(body);
      }
      raw += token;
      record.content = raw;
      if (!frame) frame = requestAnimationFrame(paint);
    },
    setSources(sources) {
      if (!sources || !sources.length) return;
      record.sources = sources;
      withAutoScroll(() => bubble.appendChild(renderSources(sources)));
    },
    setError(message) {
      withAutoScroll(() => {
        status.remove();
        const box = el('div', 'bubble error', `${icon('alert')}<span></span>`);
        box.querySelector('span').textContent = message;
        if (started) {
          bubble.appendChild(box);
        } else {
          bubble.className = box.className;
          bubble.innerHTML = box.innerHTML;
        }
      });
      this.finish({ silent: true });
    },
    finish({ note, silent } = {}) {
      if (finished) return;
      finished = true;
      if (frame) cancelAnimationFrame(frame);
      if (started) paint();
      cursor.remove();
      if (!started && !silent) {
        status.classList.add('idle');
        status.querySelector('.typing')?.remove();
        statusText.textContent = note || '（本次没有返回内容）';
      } else if (note) {
        bubble.appendChild(el('div', 'stage-steps', `<span class="stage-step">${escapeHtml(note)}</span>`));
      }
      if (started) {
        const seconds = ((performance.now() - startedAt) / 1000).toFixed(1);
        addActions(msg, () => raw, `用时 ${seconds}s`);
      }
    },
  };
}

/* ============================ 流式问答 ============================ */

function parseSseBlock(block) {
  const data = block
    .split('\n')
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).trim());
  if (!data.length) return null;
  try {
    return JSON.parse(data.join('\n'));
  } catch (err) {
    console.warn('SSE 数据解析失败：', err, block);
    return null;
  }
}

function setStreaming(on) {
  state.streaming = on;
  els.sendBtn.classList.toggle('stop', on);
  els.sendBtn.title = on ? '停止生成（Esc）' : '发送（Enter）';
  updateSendState();
  refreshStatus();
}

function updateSendState() {
  els.sendBtn.disabled = !state.streaming && !els.input.value.trim();
}

function stopStreaming() {
  if (state.controller) state.controller.abort();
}

/* ============================ 登录 ============================ */

/** 同步服务端登录状态，并更新侧栏用户显示。 */
async function refreshUser() {
  try {
    const res = await fetch(API.me, { cache: 'no-store' });
    const data = await res.json();
    state.user = data.logged_in ? data.username : null;
  } catch (_) {
    state.user = null;
  }
  renderUser();
  return state.user;
}

function renderUser() {
  const logged = Boolean(state.user);
  if (els.userBadge) els.userBadge.hidden = !logged;
  if (els.userName) els.userName.textContent = state.user || '';
}

function openLogin(prefillUser) {
  if (!els.loginModal) return;
  els.loginError.hidden = true;
  els.loginForm.reset();
  if (prefillUser) els.loginUser.value = prefillUser;
  els.loginModal.hidden = false;
  setTimeout(() => (els.loginUser.value ? els.loginPass : els.loginUser).focus(), 30);
}

function closeLogin() {
  if (els.loginModal) els.loginModal.hidden = true;
  state.pendingText = null;
}

async function doLogin(username, password) {
  els.loginSubmit.disabled = true;
  els.loginError.hidden = true;
  try {
    const res = await fetch(API.login, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = typeof data.detail === 'string' ? data.detail : '登录失败，请重试';
      throw new Error(detail);
    }
    state.user = data.username;
    renderUser();
    els.loginModal.hidden = true;
    const pending = state.pendingText;
    state.pendingText = null;
    toast(`已登录：${state.user}`);
    if (pending) sendMessage(pending);   // 把刚才被拦下的问题接着发出去
    return true;
  } catch (error) {
    els.loginError.textContent = error.message;
    els.loginError.hidden = false;
    els.loginPass.select();
    return false;
  } finally {
    els.loginSubmit.disabled = false;
  }
}

/** 需要登录才能继续的操作：未登录则记下待发送内容并弹框。 */
function ensureLogin(text) {
  if (state.user) return true;
  state.pendingText = text || null;
  openLogin();
  return false;
}

async function sendMessage(text) {
  if (state.streaming) return;
  const threadAtStart = state.threadId;
  const controller = new AbortController();
  state.controller = controller;
  setStreaming(true);

  addUserMessage(text);
  state.transcript.push({ role: 'user', content: text });
  const record = { role: 'assistant', content: '' };
  state.transcript.push(record);
  touchThread(threadAtStart, text);
  scrollToBottom(true);

  const view = createAssistantMessage(record);
  let failed = false;

  try {
    const res = await fetch(API.chat, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ thread_id: threadAtStart, message: text }),
      signal: controller.signal,
    });

    if (!res.ok || !res.body) {
      let detail = `请求失败（HTTP ${res.status}）`;
      try {
        const data = await res.json();
        detail = data.error || data.detail || detail;
        if (typeof detail !== 'string') detail = JSON.stringify(detail);
      } catch (_) { /* 非 JSON 响应 */ }
      if (res.status === 401) {
        // 会话过期或被踢下线：提示重新登录，并把这条问题留到登录后重发
        state.user = null;
        renderUser();
        state.pendingText = text;
        openLogin();
      }
      throw new Error(detail);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');
      const blocks = buffer.split('\n\n');
      buffer = blocks.pop(); // 末段可能不完整，留到下一轮

      for (const block of blocks) {
        const event = parseSseBlock(block);
        if (!event) continue;
        switch (event.type) {
          case 'status':
            view.setStatus(event.stage, event.message || '处理中…');
            break;
          case 'sources':
            view.setSources(event.sources);
            break;
          case 'token':
            view.appendToken(event.content || '');
            break;
          case 'error':
            failed = true;
            view.setError(event.message || '服务异常');
            break;
          case 'done':
            view.finish();
            break;
          default:
            break;
        }
      }
    }
    view.finish();
    if (!failed) state.healthy = true;
  } catch (error) {
    if (error.name === 'AbortError') {
      view.finish({ note: '已停止生成' });
    } else {
      console.error(error);
      failed = true;
      const offline = error instanceof TypeError;
      if (offline) state.healthy = false;
      view.setError(offline
        ? '无法连接后端服务，请确认服务已启动（python run.py）后重试。'
        : `出错了：${error.message}。请检查后端服务与 .env 配置后重试。`);
    }
  } finally {
    if (state.controller === controller) state.controller = null;
    setStreaming(false);
    if (failed) setStatus('error', '请求失败');
    if (state.threadId === threadAtStart) els.input.focus();
  }
}

function submitInput() {
  const text = els.input.value.trim();
  if (!text || state.streaming) return;
  if (!ensureLogin(text)) return;   // 未登录：弹登录框，登录后自动发送这条
  els.input.value = '';
  autoGrow();
  sendMessage(text);
}

/* ============================ 会话管理 ============================ */

function getThreads() {
  return readJson(KEYS.threads, []);
}

/** 记录 / 更新本机会话列表（标题取首条用户提问）。 */
function touchThread(id, firstText) {
  const threads = getThreads();
  let item = threads.find((t) => t.id === id);
  if (!item) {
    item = { id, title: (firstText || '新对话').replace(/\s+/g, ' ').slice(0, 40) };
    threads.unshift(item);
  }
  item.updatedAt = Date.now();
  threads.sort((a, b) => b.updatedAt - a.updatedAt);
  writeJson(KEYS.threads, threads.slice(0, MAX_THREADS));
  renderThreads();
}

function renderThreads() {
  const threads = getThreads();
  const current = threads.find((t) => t.id === state.threadId);
  els.threadTitle.textContent = current ? current.title : '新对话';
  els.threadList.innerHTML = '';
  if (!threads.length) {
    els.threadList.appendChild(el('li', 'side-empty', '还没有会话记录'));
    return;
  }
  for (const t of threads) {
    const li = el('li', `thread-item${t.id === state.threadId ? ' active' : ''}`);
    li.title = `${t.title}\n${relativeTime(t.updatedAt)}`;
    li.innerHTML = `${icon('chat')}<span class="name"></span>`
      + `<button class="del" type="button" title="从列表移除">${icon('trash')}</button>`;
    li.querySelector('.name').textContent = t.title;
    li.addEventListener('click', () => switchThread(t.id));
    li.querySelector('.del').addEventListener('click', (e) => {
      e.stopPropagation();
      removeThread(t.id);
    });
    els.threadList.appendChild(li);
  }
}

function removeThread(id) {
  writeJson(KEYS.threads, getThreads().filter((t) => t.id !== id));
  if (id === state.threadId) {
    startNewChat();
  } else {
    renderThreads();
  }
  toast('已从本机列表移除');
}

async function loadHistory() {
  const seq = ++state.loadSeq;
  const id = state.threadId;
  try {
    const res = await fetch(API.history(id));
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (seq !== state.loadSeq) return; // 已切换到别的会话
    const messages = (data.messages || []).filter((m) => m.content);
    state.transcript = messages.map((m) => ({ role: m.role, content: m.content }));
    if (!messages.length) {
      if (getThreads().some((t) => t.id === id)) showMissingState();
      else showEmptyState();
      return;
    }

    els.thread.innerHTML = '';
    for (const item of messages) {
      if (item.role === 'user') addUserMessage(item.content);
      else addAssistantStatic(item.content);
    }
    const firstUser = messages.find((m) => m.role === 'user');
    if (!getThreads().some((t) => t.id === id)) touchThread(id, firstUser && firstUser.content);
    scrollToBottom();
  } catch (error) {
    if (seq !== state.loadSeq) return;
    console.warn('加载历史失败：', error);
    showEmptyState();
    toast('历史记录加载失败', 'error');
  }
}

function setThread(id) {
  state.threadId = id;
  state.transcript = [];
  localStorage.setItem(KEYS.thread, id);
  renderThreads();
  closeSidebar();
}

function startNewChat() {
  stopStreaming();
  state.loadSeq += 1;
  setThread(createThreadId());
  showEmptyState();
  els.input.focus();
}

function switchThread(id) {
  if (id === state.threadId) { closeSidebar(); return; }
  stopStreaming();
  setThread(id);
  els.thread.innerHTML = '';
  loadHistory();
}

/* ============================ 导出 ============================ */

function exportChat() {
  const items = state.transcript.filter((m) => m.content);
  if (!items.length) { toast('当前对话还没有内容'); return; }

  const title = els.threadTitle.textContent;
  let md = `# ${title}\n\n> 导出时间：${new Date().toLocaleString('zh-CN')}　会话 ID：\`${state.threadId}\`\n\n`;
  for (const m of items) {
    md += m.role === 'user' ? '## 🙋 提问\n\n' : '## 🤖 回答\n\n';
    md += `${m.content.trim()}\n\n`;
    if (m.sources && m.sources.length) {
      md += '**引用来源**\n\n';
      m.sources.forEach((s, i) => {
        md += s.kind === 'paper'
          ? `${i + 1}. [P${i + 1}] ${s.title}（${s.venue} ${s.year}）\n`
          : `${i + 1}. ${s.file_name || '未知文件'}（chunk 起始位置 ${s.start_index ?? 0}）\n`;
      });
      md += '\n';
    }
  }
  const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
  const a = el('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${title.replace(/[\\/:*?"<>|]/g, '_').slice(0, 40) || '对话'}.md`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  toast('已导出为 Markdown');
}

/* ============================ 上传 ============================ */

function renderDocs() {
  const docs = readJson(KEYS.docs, []);
  els.docCount.textContent = docs.length;
  els.docList.innerHTML = '';
  if (!docs.length) {
    els.docList.appendChild(el('li', 'side-empty', '上传后的文档会显示在这里'));
    return;
  }
  for (const d of docs) {
    const li = el('li', 'doc-item', `${icon('file')}<span class="name"></span><span class="meta"></span>`);
    li.querySelector('.name').textContent = d.name;
    li.querySelector('.meta').textContent = `${d.chunks ?? '?'} 片段`;
    li.title = `${d.name} · ${relativeTime(d.uploadedAt)}入库`;
    els.docList.appendChild(li);
  }
}

function rememberDoc(name, chunks) {
  const docs = readJson(KEYS.docs, []).filter((d) => d.name !== name);
  docs.unshift({ name, chunks, uploadedAt: Date.now() });
  writeJson(KEYS.docs, docs.slice(0, MAX_DOCS));
  renderDocs();
}

async function uploadFile(file) {
  const { bubble } = createRow('system');
  bubble.className = 'notice';
  bubble.innerHTML = '<span class="spinner"></span><span></span>';
  bubble.lastChild.textContent = `正在处理《${file.name}》…`;
  scrollToBottom(true);

  const form = new FormData();
  form.append('file', file);
  try {
    const res = await fetch(API.upload, { method: 'POST', body: form });
    let data = {};
    try { data = await res.json(); } catch (_) { /* 非 JSON */ }
    if (!res.ok || data.error) throw new Error(data.error || data.detail || `HTTP ${res.status}`);

    bubble.className = 'notice ok';
    bubble.innerHTML = `${icon('check')}<span></span>`;
    bubble.lastChild.textContent = data.message || `《${file.name}》已入库`;
    rememberDoc(data.file_name || file.name, data.chunks);
    toast(`《${data.file_name || file.name}》入库成功`);
  } catch (error) {
    const message = error instanceof TypeError ? '无法连接后端服务' : error.message;
    bubble.className = 'notice error';
    bubble.innerHTML = `${icon('alert')}<span></span>`;
    bubble.lastChild.textContent = message.startsWith('上传失败') ? message : `上传失败：${message}`;
    toast('上传失败', 'error');
  }
}

async function uploadFiles(files) {
  for (const file of files) await uploadFile(file); // 逐个上传，避免并发写库
}

/* ============================ 主题 / 侧栏 ============================ */

const THEME_MASK_SOLID = 0.72; // 与 style.css 中遮罩渐变的实心比例保持一致
let themeSwitching = false;

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem(KEYS.theme, theme);
}

/**
 * 切换主题：变亮时亮色从按钮处像阳光一样扩散到全屏；变暗时亮色从全屏收回到按钮。
 * 依赖 View Transitions API；不支持或用户开启「减少动态效果」时直接切换。
 */
function toggleTheme() {
  if (themeSwitching) return;
  const root = document.documentElement;
  const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!document.startViewTransition || reduceMotion) {
    applyTheme(next);
    return;
  }

  const rect = els.themeBtn.getBoundingClientRect();
  const x = rect.left + rect.width / 2;
  const y = rect.top + rect.height / 2;
  const radius = Math.hypot(Math.max(x, window.innerWidth - x), Math.max(y, window.innerHeight - y));
  const size = (radius * 2) / THEME_MASK_SOLID; // 保证实心区域能盖住最远的角
  const toLight = next === 'light';
  const directionClass = toLight ? 'to-light' : 'to-dark';

  themeSwitching = true;
  root.classList.add('theme-switching', directionClass);
  const transition = document.startViewTransition(() => applyTheme(next));

  transition.ready.then(() => {
    const collapsed = { maskSize: '0px 0px', maskPosition: `${x}px ${y}px` };
    const expanded = { maskSize: `${size}px ${size}px`, maskPosition: `${x - size / 2}px ${y - size / 2}px` };
    root.animate(toLight ? [collapsed, expanded] : [expanded, collapsed], {
      duration: toLight ? 500 : 300,
      easing: toLight ? 'cubic-bezier(0.25, 0.8, 0.3, 1)' : 'cubic-bezier(0.65, 0, 0.35, 1)',
      fill: 'forwards',
      pseudoElement: toLight ? '::view-transition-new(root)' : '::view-transition-old(root)',
    });
  }).catch(() => { /* 过渡被跳过时主题已直接生效 */ });

  transition.finished.finally(() => {
    root.classList.remove('theme-switching', directionClass);
    themeSwitching = false;
  });
}

function closeSidebar() {
  els.app.classList.remove('sidebar-open');
}

/* ============================ 事件绑定 ============================ */

function autoGrow() {
  els.input.style.height = 'auto';
  els.input.style.height = `${Math.min(els.input.scrollHeight, INPUT_MAX_HEIGHT)}px`;
  els.input.style.overflowY = els.input.scrollHeight > INPUT_MAX_HEIGHT ? 'auto' : 'hidden';
  updateSendState();
}

els.sendBtn.addEventListener('click', () => {
  if (state.streaming) stopStreaming();
  else submitInput();
});

els.input.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    submitInput();
  }
});
els.input.addEventListener('input', autoGrow);

els.newChatBtn.addEventListener('click', startNewChat);
els.uploadBtn.addEventListener('click', () => els.fileInput.click());
els.attachBtn.addEventListener('click', () => els.fileInput.click());
els.exportBtn.addEventListener('click', exportChat);
els.themeBtn.addEventListener('click', toggleTheme);
els.sidebarBtn.addEventListener('click', () => els.app.classList.add('sidebar-open'));
els.scrim.addEventListener('click', closeSidebar);
els.jumpBtn.addEventListener('click', () => scrollToBottom(true));

// 登录弹窗
els.loginForm.addEventListener('submit', (event) => {
  event.preventDefault();
  const username = els.loginUser.value.trim();
  const password = els.loginPass.value;
  if (!username || !password) return;
  doLogin(username, password);
});
els.loginCancel.addEventListener('click', closeLogin);
els.loginModal.addEventListener('click', (event) => {
  if (event.target === els.loginModal) closeLogin();   // 点遮罩关闭
});
els.logoutBtn.addEventListener('click', async () => {
  await fetch(API.logout, { method: 'POST' }).catch(() => {});
  state.user = null;
  renderUser();
  toast('已退出登录');
});

els.fileInput.addEventListener('change', (event) => {
  const files = [...(event.target.files || [])];
  event.target.value = '';
  if (files.length) uploadFiles(files);
});

els.messages.addEventListener('scroll', () => {
  els.jumpBtn.classList.toggle('show', !nearBottom());
}, { passive: true });

// 事件委托：代码块复制、空状态推荐问题
els.messages.addEventListener('click', async (event) => {
  const copyBtn = event.target.closest('.copy-code');
  if (copyBtn) {
    const code = copyBtn.closest('.code-block').querySelector('pre code');
    await copyText(code.textContent.replace(/\n$/, ''));
    flashCopied(copyBtn);
    return;
  }
  const suggestion = event.target.closest('.suggestion');
  if (suggestion && !state.streaming) sendMessage(suggestion.dataset.q);
});

document.addEventListener('keydown', (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault();
    startNewChat();
  } else if (event.key === 'Escape') {
    if (els.loginModal && !els.loginModal.hidden) closeLogin();
    else if (state.streaming) stopStreaming();
    closeSidebar();
  }
});

// 拖拽上传
let dragDepth = 0;
const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes('Files');
window.addEventListener('dragenter', (e) => {
  if (!hasFiles(e) || !state.uploadEnabled) return;
  e.preventDefault();
  dragDepth += 1;
  els.dropOverlay.classList.add('show');
});
window.addEventListener('dragover', (e) => { if (hasFiles(e)) e.preventDefault(); });
window.addEventListener('dragleave', (e) => {
  if (!hasFiles(e)) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) els.dropOverlay.classList.remove('show');
});
window.addEventListener('drop', (e) => {
  if (!hasFiles(e)) return;
  if (!state.uploadEnabled) { e.preventDefault(); return; }
  e.preventDefault();
  dragDepth = 0;
  els.dropOverlay.classList.remove('show');
  const files = [...e.dataTransfer.files];
  if (files.length) uploadFiles(files);
});

/* ============================ 初始化 ============================ */
(async function init() {
  renderThreads();
  renderDocs();
  refreshStatus();
  await refreshUser();   // 先确定登录状态，决定侧栏是否显示用户名
  checkHealth();
  setInterval(() => { if (!state.streaming) checkHealth(); }, HEALTH_INTERVAL);
  await loadHistory();
  els.input.focus();
})();
