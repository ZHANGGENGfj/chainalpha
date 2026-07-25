import { createRun, createSession, deleteSession, fetchReport, fetchRuns, fetchSessions, parseMarkdownSections, saveSession } from "./api.js";

let runs = [];
let conversations = loadConversations();
let sessionStore = "local";

const state = {
  activeConversationId: conversations[0]?.id || "",
  apiMode: "mock",
  isCreating: false,
  form: {
    topic: "",
    reportType: "company",
    depth: "standard",
    llm: true,
    visual: true,
    quality: true,
    filings: true,
    maxDocs: 80,
    topChunks: 60,
    companies: 6
  }
};

let forceScrollBottom = false;

function activeConversation() {
  return conversations.find((item) => item.id === state.activeConversationId) || null;
}

function activeMessages() {
  return activeConversation()?.messages || [];
}

function render() {
  const scrollState = captureScrollState();
  const messages = activeMessages();
  const showWelcome = messages.length === 0;

  document.querySelector("#app").innerHTML = `
    <div class="app-shell">
      <aside class="sidebar">
        <div class="sidebar-top">
          <div class="logo">CA</div>
          <button class="icon-button" title="收起侧边栏">☰</button>
        </div>

        <button class="side-action active" id="new-chat">＋ 新研究</button>
        <button class="side-action" id="refresh-runs">⌕ 同步报告</button>
        <button class="side-action">▣ 报告库</button>
        <button class="side-action">⚙ 设置</button>

        <div class="history-group">
          <div class="history-label">会话</div>
          <div class="history-list">
            ${conversations.map((item) => `
              <div class="history-entry ${item.id === state.activeConversationId ? "active" : ""}">
                <button class="history-item" data-session="${item.id}">${escapeHtml(item.title || "新研究")}</button>
                <button class="history-delete" data-delete-session="${item.id}" title="删除会话" aria-label="删除会话">×</button>
              </div>
            `).join("")}
          </div>
        </div>

        <div class="sidebar-foot">
          <strong>ChainAlpha</strong>
          <span>${state.apiMode === "api" ? "已连接本地后端" : "Demo 模式"}</span>
        </div>
      </aside>

      <main class="chat-main">
        <header class="topbar">
          <button class="model-button">ChainAlpha 研投助手⌄</button>
          <div class="topbar-actions">
            <button class="pill-button" id="open-api">接口</button>
            <button class="pill-button primary">本地版</button>
          </div>
        </header>

        <section class="chat-scroll ${showWelcome ? "empty" : ""}">
          ${showWelcome ? renderWelcome() : renderMessages(messages)}
        </section>

        <section class="composer-wrap">
          <form id="run-form" class="composer">
            <div class="composer-input-row">
              <button type="button" class="round-button" title="更多选项">＋</button>
              <textarea id="topic" name="topic" rows="1" placeholder="有问题，尽管问">${escapeHtml(state.form.topic)}</textarea>
              <button type="button" class="round-button" title="语音">⌕</button>
              <button type="button" class="send-button" id="create-run" ${state.isCreating ? "disabled" : ""}>
                ${state.isCreating ? "…" : "↑"}
              </button>
            </div>
          </form>
          <p class="disclaimer">ChainAlpha 可能会出错。重要投资判断请核验原始公告、财报和行情数据。</p>
        </section>
      </main>
    </div>
  `;

  bindEvents();
  autoSizeTextarea();
  restoreScrollState(scrollState);
}

function renderWelcome() {
  return `
    <div class="welcome">
      <h1>准备好了，随时开始</h1>
      <p>除非点击“新研究”或切换左侧会话，否则连续提问都会保留在当前聊天里。</p>
    </div>
  `;
}

function renderMessages(messages) {
  return `
    <div class="thread">
      ${messages.map((message) => renderMessage(message)).join("")}
    </div>
  `;
}

