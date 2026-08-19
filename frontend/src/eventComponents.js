import {EVENT_CATEGORY_LABELS, EVENT_STATUS_LABELS, IMPACT_LABELS} from "./eventTypes.js";

export function icon(name) {
  const icons = {
    home: "⌂",
    stream: "≋",
    sectors: "▦",
    market: "◫",
    research: "⌕",
    fiveGood: "◆",
    menu: "☰",
    collapse: "‹",
    search: "⌕",
    bell: "♢",
    star: "☆",
    policy: "§",
    industry: "◆",
    earnings: "▤",
    company: "▣",
    overseas: "◎",
    risk: "!",
    arrow: "→",
    close: "×",
    refresh: "↻",
    chevron: "⌄",
  };
  return icons[name] || "•";
}

export function renderSidebar({route, collapsed, mobileOpen, appBase = ""}) {
  const navItems = [
    {path: "/", label: "首页", icon: "home"},
    {path: "/sectors", label: "热门板块", icon: "sectors"},
    {path: "/market-daily", label: "盘前 / 盘中 / 盘后", icon: "market"},
    {path: "/research", label: "我的研究", icon: "research"},
  ];
  const isActive = (path) => path === "/"
    ? route === "/" || route === "/five-good" || route.startsWith("/five-good/")
    : route === path || route.startsWith(`${path}/`);

  return `
    <button class="mobile-menu-button" data-mobile-menu aria-label="打开导航菜单">${icon("menu")}</button>
    <div class="sidebar-scrim ${mobileOpen ? "visible" : ""}" data-close-menu></div>
    <aside class="workspace-sidebar ${collapsed ? "collapsed" : ""} ${mobileOpen ? "mobile-open" : ""}">
      <div class="workspace-brand">
        <div class="brand-mark">CA</div>
        <div class="brand-copy">
          <strong>ChainAlpha</strong>
          <span>研投助手</span>
        </div>
        <button class="sidebar-collapse" data-collapse-sidebar aria-label="${collapsed ? "展开导航" : "收起导航"}">${icon("collapse")}</button>
      </div>
      <nav class="workspace-nav" aria-label="主导航">
        ${navItems.map((item) => `
          <a href="${appBase}${item.path}" data-route="${item.path}" class="${isActive(item.path) ? "active" : ""}" title="${item.label}">
            <span class="nav-icon" aria-hidden="true">${icon(item.icon)}</span>
            <span class="nav-label">${item.label}</span>
          </a>
        `).join("")}
      </nav>
      <div class="sidebar-brand-card">
        <strong>ChainAlpha</strong>
        <span>洞察事件 · 理解影响 · 把握机会</span>
        <small>研投助手 · 智能研究平台</small>
      </div>
    </aside>
  `;
}

export function renderDashboardHeader({
  query,
  lastUpdated,
  dataMode = "api",
  connectionStatus = "connecting",
  collectorStatus = null,
}) {
  return `
    <header class="dashboard-header">
      <div class="dashboard-heading">
        <div class="title-line">
          <h1>实时金融事件智能流</h1>
          <span class="beta-badge">BETA</span>
          ${dataMode === "mock" ? `<span class="demo-data-badge">演示数据</span>` : ""}
          <span class="connection-badge ${connectionStatus}">${connectionLabel(connectionStatus)}</span>
        </div>
        <p>自动发现重要事件，判断影响板块，解释影响逻辑，并持续更新事件进展。</p>
        <div class="header-status-line">
          <span class="header-updated">数据刷新于 ${formatFreshness(lastUpdated)}</span>
          ${collectorStatus ? `
            <span class="collector-summary ${collectorStatus.running ? "running" : collectorStatus.state}">
              ${collectorStatus.running
                ? "正在抓取最新新闻"
                : collectorStatus.state === "failed"
                  ? "上次采集失败"
                  : `每 ${Math.round(collectorStatus.intervalSeconds / 60)} 分钟自动采集`}
            </span>
          ` : ""}
        </div>
      </div>
      <div class="header-tools">
        <label class="global-search">
          <span aria-hidden="true">${icon("search")}</span>
          <input data-global-search aria-label="全局搜索" value="${escapeAttr(query)}" placeholder="搜索事件、板块、公司…" />
        </label>
        <button class="square-icon-button" aria-label="通知" title="通知">${icon("bell")}</button>
        ${dataMode === "api" ? `
          <button class="square-icon-button" data-trigger-collection aria-label="立即采集新闻"
            title="立即采集新闻" ${collectorStatus?.running ? "disabled" : ""}>${icon("refresh")}</button>
        ` : ""}
        <button class="square-icon-button" data-follow-global aria-label="收藏或关注" title="收藏或关注">${icon("star")}</button>
        <button class="user-avatar" aria-label="当前用户">研</button>
      </div>
    </header>
  `;
}

