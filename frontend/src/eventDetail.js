import {EVENT_CATEGORY_LABELS, EVENT_STATUS_LABELS, IMPACT_LABELS} from "./eventTypes.js";
import {formatClock, icon} from "./eventComponents.js";

export function renderEventDetail(event, timeline, {dataMode = "api", followed = false} = {}) {
  return `
    <main class="event-detail-page">
      <div class="detail-topbar">
        <a href="/" data-route="/" class="detail-back">${icon("arrow")} 返回事件首页</a>
        <button class="soft-button ${followed ? "selected" : ""}" data-follow-event="${escapeAttr(event.id)}">
          ${icon("star")} ${followed ? "已关注" : "关注事件"}
        </button>
      </div>
      ${dataMode === "mock" ? `<div class="detail-demo-banner">当前详情来自显式启用的演示数据。</div>` : ""}
      <header class="detail-hero">
        <div class="detail-eyebrow">
          ${["hybrid", "llm"].includes(event.analysisMethod)
            ? `<span class="detail-ai-badge" title="${escapeAttr(`${event.analysisProvider || "AI"} / ${event.analysisModel || "model"}`)}">AI 分析</span>`
            : ""}
          <span>${EVENT_CATEGORY_LABELS[event.category] || "事件"}</span>
          <span class="detail-status">${EVENT_STATUS_LABELS[event.status] || event.status}</span>
          <span class="detail-impact ${event.impactDirection}">${IMPACT_LABELS[event.impactDirection] || "观察"}</span>
        </div>
        <h1>${escapeHtml(event.title)}</h1>
        <div class="detail-metrics">
          ${event.llmConfidenceScore == null
            ? ""
            : metric("AI 分析置信度", `${event.llmConfidenceScore.toFixed(0)}/100`)}
          ${metric("重要性", `${event.importanceScore.toFixed(0)}/100`)}
          ${metric("可信度", `${event.confidenceScore.toFixed(0)}/100`)}
          ${metric("首次发现", formatDateTime(event.firstSeenAt))}
          ${metric("最后更新", formatDateTime(event.updatedAt))}
          ${metric("影响周期", horizonLabel(event.impactHorizon))}
        </div>
      </header>

      <div class="detail-layout">
        <div class="detail-main">
          ${semanticSection("事实", "已确认事实", event.factSummary, "fact")}
          ${semanticSection("直接影响", "直接影响", event.directImpact, "direct")}
          ${semanticSection("分析判断", "推导影响", event.inferredImpact, "analysis")}
          ${pendingSection(event.pendingVerification)}
          <section class="detail-section">
            <div class="detail-section-heading"><span>进展</span><h2>事件进展时间线</h2></div>
            ${renderTimeline(timeline)}
          </section>
          <section class="detail-section">
            <div class="detail-section-heading"><span>来源</span><h2>可追溯信息来源</h2></div>
            ${renderSources(event.sources || [])}
          </section>
        </div>
        <aside class="detail-rail">
          ${tagPanel("影响板块", event.sectors)}
          ${tagPanel("相关公司", event.companies)}
          ${tagPanel("相关概念", event.concepts)}
          <section class="detail-side-panel risk">
            <h3>风险提示</h3>
            <p>${escapeHtml(event.riskNote || "请以官方公告和后续披露为准。")}</p>
          </section>
        </aside>
      </div>
    </main>
  `;
}

export function renderDetailSkeleton() {
  return `
    <main class="event-detail-page">
      <div class="skeleton skeleton-detail-title"></div>
      <div class="skeleton skeleton-detail-hero"></div>
      <div class="detail-layout">
        <div class="skeleton skeleton-detail-body"></div>
        <div class="skeleton skeleton-detail-side"></div>
      </div>
    </main>
  `;
}

export function renderDetailError(message, status = 0) {
  return `
    <main class="dashboard-state">
      <span class="state-icon">!</span>
      <h2>${status === 404 ? "事件不存在或已移除" : "事件详情暂时无法加载"}</h2>
      <p>${escapeHtml(message || "请稍后重试。")}</p>
      <div class="detail-error-actions">
        <a href="/" data-route="/" class="primary-button">返回首页</a>
        ${status === 404 ? "" : `<button class="soft-button" data-retry-detail>${icon("refresh")} 重新加载</button>`}
      </div>
    </main>
  `;
}

function semanticSection(label, title, content, tone) {
  return `
    <section class="detail-section semantic ${tone}">
      <div class="detail-section-heading"><span>${label}</span><h2>${title}</h2></div>
      <p>${escapeHtml(content || "暂时无法判断")}</p>
    </section>
  `;
}

function pendingSection(items) {
  return `
    <section class="detail-section semantic pending">
      <div class="detail-section-heading"><span>待验证</span><h2>待验证信息</h2></div>
      ${items?.length
        ? `<ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
        : `<p>当前没有额外待验证信息。</p>`}
    </section>
  `;
}

function renderTimeline(items) {
  if (!items.length) return `<p class="detail-empty">该事件尚无进展记录。</p>`;
  return `
    <div class="detail-timeline">
      ${items.map((item) => `
        <article>
          <div class="timeline-marker"></div>
          <div>
            <div class="timeline-title"><strong>${escapeHtml(item.title)}</strong><time>${formatDateTime(item.occurredAt)}</time></div>
            <p>${escapeHtml(item.summary)}</p>
            <span>${EVENT_STATUS_LABELS[item.status] || item.status || "未标记"}</span>
          </div>
        </article>
      `).join("")}
    </div>
  `;
}

function renderSources(items) {
  if (!items.length) return `<p class="detail-empty">当前事件尚未附带可公开访问的来源。</p>`;
  return `
    <div class="detail-source-list">
      ${items.map((source) => `
        <article>
          <div>
            <span>${escapeHtml(source.publisher)}</span>
            <span>${sourceTypeLabel(source.sourceType)}</span>
            ${source.isPrimarySource ? `<span class="primary-source">一手来源</span>` : ""}
          </div>
          <h3>${escapeHtml(source.title)}</h3>
          <p>${escapeHtml(source.summary || "未提供摘要")}</p>
          <footer>
            <time>${source.timeMissing ? "发布时间缺失" : formatDateTime(source.publishedAt)}</time>
            ${source.url
              ? `<a href="${escapeAttr(source.url)}" target="_blank" rel="noopener noreferrer">查看原文 ${icon("arrow")}</a>`
              : `<span>无可用原文链接</span>`}
          </footer>
        </article>
      `).join("")}
    </div>
  `;
}

function tagPanel(title, items) {
  return `
    <section class="detail-side-panel">
      <h3>${title}</h3>
      <div class="detail-tags">${items?.length
        ? items.map((item) => `<span>${escapeHtml(item)}</span>`).join("")
        : `<small>暂无结构化数据</small>`}
      </div>
    </section>
  `;
}

function metric(label, value) {
  return `<div><span>${label}</span><strong>${escapeHtml(value)}</strong></div>`;
}

function sourceTypeLabel(value) {
  return {news: "新闻", announcement: "公告", research: "研报", social: "社交媒体"}[value] || "其他";
}

function horizonLabel(value) {
  return {
    intraday: "日内",
    short_term: "短期",
    medium_term: "中期",
    long_term: "长期",
  }[value] || "暂未判断";
}

function formatDateTime(value) {
  if (!value) return "时间未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(date);
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