function renderMessage(message) {
  if (message.role === "user") {
    return `
      <article class="message user-message" data-message-id="${escapeAttr(message.id || "")}">
        <div class="message-body user-body">${escapeHtml(message.text)}</div>
        <button class="copy-message-button" data-copy-text="${escapeAttr(message.text || "")}" title="复制">复制</button>
      </article>
    `;
  }
  const copyText = assistantCopyText(message);
  return `
    <article class="message assistant-message" data-message-id="${escapeAttr(message.id || "")}">
      <div class="assistant-avatar">CA</div>
      <div class="message-body assistant-body">
        ${message.status === "running" ? renderThinking(message) : renderAssistantAnswer(message)}
      </div>
      <button class="copy-message-button" data-copy-text="${escapeAttr(copyText)}" title="复制">复制</button>
    </article>
  `;
}

function assistantCopyText(message) {
  if (message.status === "running") return "研究进行中，最终报告尚未生成。";
  if (message.sections?.length) {
    return message.sections
      .map((section) => [`# ${section.title}`, ...(section.body || [])].join("\n\n"))
      .join("\n\n");
  }
  return message.text || "";
}

function renderThinking(message) {
  return `
    <div class="thinking">
      <div class="thinking-title">
        <span class="spinner"></span>
        <strong>正在研究这个问题</strong>
      </div>
      <p>${escapeHtml(message.stage || "任务已提交，正在准备数据。")}</p>
      ${renderTimeline(message)}
    </div>
  `;
}

function renderTimeline(message) {
  const pages = collectVisitedPages(message.timeline || []);
  const expanded = Boolean(message.showTimeline);
  return `
    <div class="live-timeline">
      <div class="live-timeline-head">
        <div>
          <strong>正在检索公开网页</strong>
          <span>${pages.length ? `已获取 ${pages.length} 个相关页面` : "正在等待检索结果"}</span>
        </div>
        <button class="timeline-toggle" data-toggle-timeline="${escapeAttr(message.id || "")}">
          ${expanded ? "收起网页" : "查看网页"}
        </button>
      </div>
      ${expanded ? `
        <div class="live-timeline-detail">
          <div class="live-timeline-subtitle">仅展示已抓取的公开页面；搜索入口页不作为报告证据。</div>
          ${pages.length ? `
            <div class="event-docs visited-pages">
              ${pages.map((page) => `
                <div class="event-doc">
                  <span>${escapeHtml(page.source || page.host || "公开网页")}</span>
                  <a href="${escapeAttr(page.url)}" target="_blank" rel="noreferrer">${escapeHtml(page.title || page.url)}</a>
                </div>
              `).join("")}
            </div>
          ` : `<div class="thinking-compact"><span class="pulse-dot"></span><span>尚未获得可用网页结果。</span></div>`}
        </div>
      ` : `
        <div class="thinking-compact">
          <span class="pulse-dot"></span>
          <span>正在检索并核验公开资料。</span>
          ${pages.length ? `<span class="compact-sites">${pages.slice(0, 3).map((page) => escapeHtml(page.host || page.source)).join(" / ")}</span>` : ""}
        </div>
      `}
    </div>
  `;
}

function collectVisitedPages(timeline) {
  const pages = [];
  const seen = new Set();
  for (const event of timeline) {
    if (!['tool_result', 'crawler'].includes(event.type)) continue;
    const docs = event.data?.documents || event.data?.pages || [];
    for (const doc of docs) {
      const url = String(doc?.url || "").trim();
      if (!url || seen.has(url)) continue;
      seen.add(url);
      let host = "";
      try { host = new URL(url).host; } catch (_) { /* local evidence has no public URL */ }
      pages.push({
        url,
        host,
        title: String(doc.title || url),
        source: String(doc.source || doc.source_type || host),
      });
    }
  }
  return pages.slice(-16);
}

function bindTimelineButton(button) {
  button.addEventListener("click", () => {
    const message = findMessage(button.dataset.toggleTimeline);
    if (!message) return;
    message.showTimeline = !message.showTimeline;
    saveConversations();
    updateAssistantMessage(message, {follow: false});
  });
}

