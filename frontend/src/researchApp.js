import { cancelRun, createRun, createSession, deleteSession, fetchReport, fetchRuns, fetchSessions, parseMarkdownSections, saveSession } from "./api.js";

let runs = [];
// Conversations are owned by the signed-in account and must never fall back
// to browser-wide localStorage, which can leak a previous user's history.
let conversations = [];
let sessionStore = "api";

const state = {
  activeConversationId: conversations[0]?.id || "",
  apiMode: "mock",
  isCreating: false,
  sidebarCollapsed: localStorage.getItem("chainalpha-research-sidebar-collapsed") === "1",
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
const activeRunStreams = new Map();
const liveDraftScrollState = new Map();

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
    <div class="app-shell ${state.sidebarCollapsed ? "research-sidebar-collapsed" : ""}">
      <aside class="sidebar">
        <div class="sidebar-top">
          <div class="research-brand">
            <div class="logo">CA</div>
            <span><strong>ChainAlpha</strong><small>研投助手</small></span>
          </div>
          <button class="icon-button" id="research-collapse" title="收起侧边栏" aria-label="收起或展开侧边栏">☰</button>
        </div>

        <nav class="research-nav" aria-label="主导航">
          <a href="${researchAppBase()}/" data-research-route="/">⌂ <span>首页</span></a>
          <a href="${researchAppBase()}/five-good" data-research-route="/five-good">◆ <span>五好精选</span></a>
          <a href="${researchAppBase()}/sectors" data-research-route="/sectors">▦ <span>热门板块</span></a>
          <a href="${researchAppBase()}/market-daily" data-research-route="/market-daily">◫ <span>盘前 / 盘中 / 盘后</span></a>
          <a href="${researchAppBase()}/research" class="active">⌕ <span>我的研究</span></a>
        </nav>

        <button class="side-action active" id="new-chat">＋ 新研究</button>

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
      ${message.runId ? `<button type="button" class="end-run-button" data-end-run="${escapeAttr(message.id || "")}">结束对话</button>` : ""}
      ${renderTimeline(message)}
      ${renderPostCollectionProgress(message.timeline || [])}
      ${renderLiveDraft(message.liveOutput || "")}
    </div>
  `;
}

function renderTimeline(message) {
  const pages = collectVisitedPages(message.timeline || []);
  const progress = crawlerProgress(message.timeline || [], pages.length);
  // Collection details are internal implementation data. The chat shows only
  // the current research stage, not crawler names, source hosts, or URLs.
  const expanded = false;
  return `
    <div class="live-timeline">
      <div class="live-timeline-head">
        <div>
          <strong>${escapeHtml(progress.title)}</strong>
          <span>${escapeHtml(progress.detail)}</span>
        </div>
        <button class="timeline-toggle" style="display:none" data-toggle-timeline="${escapeAttr(message.id || "")}">
          ${expanded ? "收起网页" : "查看网页"}
        </button>
      </div>
      ${expanded ? `
        <div class="live-timeline-detail">
          <div class="live-timeline-subtitle">仅展示 MediaCrawler 已抓取的相关页面；搜索入口页不作为报告证据。</div>
          ${pages.length ? `
            <div class="event-docs visited-pages">
              ${pages.map((page) => `
                <div class="event-doc">
                  <span>${escapeHtml(page.source || page.host || "财经资讯")}</span>
                  <a href="${escapeAttr(page.url)}" target="_blank" rel="noreferrer">${escapeHtml(page.title || page.url)}</a>
                </div>
              `).join("")}
            </div>
          ` : `<div class="thinking-compact"><span class="pulse-dot"></span><span>${escapeHtml(progress.emptyDetail)}</span></div>`}
        </div>
      ` : `
        <div class="thinking-compact">
          <span class="pulse-dot"></span>
          <span>${escapeHtml(progress.compactDetail)}</span>
          ${""}
        </div>
      `}
    </div>
  `;
}

function crawlerProgress(timeline, pageCount) {
  const progressEvent = [...timeline].reverse().find((event) =>
    ["stage", "artifact", "model_stream", "model_stream_warning"].includes(event.type)
  );
  const collectionEvent = progressEvent?.type === "stage" && progressEvent.data?.index
    ? progressEvent
    : null;
  if (collectionEvent) {
    const index = Number(collectionEvent.data?.index || 0);
    const total = Number(collectionEvent.data?.total || 4);
    const completed = collectionEvent.data?.status === "completed";
    const stageLabel = String(collectionEvent.data?.stage_label || "相关公开信息");
    const stageGoal = String(collectionEvent.data?.stage_goal || "核查相关公开信息");
    const query = String(collectionEvent.data?.query || "");
    const progressLabel = `${stageLabel}（${index}/${total}）`;
    return {
      title: completed ? `${stageLabel}核查完成` : `正在核查${progressLabel}`,
      detail: collectionEvent.message || `${stageGoal}${query ? `：${query}` : ""}`,
      compactDetail: completed ? `${stageLabel}的候选信息已整理。` : `${stageGoal}${query ? `：${query}` : ""}`,
      emptyDetail: completed ? `正在整理${stageLabel}的候选信息。` : `${stageGoal}。`,
    };
  }
  if (progressEvent) {
    if (progressEvent.type === "model_stream") {
      const started = progressEvent.title === "模型开始输出";
      return {
        title: started ? "模型开始输出" : "模型输出完成",
        detail: started ? "正在将已核验信息整理为最终报告" : "正在整理报告结构并进行质量检查",
        compactDetail: started ? "正在将已核验信息整理为最终报告。" : "正在整理报告结构并进行质量检查。",
        emptyDetail: "报告草稿将实时显示在下方。",
      };
    }
    const title = String(progressEvent.title || "正在整理研究材料");
    const detail = String(progressEvent.message || "正在继续处理已获取的信息");
    return {
      title,
      detail,
      compactDetail: detail,
      emptyDetail: detail,
    };
  }
  const crawlerEvent = [...timeline].reverse().find((event) =>
    event.type === "crawler" || event.data?.action === "targeted_news_crawl"
  );
  if (crawlerEvent?.type === "crawler" && crawlerEvent.data?.status === "started") {
    const query = String(crawlerEvent.data?.query || crawlerEvent.message || "").replace(/^正在检索财经资讯：/, "");
    return {
      title: "正在通过 MediaCrawler 检索财经资讯",
      detail: query ? `检索关键词：${query}` : "正在连接已配置财经站点",
      compactDetail: "正在后台检索财经资讯，浏览器不会显示在桌面。",
      emptyDetail: "正在抓取，尚未返回结果。",
    };
  }
  if (crawlerEvent?.data?.action === "targeted_news_crawl") {
    const found = Number(crawlerEvent.data?.documents_found || 0);
    return {
      title: "MediaCrawler 检索完成",
      detail: found ? `已获得 ${found} 条候选资讯，正在继续研究。` : "本轮未获得可用资讯，正在转入下一项证据判断。",
      compactDetail: found ? `已获得 ${found} 条候选财经资讯。` : "本轮财经资讯未返回可用结果。",
      emptyDetail: found ? "结果正在整理为可访问网页。" : "本轮未获得可用网页结果。",
    };
  }
  return {
    title: "正在准备财经资讯检索",
    detail: pageCount ? `已获取 ${pageCount} 个相关页面` : "等待 Agent 决定下一步证据来源",
    compactDetail: "正在根据证据缺口安排财经资讯检索。",
    emptyDetail: "尚未获得可用网页结果。",
  };
}

function renderPostCollectionProgress(timeline) {
  const lastCollectionIndex = timeline.reduce((lastIndex, event, index) => (
    event.type === "stage" && event.data?.index && event.data?.status === "completed" ? index : lastIndex
  ), -1);
  if (lastCollectionIndex < 0) return "";
  const entries = timeline
    .slice(lastCollectionIndex + 1)
    .filter((event) => ["stage", "artifact", "model_stream", "model_stream_warning"].includes(event.type))
    .filter((event) => event.type !== "model_stream" || event.title !== "模型开始输出")
    .slice(-8);
  if (!entries.length) return "";
  return `
    <div class="research-progress-log" aria-label="研究处理进度">
      ${entries.map((event) => `
        <div class="research-progress-entry">
          <span>${escapeHtml(progressEntryTitle(event))}</span>
          <small>${escapeHtml(progressEntryDetail(event))}</small>
        </div>
      `).join("")}
    </div>
  `;
}

function progressEntryTitle(event) {
  if (event.type === "model_stream") {
    return event.title === "模型开始输出" ? "模型开始输出" : "模型输出完成";
  }
  if (event.type === "model_stream_warning") return "模型输出连接调整";
  return String(event.title || "研究处理中");
}

function progressEntryDetail(event) {
  if (event.type === "model_stream") {
    return event.title === "模型开始输出"
      ? "正在将已核验信息整理为最终报告"
      : "正在整理报告结构并进行质量检查";
  }
  if (event.type === "model_stream_warning") return "正在继续生成报告";
  return String(event.message || "");
}

function renderLiveDraft(liveOutput) {
  const text = String(liveOutput || "").trim();
  if (!text) return "";
  return `
    <div class="live-report-draft" aria-live="polite">
      <span>报告草稿正在生成</span>
      <pre>${escapeHtml(text)}</pre>
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
      updateResearchRoute(state.activeConversationId);
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
  document.querySelector("#new-chat").addEventListener("click", () => {
    startNewConversation();
  });
  document.querySelectorAll("[data-end-run]").forEach((button) => {
    button.addEventListener("click", () => endRunningConversation(button.dataset.endRun));
  });
  document.querySelector("#research-collapse")?.addEventListener("click", () => {
    state.sidebarCollapsed = !state.sidebarCollapsed;
    localStorage.setItem("chainalpha-research-sidebar-collapsed", state.sidebarCollapsed ? "1" : "0");
    render();
  });
  document.querySelectorAll("[data-research-route]").forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      history.pushState({}, "", `${researchAppBase()}${link.dataset.researchRoute}`);
      location.reload();
    });
  });

  bindTimelineButtons();
  bindCopyButtons();
}

