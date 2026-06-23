import { createRun, createSession, fetchReport, fetchRuns, fetchSessions, parseMarkdownSections, saveSession } from "./api.js";

let runs = [];
let conversations = loadConversations();
let sessionStore = "local";

const state = {
  activeConversationId: conversations[0]?.id || "",
  apiMode: "mock",
  isCreating: false,
  form: {
    topic: "",
    reportType: "industry-chain",
    depth: "standard",
    llm: true,
    visual: true,
    quality: true,
    filings: false,
    maxDocs: 80,
    topChunks: 60,
    companies: 6
  }
};

let forceScrollBottom = false;

const reportTypeLabels = {
  topic: "主题研究",
  company: "单公司深度",
  "industry-chain": "产业链瓶颈",
  daily: "每日扫描"
};

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
              <button class="history-item ${item.id === state.activeConversationId ? "active" : ""}" data-session="${item.id}">
                ${escapeHtml(item.title || "新研究")}
              </button>
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
            <div class="option-strip">
              <label>
                <span>类型</span>
                <select name="reportType">
                  ${option("topic", "主题研究", state.form.reportType)}
                  ${option("company", "单公司深度", state.form.reportType)}
                  ${option("industry-chain", "产业链瓶颈", state.form.reportType)}
                  ${option("daily", "每日扫描", state.form.reportType)}
                </select>
              </label>
              <label>
                <span>深度</span>
                <select name="depth">
                  ${option("quick", "快速", state.form.depth)}
                  ${option("standard", "标准", state.form.depth)}
                  ${option("deep", "深度", state.form.depth)}
                </select>
              </label>
              ${toggle("llm", "LLM", state.form.llm)}
              ${toggle("visual", "图谱", state.form.visual)}
              ${toggle("quality", "质检", state.form.quality)}
              ${toggle("filings", "公告", state.form.filings)}
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
      <article class="message user-message">
        <div class="message-body user-body">${escapeHtml(message.text)}</div>
      </article>
    `;
  }
  return `
    <article class="message assistant-message">
      <div class="assistant-avatar">CA</div>
      <div class="message-body assistant-body">
        ${message.status === "running" ? renderThinking(message) : renderAssistantAnswer(message)}
      </div>
    </article>
  `;
}

function renderThinking(message) {
  return `
    <div class="thinking">
      <div class="thinking-title">
        <span class="spinner"></span>
        <strong>正在研究这个问题</strong>
      </div>
      <p>${escapeHtml(message.stage || "任务已提交，正在准备数据。")}</p>
      <div class="progress-steps">
        ${["创建任务", "查询扩展", "数据抓取", "事件抽取", "产业链分析", "财务验证", "报告生成", "质量检查"]
          .map((step) => `<span>${escapeHtml(step)}</span>`)
          .join("")}
      </div>
      ${message.logTail ? `<pre class="log-tail">${escapeHtml(message.logTail)}</pre>` : ""}
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
    ${message.artifacts?.length ? `
      <div class="artifact-line">
        ${message.artifacts.map((artifact) => `
          <button data-run-id="${escapeAttr(message.runId)}" data-artifact="${escapeAttr(artifact)}">${escapeHtml(artifact)}</button>
        `).join("")}
      </div>
    ` : ""}
  `;
}

function renderParagraph(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  if (text.startsWith("|")) return `<pre>${escapeHtml(text)}</pre>`;
  return `<p>${escapeHtml(text)}</p>`;
}

function bindEvents() {
  document.querySelectorAll(".history-item[data-session]").forEach((button) => {
    button.addEventListener("click", () => {
      state.activeConversationId = button.dataset.session;
      state.form.topic = "";
      render();
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

  document.querySelectorAll("[data-artifact]").forEach((button) => {
    button.addEventListener("click", () => {
      if (state.apiMode !== "api") return;
      window.open(`/api/runs/${encodeURIComponent(button.dataset.runId)}/files/${encodeURIComponent(button.dataset.artifact)}`, "_blank");
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

  conversation.messages.push({role: "user", text: question});
  updateCurrentConversationTitle(question);

  const assistantMessage = {
    id: `assistant-${Date.now()}`,
    role: "assistant",
    status: "running",
    stage: "正在创建任务",
    logTail: "",
    runId: ""
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
    await pollRunUntilDone(result.run_id, assistantMessage.id);
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
      saveConversations();
      forceScrollBottom = false;
      render();

      if (runInfo.status === "completed") {
        const report = await fetchReport(runId, "markdown");
        message.status = "completed";
        message.statusText = "已完成";
        message.source = report.source || "";
        message.sections = parseMarkdownSections(report.content);
        message.artifacts = runInfo.artifacts || [];
        saveConversations();
        await refreshRunsSilently();
        render();
        return;
      }

      if (runInfo.status === "failed") {
        message.status = "failed";
        message.statusText = "失败";
        message.sections = [{title: "任务失败", body: [runInfo.log_tail || "请查看 run.log。"]}];
        saveConversations();
        render();
        return;
      }
    } catch (error) {
      message.stage = "等待后端响应";
      saveConversations();
      forceScrollBottom = false;
      render();
    }
  }
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
  const distanceFromBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
  return {
    top: scroller.scrollTop,
    wasNearBottom: distanceFromBottom < 80
  };
}

function restoreScrollState(scrollState) {
  const scroller = document.querySelector(".chat-scroll");
  if (!scroller) return;
  requestAnimationFrame(() => {
    if (forceScrollBottom || scrollState?.wasNearBottom) {
      scroller.scrollTop = scroller.scrollHeight;
      forceScrollBottom = false;
      return;
    }
    if (scrollState) scroller.scrollTop = Math.min(scrollState.top, scroller.scrollHeight);
  });
}

function option(value, label, selected) {
  return `<option value="${value}" ${value === selected ? "selected" : ""}>${label}</option>`;
}

function toggle(name, label, checked) {
  return `
    <label class="toggle">
      <input type="checkbox" name="${name}" ${checked ? "checked" : ""} />
      <span></span>
      ${label}
    </label>
  `;
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
  .then(render);

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