function bindTimelineButtons(root = document) {
  root.querySelectorAll("[data-toggle-timeline]").forEach((button) => {
    bindTimelineButton(button);
  });
}

function renderEventDocuments(event) {
  const docs = event.data?.documents || [];
  if (!docs.length) return "";
  return `
    <div class="event-docs">
      ${docs.slice(0, 3).map((doc) => `
        <div class="event-doc">
          <span>${escapeHtml(doc.source || doc.source_type || "source")}</span>
          ${doc.url ? `<a href="${escapeAttr(doc.url)}" target="_blank" rel="noreferrer">${escapeHtml(doc.title || doc.url)}</a>` : `<strong>${escapeHtml(doc.title || "证据")}</strong>`}
        </div>
      `).join("")}
    </div>
  `;
}

function eventTypeLabel(type) {
  if (type === "tool_plan") return "决策";
  if (type === "tool_result") return "检索";
  if (type === "tool_judge") return "判断";
  if (type === "model_output") return "模型";
  if (type === "crawler") return "爬取";
  if (type === "artifact") return "产物";
  if (type === "stage") return "阶段";
  return "事件";
}

function renderStream(message) {
  const lines = message.streamLines?.length ? message.streamLines : streamLinesFromLog(message.logTail || "");
  if (!lines.length) return "";
  return `
    <div class="stream-box">
      ${lines.slice(-18).map((line) => `<div class="stream-line">${escapeHtml(line)}</div>`).join("")}
      <div class="stream-line muted">正在持续接收运行日志<span class="cursor-dot"></span></div>
    </div>
  `;
}

function renderAssistantAnswer(message) {
  const sections = message.sections?.length ? message.sections : [
    {title: "回答", body: [message.text || "暂无回答。"]}
  ];
  return `
    <div class="run-note">
      <span>${escapeHtml(message.statusText || "已完成")}</span>
      ${message.source ? `<span>${escapeHtml(message.source)}</span>` : ""}
    </div>
    <div class="markdown-answer">
      ${sections.map((section) => `
        <section class="answer-section">
          <h2>${escapeHtml(section.title)}</h2>
          ${(section.body || []).map((paragraph) => renderParagraph(paragraph)).join("")}
        </section>
      `).join("")}
    </div>
  `;
}

function renderParagraph(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  if (isMarkdownTable(text)) return renderMarkdownTable(text);
  if (/^(?:[-*]\s+)/.test(text)) {
    const items = text
      .split(/\r?\n/)
      .map((line) => line.replace(/^(?:[-*]\s+)/, "").trim())
      .filter(Boolean);
    return `<ul>${items.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</ul>`;
  }
  return `<p>${renderInlineMarkdown(text)}</p>`;
}

function isMarkdownTable(text) {
  const lines = String(text || "").split(/\r?\n/).filter((line) => line.trim());
  return lines.length >= 2
    && lines[0].includes("|")
    && /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[1]);
}