async function deleteConversation(sessionId) {
  const conversation = conversations.find((item) => item.id === sessionId);
  if (!conversation) return;
  if (!window.confirm(`删除“${conversation.title || "新研究"}”吗？此操作不可恢复。`)) return;
  try {
    await deleteSession(sessionId);
    conversations = conversations.filter((item) => item.id !== sessionId);
    if (!conversations.length) {
      const session = await createSession("新研究");
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

  try {
    await ensureActiveConversation(question);
  } catch (error) {
    window.alert("无法创建专属会话，请重新登录后再试。");
    return;
  }
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

async function endRunningConversation(messageId) {
  const message = findMessage(messageId);
  if (!message || message.status !== "running" || !message.runId) return;
  if (!window.confirm("结束本次研究？未完成的报告将不会继续生成。")) return;

  try {
    await cancelRun(message.runId);
    message.status = "cancelled";
    message.statusText = "已结束";
    message.stage = "研究已由用户结束";
    message.sections = [{title: "研究已结束", body: ["本次研究已由用户主动结束。"]}];
    activeRunStreams.get(message.runId)?.();
    saveConversations();
    forceScrollBottom = true;
    render();
  } catch (error) {
    window.alert(`结束研究失败：${error.message || "请稍后重试"}`);
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
    activeRunStreams.set(runId, () => {
      if (settled) return;
      settled = true;
      source.close();
      activeRunStreams.delete(runId);
      resolve();
    });

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
        activeRunStreams.delete(runId);
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
        activeRunStreams.delete(runId);
        current.status = "failed";
        current.statusText = "失败";
        current.sections = [{title: "任务失败", body: [runInfo.log_tail || "请查看 run.log。"]}];
        saveConversations();
        updateAssistantMessage(current);
        resolve();
        return;
      }

      if (runInfo.status === "cancelled") {
        settled = true;
        source.close();
        activeRunStreams.delete(runId);
        current.status = "cancelled";
        current.statusText = "已结束";
        current.sections = [{title: "研究已结束", body: ["本次研究已由用户主动结束。"]}];
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
        activeRunStreams.delete(runId);
        pollRunUntilDone(runId, messageId).then(resolve);
        return;
      }
      settled = true;
      activeRunStreams.delete(runId);
      pollRunUntilDone(runId, messageId).then(resolve);
    };
  });
}