export function renderFilterBar({category, hours, hasFilters}) {
  const tabs = [
    ["all", "全部"],
    ["policy", "政策"],
    ["industry", "产业"],
    ["earnings", "财报"],
    ["company", "公司"],
    ["overseas", "海外"],
    ["risk", "风险"],
  ];
  return `
    <section class="filter-bar" aria-label="事件筛选">
      <div class="category-tabs" role="tablist" aria-label="事件分类">
        ${tabs.map(([value, label], index) => `
          <button role="tab" tabindex="${value === category ? "0" : "-1"}"
            aria-selected="${value === category}" data-category="${value}" class="${value === category ? "active" : ""}">
            ${label}
          </button>
        `).join("")}
      </div>
      <div class="time-filter">
        <span>时间范围</span>
        <select data-hours aria-label="时间范围">
          ${[[1, "1小时"], [6, "6小时"], [24, "24小时"], [72, "3天"], [168, "7天"], [720, "30天"]]
            .map(([value, label]) => `<option value="${value}" ${Number(hours) === value ? "selected" : ""}>${label}</option>`)
            .join("")}
        </select>
        <button class="soft-button" data-follow-custom>${icon("star")} 自定义关注</button>
        ${hasFilters ? `<button class="text-button" data-clear-filters>清除筛选</button>` : ""}
      </div>
    </section>
  `;
}

export function renderTopEvents(events) {
  if (!events.length) return "";
  return `
    <section class="top-events-section" aria-labelledby="top-events-title">
      <div class="section-heading">
        <div>
          <span class="section-kicker">PRIORITY</span>
          <h2 id="top-events-title">今日最重要的 5 件事</h2>
        </div>
        <span>按事件重要性动态排序</span>
      </div>
      <div class="top-events-track">
        ${events.map((event, index) => renderTopEventCard(event, index + 1)).join("")}
      </div>
    </section>
  `;
}

export function renderTopEventCard(event, rank) {
  return `
    <article class="top-event-card" data-jump-event="${event.id}" tabindex="0" role="button"
      aria-label="定位事件：${escapeAttr(event.title)}">
      <div class="top-card-row">
        <span class="rank-number">${String(rank).padStart(2, "0")}</span>
        ${renderImpactBadge(event.impactDirection)}
      </div>
      <h3>${escapeHtml(event.title)}</h3>
      <div class="top-card-sectors">${event.sectors.slice(0, 2).map(renderTag).join("")}</div>
      <time>${formatRelative(event.occurredAt)}</time>
    </article>
  `;
}

export function renderFeedHeader({lastUpdated, refreshing, feedQuery}) {
  return `
    <div class="feed-heading">
      <div>
        <div class="live-title">
          <span class="live-dot"></span>
          <h2>实时事件流</h2>
          <span class="auto-update">${refreshing ? "正在刷新" : "自动更新"}</span>
        </div>
        <p>最后更新 ${formatFreshness(lastUpdated)}</p>
      </div>
      <label class="feed-search">
        <span aria-hidden="true">${icon("search")}</span>
        <input data-feed-search aria-label="搜索事件流" value="${escapeAttr(feedQuery)}"
          placeholder="搜索事件、板块、公司或关键词…" />
      </label>
    </div>
  `;
}

export function renderEventFeed(events, expandedEvents, timelineEvents) {
  return `
    <div class="event-feed" id="event-feed">
      ${events.map((event) => renderEventCard(
        event,
        expandedEvents.has(event.id),
        timelineEvents.has(event.id),
      )).join("")}
    </div>
  `;
}