function markdownTableCells(line) {
  return String(line || "")
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function renderMarkdownTable(text) {
  const lines = String(text || "").split(/\r?\n/).filter((line) => line.trim());
  const headers = markdownTableCells(lines[0]);
  const rows = lines.slice(2)
    .filter((line) => line.includes("|"))
    .map(markdownTableCells)
    .filter((cells) => cells.some(Boolean));
  if (!headers.length || !rows.length) return `<pre>${escapeHtml(text)}</pre>`;

  return `
    <div class="markdown-table-wrap">
      <table class="markdown-table">
        <thead><tr>${headers.map((cell) => `<th>${renderInlineMarkdown(cell)}</th>`).join("")}</tr></thead>
        <tbody>
          ${rows.map((cells) => `<tr>${headers.map((_, index) => `<td>${renderInlineMarkdown(cells[index] || "")}</td>`).join("")}</tr>`).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderInlineMarkdown(value) {
  let html = escapeHtml(value);
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_match, label, url) => (
    `<a href="${url}" target="_blank" rel="noreferrer">${label}</a>`
  ));
  return html.replace(/\n/g, "<br>");
}

function bindEvents() {
  document.querySelectorAll(".history-item[data-session]").forEach((button) => {
    button.addEventListener("click", () => {
      state.activeConversationId = button.dataset.session;
      state.form.topic = "";
      forceScrollBottom = true;
      render();
      scrollChatToBottom();
    });
  });
  document.querySelectorAll("[data-delete-session]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      deleteConversation(button.dataset.deleteSession);
    });
  });

  document.querySelector("#run-form").addEventListener("input", (event) => {
    const target = event.target;
    if (!target.name) return;
    if (target.type === "checkbox") {
      state.form[target.name] = target.checked;
    } else if (target.type === "number") {
      state.form[target.name] = Number(target.value);
    } else {
      state.form[target.name] = target.value;
    }
    if (target.name === "topic") autoSizeTextarea();
  });

  document.querySelector("#topic").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      createResearchRun();
    }
  });

  document.querySelector("#create-run").addEventListener("click", createResearchRun);
  document.querySelector("#refresh-runs").addEventListener("click", refreshRunsSilently);
  document.querySelector("#new-chat").addEventListener("click", () => {
    startNewConversation();
  });
  document.querySelector("#open-api").addEventListener("click", () => {
    window.alert("报告产物仍按 run_id 存储；左侧显示的是浏览器本地会话。");
  });

  bindTimelineButtons();
  bindCopyButtons();
}

async function deleteConversation(sessionId) {
  const conversation = conversations.find((item) => item.id === sessionId);
  if (!conversation) return;
  if (!window.confirm(`删除“${conversation.title || "新研究"}”吗？此操作不可恢复。`)) return;
  try {
    if (sessionStore === "api") await deleteSession(sessionId);
    conversations = conversations.filter((item) => item.id !== sessionId);
    if (!conversations.length) {
      const session = sessionStore === "api"
        ? await createSession("新研究")
        : createConversation("新研究");
      conversations = [session];
    }
    if (state.activeConversationId === sessionId) {
      state.activeConversationId = conversations[0].id;
    }
    saveConversations();
    forceScrollBottom = true;
    render();
    scrollChatToBottom();
  } catch (error) {
    window.alert("删除会话失败，请稍后重试。");
  }
}

function bindArtifactButton(button) {
  button.addEventListener("click", () => {
    if (state.apiMode !== "api") return;
    window.open(`/api/runs/${encodeURIComponent(button.dataset.runId)}/files/${encodeURIComponent(button.dataset.artifact)}`, "_blank");
  });
}

function bindCopyButtons(root = document) {
  root.querySelectorAll("[data-copy-text]").forEach((button) => {
    button.addEventListener("click", async () => {
      const text = button.dataset.copyText || "";
      if (!text) return;
      try {
        await navigator.clipboard.writeText(text);
        button.textContent = "已复制";
        setTimeout(() => {
          button.textContent = "复制";
        }, 1200);
      } catch (error) {
        const area = document.createElement("textarea");
        area.value = text;
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.appendChild(area);
        area.select();
        document.execCommand("copy");
        area.remove();
        button.textContent = "已复制";
        setTimeout(() => {
          button.textContent = "复制";
        }, 1200);
      }
    });
  });
}

async function createResearchRun() {
  const textarea = document.querySelector("#topic");
  const question = (textarea?.value || state.form.topic).trim();
  if (!question) {
    window.alert("先输入一个研究问题。");
    return;
  }

  ensureActiveConversation(question);
  const conversation = activeConversation();
  state.form.topic = "";
  if (textarea) {
    textarea.value = "";
    textarea.style.height = "auto";
  }

  conversation.messages.push({id: `user-${Date.now()}`, role: "user", text: question});
  updateCurrentConversationTitle(question);

  const assistantMessage = {
    id: `assistant-${Date.now()}`,
    role: "assistant",
    status: "running",
    stage: "正在创建任务",
    logTail: "",
    runId: "",
    showTimeline: false
  };
  conversation.messages.push(assistantMessage);
  saveConversations();

  state.isCreating = true;
  forceScrollBottom = true;
  render();

  if (state.apiMode !== "api") {
    assistantMessage.status = "completed";
    assistantMessage.statusText = "Demo 回答";
    assistantMessage.sections = [{title: "Demo", body: ["后端未连接，当前为本地演示。"]}];
    state.isCreating = false;
    saveConversations();
    render();
    return;
  }

  try {
    const payload = {...state.form, topic: question};
    const result = await createRun(payload, {
      sessionId: state.activeConversationId,
      assistantMessageId: assistantMessage.id
    });
    assistantMessage.runId = result.run_id;
    saveConversations();
    render();
    await streamRunUntilDone(result.run_id, assistantMessage.id);
  } catch (error) {
    assistantMessage.status = "failed";
    assistantMessage.statusText = "创建任务失败";
    assistantMessage.sections = [{title: "错误", body: [error.message]}];
    saveConversations();
  } finally {
    state.isCreating = false;
    render();
  }
}

function streamRunUntilDone(runId, messageId) {
  return new Promise((resolve) => {
    if (typeof EventSource === "undefined") {
      pollRunUntilDone(runId, messageId).then(resolve);
      return;
    }
    const message = findMessage(messageId);
    if (!message) {
      resolve();
      return;
    }
    let settled = false;
    let opened = false;
    const source = new EventSource(`${apiBase()}/api/runs/${encodeURIComponent(runId)}/stream`);

    source.onopen = () => {
      opened = true;
    };

    source.onmessage = async (event) => {
      const current = findMessage(messageId);
      if (!current || settled) return;
      let runInfo;
      try {
        runInfo = JSON.parse(event.data);
      } catch (error) {
        return;
      }
      current.runId = runId;
      current.stage = runInfo.stage || "运行中";
      current.logTail = runInfo.log_tail || "";
      current.streamLines = streamLinesFromLog(current.logTail);
      current.timeline = runInfo.timeline || [];
      current.liveOutput = runInfo.live_output || "";

      if (runInfo.status === "completed") {
        settled = true;
        source.close();
        const report = await fetchReport(runId, "markdown");
        current.status = "completed";
        current.statusText = "已完成";
        current.source = report.source || "";
        current.sections = parseMarkdownSections(report.content);
        current.artifacts = runInfo.artifacts || [];
        saveConversations();
        await refreshRunsSilently();
        updateAssistantMessage(current);
        resolve();
        return;
      }

      if (runInfo.status === "failed") {
        settled = true;
        source.close();
        current.status = "failed";
        current.statusText = "失败";
        current.sections = [{title: "任务失败", body: [runInfo.log_tail || "请查看 run.log。"]}];
        saveConversations();
        updateAssistantMessage(current);
        resolve();
        return;
      }

      current.status = runInfo.status || "running";
      saveConversations();
      updateAssistantMessage(current, {follow: false});
    };

    source.onerror = () => {
      source.close();
      if (settled) return;
      if (!opened) {
        pollRunUntilDone(runId, messageId).then(resolve);
        return;
      }
      settled = true;
      pollRunUntilDone(runId, messageId).then(resolve);
    };
  });
}

async function pollRunUntilDone(runId, messageId) {
  for (let attempt = 0; attempt < 240; attempt += 1) {
    await sleep(2000);
    const message = findMessage(messageId);
    if (!message) return;
    try {
      const runInfo = await fetchRunInfo(runId);
      message.runId = runId;
      message.status = runInfo.status || "running";
      message.stage = runInfo.stage || "运行中";
      message.logTail = runInfo.log_tail || "";
      message.streamLines = streamLinesFromLog(message.logTail);
      message.timeline = runInfo.timeline || [];
      message.liveOutput = runInfo.live_output || "";
      saveConversations();

      if (runInfo.status === "completed") {
        const report = await fetchReport(runId, "markdown");
        message.status = "completed";
        message.statusText = "已完成";
        message.source = report.source || "";
        message.sections = parseMarkdownSections(report.content);
        message.artifacts = runInfo.artifacts || [];
        saveConversations();
        await refreshRunsSilently();
        updateAssistantMessage(message);
        return;
      }

      if (runInfo.status === "failed") {
        message.status = "failed";
        message.statusText = "失败";
        message.sections = [{title: "任务失败", body: [runInfo.log_tail || "请查看 run.log。"]}];
        saveConversations();
        updateAssistantMessage(message);
        return;
      }

      updateAssistantMessage(message, {follow: false});
    } catch (error) {
      message.stage = "等待后端响应";
      saveConversations();
      updateAssistantMessage(message, {follow: false});
    }
  }
}

function updateAssistantMessage(message, {follow = false} = {}) {
  const element = messageElement(message.id);
  if (!element) {
    render();
    return;
  }
  const scroller = document.querySelector(".chat-scroll");
  const previousTop = scroller?.scrollTop || 0;
  const wasNearBottom = isNearBottom(scroller);
  element.outerHTML = renderMessage(message);
  const nextElement = messageElement(message.id);
  nextElement?.querySelectorAll("[data-artifact]").forEach(bindArtifactButton);
  if (nextElement) bindTimelineButtons(nextElement);
  if (nextElement) bindCopyButtons(nextElement);
  requestAnimationFrame(() => {
    const current = document.querySelector(".chat-scroll");
    if (!current) return;
    if (follow || wasNearBottom) {
      current.scrollTop = current.scrollHeight;
    } else {
      current.scrollTop = Math.min(previousTop, current.scrollHeight);
    }
  });
}

function messageElement(messageId) {
  return [...document.querySelectorAll("[data-message-id]")]
    .find((item) => item.dataset.messageId === messageId) || null;
}

function ensureActiveConversation(question) {
  if (state.activeConversationId && activeConversation()) return;
  const conversation = createConversation(question);
  conversations = [conversation, ...conversations];
  state.activeConversationId = conversation.id;
  saveConversations();
}

function createConversation(title = "新研究") {
  return {
    id: `session-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    title,
    createdAt: new Date().toISOString(),
    messages: []
  };
}