async function pollRunUntilDone(runId, messageId) {
  for (let attempt = 0; attempt < 240; attempt += 1) {
    await sleep(2000);
    const message = findMessage(messageId);
    if (!message) return;
    if (message.status === "cancelled") return;
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

      if (runInfo.status === "cancelled") {
        message.status = "cancelled";
        message.statusText = "已结束";
        message.sections = [{title: "研究已结束", body: ["本次研究已由用户主动结束。"]}];
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
  const previousDraft = element.querySelector(".live-report-draft pre");
  if (previousDraft) {
    liveDraftScrollState.set(message.id, {
      top: previousDraft.scrollTop,
      wasNearBottom: previousDraft.scrollHeight - previousDraft.scrollTop - previousDraft.clientHeight < 20,
    });
  }
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
    restoreLiveDraftScroll(message.id);
  });
}

function restoreLiveDraftScroll(messageId) {
  const saved = liveDraftScrollState.get(messageId);
  const draft = messageElement(messageId)?.querySelector(".live-report-draft pre");
  if (!draft || !saved) return;
  draft.scrollTop = saved.wasNearBottom
    ? draft.scrollHeight
    : Math.min(saved.top, draft.scrollHeight);
}

function messageElement(messageId) {
  return [...document.querySelectorAll("[data-message-id]")]
    .find((item) => item.dataset.messageId === messageId) || null;
}