export function renderEventCard(event, expanded, showTimeline) {
  const companyVisible = event.companies.slice(0, 3);
  const companyOverflow = event.companies.length - companyVisible.length;
  return `
    <article class="market-event-card" id="event-${event.id}" data-event-id="${event.id}">
      <div class="event-time-column">
        <strong>${formatClock(event.occurredAt)}</strong>
        <span>${event.occurredAtEstimated ? "抓取时间" : formatDay(event.occurredAt)}</span>
        <span class="category-icon ${event.category}" title="${EVENT_CATEGORY_LABELS[event.category]}">${icon(event.category)}</span>
      </div>
      <div class="event-card-content">
        <div class="event-title-row">
          <div>
            <span class="event-category">${EVENT_CATEGORY_LABELS[event.category]}</span>
            <h3>${escapeHtml(event.title)}</h3>
          </div>
          <div class="event-badges">
            ${renderAiBadge(event)}
            ${renderImpactBadge(event.impactDirection)}
            ${renderStatusBadge(event.status)}
          </div>
        </div>
        <div class="event-analysis-grid">
          <div>
            <span class="analysis-label">发生了什么</span>
            <p class="${expanded ? "" : "line-clamp-3"}">${escapeHtml(event.factSummary)}</p>
          </div>
          <div>
            <span class="analysis-label">为什么重要</span>
            <p class="${expanded ? "" : "line-clamp-3"}">${escapeHtml(event.importanceReason)}</p>
          </div>
        </div>
        ${(event.factSummary.length + event.importanceReason.length) > 150 ? `
          <button class="expand-copy" data-expand-event="${event.id}" aria-expanded="${expanded}">
            ${expanded ? "收起" : "展开全文"} ${icon("chevron")}
          </button>
        ` : ""}
        <div class="event-meta-row">
          <div class="event-tags">
            ${event.sectors.slice(0, 3).map(renderTag).join("")}
          </div>
          <div class="event-companies">
            ${companyVisible.map((company) => `<span>${escapeHtml(company)}</span>`).join("")}
            ${companyOverflow > 0 ? `<span>+${companyOverflow}</span>` : ""}
          </div>
        </div>
        ${showTimeline ? renderEventTimeline(event) : ""}
        <div class="event-card-footer">
          <span>${event.sourceCount} 个来源 · 置信度 ${event.confidenceScore}%</span>
          <span>更新 ${event.updateCount} 次 · ${formatRelative(event.updatedAt)}</span>
          <button data-open-event-detail="${event.id}">
            查看进展 ${icon("arrow")}
          </button>
        </div>
      </div>
    </article>
  `;
}

export function renderRightRail(data) {
  return `
    <aside class="dashboard-rail">
      ${renderHotSectorRanking(data.hotSectors)}
      ${renderEventDistribution(data.eventDistribution)}
      ${renderTrackingEvents(data.trackingEvents)}
      ${renderMarketSessionCards()}
    </aside>
  `;
}

export function renderHotSectorRanking(items) {
  return `
    <section class="rail-panel">
      <div class="rail-heading"><h3>热门板块</h3><span>实时热度</span></div>
      <ol class="sector-ranking">
        ${items.map((item, index) => `
          <li tabindex="0" title="${escapeAttr(item.primaryCatalyst)}">
            <span class="sector-rank">${index + 1}</span>
            <div><strong>${escapeHtml(item.name)}</strong><small>${item.eventCount} 个事件</small></div>
            <b>${item.heatScore.toFixed(1)}</b>
            <span class="heat-change ${item.heatChange == null ? "neutral" : item.heatChange >= 0 ? "up" : "down"}">
              ${item.heatChange == null ? "—" : `${item.heatChange >= 0 ? "↑ +" : "↓ "}${item.heatChange.toFixed(1)}`}
            </span>
          </li>
        `).join("")}
      </ol>
    </section>
  `;
}

export function renderEventDistribution(items) {
  return `
    <section class="rail-panel">
      <div class="rail-heading"><h3>事件类型分布</h3><span>近 24 小时</span></div>
      <div class="distribution-list">
        ${items.map((item) => `
          <div class="distribution-row">
            <div><span>${EVENT_CATEGORY_LABELS[item.category]}</span><strong>${item.percentage}%</strong></div>
            <div class="distribution-track"><i style="width:${item.percentage}%"></i></div>
          </div>
        `).join("")}
      </div>
    </section>
  `;
}

export function renderTrackingEvents(events) {
  return `
    <section class="rail-panel">
      <div class="rail-heading"><h3>持续跟踪中</h3><span>${events.length} 条</span></div>
      <div class="tracking-list">
        ${events.length ? events.map((event) => `
          <button data-jump-event="${event.id}">
            <span class="tracking-dot"></span>
            <span><strong>${escapeHtml(event.title)}</strong><small>已更新 ${event.updateCount} 次</small></span>
            ${icon("arrow")}
          </button>
        `).join("") : `<p class="rail-empty">当前筛选范围内暂无持续跟踪事件。</p>`}
      </div>
    </section>
  `;
}

export function renderMarketSessionCards() {
  const sessions = [
    ["pre", "盘前观察", "查看今日重点事件"],
    ["intraday", "盘中异动", "实时追踪异动事件"],
    ["post", "盘后总结", "今日事件回顾"],
  ];
  return `
    <section class="rail-panel market-sessions">
      <div class="rail-heading"><h3>市场时段</h3><span>每日研究</span></div>
      ${sessions.map(([session, title, copy]) => `
        <a href="/market-daily?session=${session}" data-market-session="${session}">
          <span><strong>${title}</strong><small>${copy}</small></span>
          ${icon("arrow")}
        </a>
      `).join("")}
    </section>
  `;
}

export function renderDashboardSkeleton() {
  return `
    <div class="dashboard-skeleton" aria-label="正在加载">
      <div class="skeleton skeleton-title"></div>
      <div class="skeleton-row">${Array.from({length: 5}, () => `<div class="skeleton skeleton-card"></div>`).join("")}</div>
      <div class="skeleton-grid">
        <div>${Array.from({length: 4}, () => `<div class="skeleton skeleton-feed"></div>`).join("")}</div>
        <div>${Array.from({length: 3}, () => `<div class="skeleton skeleton-panel"></div>`).join("")}</div>
      </div>
    </div>
  `;
}