async function startNewConversation() {
  let conversation;
  if (sessionStore === "api") {
    try {
      conversation = await createSession("新研究");
    } catch (error) {
      conversation = createConversation("新研究");
      sessionStore = "local";
    }
  } else {
    conversation = createConversation("新研究");
  }
  conversations = [conversation, ...conversations.filter((item) => item.id !== conversation.id)];
  state.activeConversationId = conversation.id;
  state.form.topic = "";
  saveConversations();
  render();
  document.querySelector("#topic")?.focus();
}

function updateCurrentConversationTitle(question) {
  const conversation = activeConversation();
  if (!conversation) return;
  if (!conversation.title || conversation.title === "新研究") {
    conversation.title = question.slice(0, 60);
  }
}

function findMessage(messageId) {
  for (const conversation of conversations) {
    const message = conversation.messages.find((item) => item.id === messageId);
    if (message) return message;
  }
  return null;
}

async function refreshRunsSilently() {
  try {
    runs = await fetchRuns();
    state.apiMode = "api";
  } catch (error) {
    state.apiMode = "mock";
  }
}

async function fetchRunInfo(runId) {
  const response = await fetch(`${apiBase()}/api/runs/${encodeURIComponent(runId)}`);
  if (!response.ok) throw new Error(`${response.status}`);
  return response.json();
}