async function ensureActiveConversation(question) {
  if (state.activeConversationId && activeConversation()) return;
  const conversation = await createSession(question.slice(0, 60) || "新研究");
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
  try {
    conversation = await createSession("新研究");
  } catch (error) {
    window.alert("新建会话失败，请确认登录状态和后端服务。");
    return;
  }
  conversations = [conversation, ...conversations.filter((item) => item.id !== conversation.id)];
  state.activeConversationId = conversation.id;
  updateResearchRoute(conversation.id);
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

function researchAppBase() {
  return location.pathname.startsWith("/app") ? "/app" : "";
}

function updateResearchRoute(sessionId) {
  if (!sessionId) return;
  history.replaceState({}, "", `${researchAppBase()}/research/${encodeURIComponent(sessionId)}`);
}

function sessionIdFromRoute() {
  const path = location.pathname.replace(/^\/app/, "");
  const match = path.match(/^\/research\/([^/]+)$/);
  return match ? decodeURIComponent(match[1]) : "";
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
  const response = await fetch(`${apiBase()}/api/runs/${encodeURIComponent(runId)}`, {credentials: "include"});
  if (!response.ok) throw new Error(`${response.status}`);
  return response.json();
}

function saveConversations() {
  const conversation = activeConversation();
  if (conversation) {
    saveSession(conversation).catch(() => {
      // Keep the page state intact but never create a shared local fallback.
      state.apiMode = "offline";
    });
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
      const routeSessionId = sessionIdFromRoute();
      if (routeSessionId && conversations.some((item) => item.id === routeSessionId)) {
        state.activeConversationId = routeSessionId;
      }
      if (!state.activeConversationId || !conversations.some((item) => item.id === state.activeConversationId)) {
        state.activeConversationId = conversations[0].id;
      }
      return;
    }
    const session = await createSession("新研究");
    conversations = [session];
    state.activeConversationId = session.id;
  } catch (error) {
    conversations = [];
    state.activeConversationId = "";
    state.apiMode = "offline";
  }
}