export function renderErrorState(message) {
  return `
    <div class="dashboard-state">
      <span class="state-icon">!</span>
      <h2>事件数据暂时无法加载</h2>
      <p>${escapeHtml(message || "请稍后重试。")}</p>
      <button class="primary-button" data-retry-dashboard>${icon("refresh")} 重新加载</button>
    </div>
  `;
}

export function renderEmptyState(hasFilters = false) {
  return `
    <div class="dashboard-state compact">
      <span class="state-icon">${icon("search")}</span>
      <h3>${hasFilters ? "没有匹配的事件" : "当前时间范围暂无真实事件"}</h3>
      <p>${hasFilters
        ? "试试缩短关键词、扩大时间范围或清除分类条件。"
        : "系统不会用演示数据冒充实时结果，可扩大时间范围查看历史采集内容。"}</p>
      ${hasFilters ? `<button class="primary-button" data-clear-filters>清除筛选</button>` : ""}
    </div>
  `;
}

export function renderBasicPage(route, searchParams) {
  const session = searchParams.get("session") || "pre";
  const content = {
    "/sectors": ["热门板块", "板块热度、催化剂与关联事件将在第二阶段接入真实数据。"],
    "/market-daily": [
      session === "intraday" ? "盘中异动" : session === "post" ? "盘后总结" : "盘前观察",
      "该页面已建立基础路由，后续将接入对应时段的事件摘要与市场数据。",
    ],
  }[route] || ["实时事件流", "完整事件流已复用首页筛选与事件卡片。"];
  return `
    <main class="basic-page">
      <span class="section-kicker">CHAINALPHA WORKSPACE</span>
      <h1>${content[0]}</h1>
      <p>${content[1]}</p>
      <a href="/" data-route="/">返回事件首页 ${icon("arrow")}</a>
    </main>
  `;
}

function renderImpactBadge(direction) {
  return `<span class="impact-badge ${direction}">${IMPACT_LABELS[direction]}</span>`;
}

function renderStatusBadge(status) {
  return `<span class="status-badge ${status}"><i></i>${EVENT_STATUS_LABELS[status]}</span>`;
}

function renderAiBadge(event) {
  if (!["hybrid", "llm"].includes(event.analysisMethod)) return "";
  const confidence = event.llmConfidenceScore == null
    ? ""
    : `，分析置信度 ${event.llmConfidenceScore.toFixed(0)}`;
  const model = event.analysisModel
    ? `${event.analysisProvider || "AI"} / ${event.analysisModel}`
    : "AI";
  return `<span class="event-ai-badge" title="${escapeAttr(`${model}${confidence}`)}">AI 分析</span>`;
}

function renderEventTimeline(event) {
  return `
    <div class="event-timeline">
      ${(event.timeline || []).map((item) => `
        <div>
          <time>${formatClock(item.occurredAt)}</time>
          <i></i>
          <span><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.summary)}</small></span>
        </div>
      `).join("")}
    </div>
  `;
}

function renderTag(value) {
  return `<span class="event-tag">${escapeHtml(value)}</span>`;
}

export function formatClock(value) {
  const date = validDate(value);
  if (!date) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {hour: "2-digit", minute: "2-digit", hour12: false}).format(date);
}

function formatFreshness(value) {
  if (!value) return "暂无可用数据";
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  const now = new Date();
  const sameDay = date.getFullYear() === now.getFullYear()
    && date.getMonth() === now.getMonth()
    && date.getDate() === now.getDate();
  return new Intl.DateTimeFormat("zh-CN", {
    year: sameDay ? undefined : "numeric",
    month: sameDay ? undefined : "2-digit",
    day: sameDay ? undefined : "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function formatDay(value) {
  const date = validDate(value);
  if (!date) return "未标记";
  const today = new Date();
  const yesterday = new Date(Date.now() - 86_400_000);
  if (date.toDateString() === today.toDateString()) return "今天";
  if (date.toDateString() === yesterday.toDateString()) return "昨天";
  return `${date.getMonth() + 1}月${date.getDate()}日`;
}

function formatRelative(value) {
  const date = validDate(value);
  if (!date) return "时间未知";
  const minutes = Math.max(0, Math.round((Date.now() - date.getTime()) / 60_000));
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)} 小时前`;
  return `${Math.floor(minutes / 1440)} 天前`;
}

function validDate(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function connectionLabel(status) {
  return {
    connected: "实时连接",
    reconnecting: "正在重连",
    polling: "定时刷新",
    offline: "连接中断",
    connecting: "正在连接",
  }[status] || "正在连接";
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