function loadConversations() {
  try {
    const raw = localStorage.getItem("chainalpha.conversations");
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch (error) {
    return [];
  }
}

function saveConversations() {
  try {
    localStorage.setItem("chainalpha.conversations", JSON.stringify(conversations.slice(0, 50)));
  } catch (error) {
    // Ignore localStorage quota or privacy-mode errors.
  }
  if (sessionStore === "api") {
    const conversation = activeConversation();
    if (conversation) {
      saveSession(conversation).catch(() => {
        sessionStore = "local";
      });
    }
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function apiBase() {
  if (window.CHAINALPHA_API_BASE) return window.CHAINALPHA_API_BASE;
  if (location.port === "5173" || location.port === "5174") return "http://127.0.0.1:8000";
  return "";
}

function autoSizeTextarea() {
  const textarea = document.querySelector("#topic");
  if (!textarea) return;
  textarea.style.height = "auto";
  textarea.style.height = `${Math.min(textarea.scrollHeight, 160)}px`;
}

function captureScrollState() {
  const scroller = document.querySelector(".chat-scroll");
  if (!scroller) return null;
  return {
    top: scroller.scrollTop,
    wasNearBottom: isNearBottom(scroller)
  };
}

function isNearBottom(scroller) {
  if (!scroller) return false;
  const distanceFromBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
  return distanceFromBottom < 120;
}

function restoreScrollState(scrollState) {
  const scroller = document.querySelector(".chat-scroll");
  if (!scroller) return;
  requestAnimationFrame(() => {
    if (forceScrollBottom || scrollState?.wasNearBottom) {
      scrollChatToBottom();
      forceScrollBottom = false;
      return;
    }
    if (scrollState) scroller.scrollTop = Math.min(scrollState.top, scroller.scrollHeight);
  });
}

function scrollChatToBottom() {
  const apply = () => {
    const scroller = document.querySelector(".chat-scroll");
    if (scroller) scroller.scrollTop = scroller.scrollHeight;
  };
  requestAnimationFrame(() => {
    apply();
    requestAnimationFrame(apply);
    setTimeout(apply, 80);
  });
}

function streamLinesFromLog(logTail) {
  return String(logTail || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .filter((line) => !line.startsWith("\r"))
    .filter((line) => !isNoisyProgressLine(line))
    .slice(-24);
}

function isNoisyProgressLine(line) {
  const normalized = String(line || "")
    .replace(/\x1b\[[0-9;?]*[A-Za-z]/g, "")
    .trim();
  return /^\d{1,3}%\|.*\|\s*\d+\s*\/\s*\d+\s*\[/.test(normalized)
    || /^\d{1,3}%\s+\S+\s+\d+\s*\/\s*\d+\s*\[/.test(normalized);
}

function inferProgressIndex(stage = "", logTail = "") {
  const text = `${stage}\n${logTail}`;
  if (/质量|Report Quality|report_quality/i.test(text)) return 7;
  if (/LLM|报告|Markdown|HTML|professional|Chain Bottleneck|Final/i.test(text)) return 6;
  if (/财务|financial|valuation|估值|cash/i.test(text)) return 5;
  if (/产业链|chain|bottleneck|visual|node/i.test(text)) return 4;
  if (/signal|事件|chunks|analyze/i.test(text)) return 3;
  if (/fetch|crawler|documents|MediaCrawler|抓取|资料|docs/i.test(text)) return 2;
  if (/Query expansion|关键词|扩展/i.test(text)) return 1;
  return 0;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, "&#096;");
}

render();
refreshRunsSilently()
  .then(loadSessionsFromApi)
  .then(() => {
    forceScrollBottom = true;
    render();
    scrollChatToBottom();
  });

async function loadSessionsFromApi() {
  try {
    const sessions = await fetchSessions();
    sessionStore = "api";
    if (sessions.length) {
      conversations = sessions;
      if (!state.activeConversationId || !conversations.some((item) => item.id === state.activeConversationId)) {
        state.activeConversationId = conversations[0].id;
      }
      return;
    }
    if (!conversations.length) {
      const session = await createSession("新研究");
      conversations = [session];
      state.activeConversationId = session.id;
    }
  } catch (error) {
    sessionStore = "local";
  }
}
