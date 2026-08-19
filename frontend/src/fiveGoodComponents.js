const CATEGORY_META = {
  good_company: {label: "好公司", tone: "company", description: "长期质量与竞争优势"},
  good_timing: {label: "好时机", tone: "timing", description: "相对有利的观察位置"},
  good_price: {label: "好价格", tone: "price", description: "估值与基本面价值匹配"},
  good_story: {label: "好故事", tone: "story", description: "明确事件催化与预期变化"},
  good_stock: {label: "好股票", tone: "stock", description: "热点产业链中的行情弹性"},
};

export function categoryLabel(value) { return CATEGORY_META[value]?.label || value; }

export function renderFiveGoodHeader(overview) {
  return `<header class="five-good-header">
    <div><div class="five-good-title-line"><h1>五好精选</h1>${overview?.dataMode === "demo" ? '<span class="fg-demo-badge">演示数据</span>' : ""}</div>
      <p>从公司质量、交易时机、估值性价比、事件催化和产业弹性五个维度，构建可解释的股票筛选与跟踪体系。</p></div>
    <div class="fg-header-meta"><span>最后更新 ${formatDateTime(overview?.updatedAt)}</span>${overview?.screenedUniverseCount ? `<span>全市场扫描 ${overview.screenedUniverseCount}</span><span>质量闸门后 ${overview.eligibleCount}</span>` : ""}<strong>入选股票 ${overview?.coverageCount ?? "-"}</strong><span>股票池记录 ${overview?.poolCount ?? "-"}</span></div>
  </header>`;
}

export function renderFiveGoodTabs(active, counts = {}) {
  const tabs = [{key: "overview", label: "总览"}, ...Object.entries(CATEGORY_META).map(([key, value]) => ({key, label: value.label}))];
  return `<nav class="fg-tabs" aria-label="五好股票池">${tabs.map((tab) => `<button class="${active === tab.key ? "active" : ""}" data-fg-tab="${tab.key}">${tab.label}${tab.key === "overview" ? "" : `<span>${counts[tab.key] || 0}</span>`}</button>`).join("")}</nav>`;
}

export function renderFiveGoodFilters(filters, options = {}) {
  return `<section class="fg-filters">
    <label class="fg-search"><span>搜索</span><input data-fg-filter="keyword" value="${esc(filters.keyword)}" placeholder="股票名称 / 代码"></label>
    ${selectFilter("market", "市场", filters.market, [["", "全部"], ["sh", "沪市"], ["sz", "深市"], ["bj", "北交所"]])}
    ${selectFilter("industry", "行业", filters.industry, [["", "全部行业"], ...(options.industries || []).map((item) => [item, item])])}
    ${selectFilter("sector", "板块/概念", filters.sector, [["", "全部板块"], ...(options.sectors || []).map((item) => [item, item])])}
    ${selectFilter("updatedWithin", "更新时间", filters.updatedWithin, [["today", "今天"], ["3d", "近3天"], ["7d", "近7天"], ["all", "全部"]])}
    <button class="fg-reset" data-fg-reset>重置筛选</button>
  </section>`;
}

export function renderOverview(overview) {
  if (!overview) return renderSkeleton();
  return `<div class="fg-overview-grid">${overview.categories.map((group) => {
    const meta = CATEGORY_META[group.category];
    return `<section class="fg-pool-panel tone-${meta.tone}">
      <header><div><h2>${meta.label}</h2><p>${meta.description}</p></div><span>${group.count} 只</span></header>
      <div class="fg-overview-list">${group.entries.map((entry) => overviewRow(entry)).join("") || renderInlineEmpty()}</div>
      <button class="fg-view-all" data-fg-tab="${group.category}">查看全部 <span>→</span></button>
    </section>`;
  }).join("")}</div>`;
}

export function renderPoolTable(category, items) {
  if (!items.length) return renderEmpty();
  const columns = tableColumns(category);
  return `<div class="fg-table-wrap"><table class="fg-table"><thead><tr>${columns.map((col) => `<th>${col.label}</th>`).join("")}<th></th></tr></thead>
    <tbody>${items.map((entry) => `<tr data-open-stock="${esc(entry.stockCode)}">${columns.map((col) => `<td>${col.render(entry)}</td>`).join("")}<td><button class="fg-row-open" aria-label="查看${esc(entry.stockName)}详情">›</button></td></tr>`).join("")}</tbody></table></div>`;
}

export function renderPoolHeader(category, total) {
  const meta = CATEGORY_META[category];
  return `<div class="fg-pool-heading"><div><h2>${meta.label}</h2><p>${poolExplanation(category)}</p></div><span>当前 ${total} 条记录</span></div>`;
}

export function renderDrawer(detail, {loading = false, error = ""} = {}) {
  if (loading) return drawerShell(`<div class="fg-drawer-loading">正在读取筛选依据…</div>`);
  if (error) return drawerShell(`<div class="fg-drawer-error">${esc(error)}</div>`);
  if (!detail) return "";
  const selected = new Map(detail.entries.map((entry) => [entry.category, entry]));
  return drawerShell(`<header class="fg-drawer-header"><div><span>${esc(detail.stockCode)}</span><h2>${esc(detail.stockName)}</h2><p>${esc(detail.industry)} · ${esc(detail.subIndustry || "-")}</p></div><button data-close-drawer aria-label="关闭">×</button></header>
    <div class="fg-drawer-tags">${detail.tags.map(tag).join("")}</div>
    <section class="fg-membership"><h3>当前入选股票池</h3><div>${Object.keys(CATEGORY_META).map((category) => `<span class="${selected.has(category) ? "selected" : ""}">${selected.has(category) ? "✓" : "–"} ${categoryLabel(category)}</span>`).join("")}</div></section>
    <div class="fg-drawer-sections">${detail.entries.map((entry) => detailSection(entry)).join("")}</div>
    <section class="fg-history"><h3>入选历史</h3>${detail.history.map((item) => `<div><time>${esc(item.asOfDate)}</time><strong>${historyAction(item.action)}</strong><span>${esc(item.reason)}</span></div>`).join("") || "<p>暂无历史记录</p>"}</section>`);
}

export function renderSkeleton() { return `<div class="fg-skeleton">${Array.from({length: 5}, () => '<div><span></span><span></span><span></span></div>').join("")}</div>`; }
export function renderError(message) { return `<div class="fg-state"><strong>加载失败</strong><p>${esc(message)}</p><button data-fg-retry>重新加载</button></div>`; }
export function renderEmpty() { return `<div class="fg-state"><strong>没有符合条件的记录</strong><p>尝试调整市场、行业、板块或更新时间筛选。</p><button data-fg-reset>重置筛选</button></div>`; }

function overviewRow(entry) {
  const data = entry.categoryData;
  const detail = {
    good_company: `${data.industryPosition || "待验证"} · ROE ${number(data.roe, "%")}`,
    good_timing: `${data.technicalSignal || "数据不足"} · ${zone(data.watchZoneLow, data.watchZoneHigh)}`,
    good_price: `PE ${number(data.pe)} · 分位 ${number(data.historicalPePercentile, "%")}`,
    good_story: data.eventTitle || "事件进展跟踪",
    good_stock: `${data.industryChain || entry.sector} → ${data.chainPosition || entry.subIndustry}`,
  }[entry.category];
  return `<button class="fg-overview-row" data-open-stock="${esc(entry.stockCode)}"><span class="fg-stock-name"><strong>${esc(entry.stockName)}</strong><small>${esc(entry.stockCode)}</small></span><span class="fg-row-detail">${esc(detail)}</span><strong class="fg-score">${Math.round(entry.score)}</strong></button>`;
}

function tableColumns(category) {
  const common = [
    {label: "股票", render: (e) => `<span class="fg-stock-name"><strong>${esc(e.stockName)}</strong><small>${esc(e.stockCode)}</small></span>`},
    {label: "行业", render: (e) => `<span>${esc(e.industry)}</span><small class="fg-cell-note">${esc(e.subIndustry)}</small>`},
  ];
  const categoryColumns = {
    good_company: [
      {label: "行业地位", render: (e) => esc(e.categoryData.industryPosition || "数据不足")},
      {label: "ROE", render: (e) => number(e.categoryData.roe, "%")},
      {label: "股息率", render: (e) => number(e.categoryData.dividendYield, "%")},
      {label: "现金流", render: (e) => statusText(e.categoryData.cashflowQuality)},
    ],
    good_timing: [
      {label: "当前价", render: (e) => number(e.categoryData.currentPrice)},
      {label: "技术状态", render: (e) => statusText(e.categoryData.technicalSignal)},
      {label: "参考关注区间", render: (e) => zone(e.categoryData.watchZoneLow, e.categoryData.watchZoneHigh)},
      {label: "资金状态", render: (e) => statusText(e.categoryData.capitalFlowSignal)},
    ],
    good_price: [
      {label: "当前价", render: (e) => number(e.categoryData.currentPrice)},
      {label: "PE / PB / PEG", render: (e) => `${number(e.categoryData.pe)} / ${number(e.categoryData.pb)} / ${number(e.categoryData.peg)}`},
      {label: "历史PE分位", render: (e) => number(e.categoryData.historicalPePercentile, "%")},
      {label: "估值状态", render: (e) => statusText(e.categoryData.valuationStatus)},
    ],
    good_story: [
      {label: "当前重大事件", render: (e) => eventTitle(e)},
      {label: "事件类型", render: (e) => esc(e.categoryData.eventType || "-")},
      {label: "状态", render: (e) => statusText(eventStatus(e.categoryData.eventStatus))},
      {label: "可信度", render: (e) => number(e.categoryData.confidence, "%")},
    ],
    good_stock: [
      {label: "产业链", render: (e) => esc(e.categoryData.industryChain || e.sector)},
      {label: "产业链环节", render: (e) => esc(e.categoryData.chainPosition || e.subIndustry)},
      {label: "热点关联度", render: (e) => number(e.categoryData.themeRelevanceScore)},
      {label: "板块相对强度", render: (e) => statusText(e.categoryData.relativeStrength)},
    ],
  }[category] || [];
  return [...common, ...categoryColumns, {label: "评分", render: (e) => `<strong class="fg-score">${Math.round(e.score)}</strong>`}, {label: "更新时间", render: (e) => formatDateTime(e.updatedAt)}];
}

function detailSection(entry) {
  const dataRows = Object.entries(entry.categoryData).filter(([key, value]) => value !== "" && value !== null && !["eventId"].includes(key)).slice(0, 10);
  const eventLink = entry.category === "good_story" && entry.categoryData.eventId ? `<span class="fg-event-link">关联事件已纳入筛选证据</span>` : "";
  return `<section><header><div><h3>为什么是${categoryLabel(entry.category)}？</h3><span>评分 ${Math.round(entry.score)}</span></div><time>数据日期 ${esc(entry.asOfDate)} · 更新 ${formatDateTime(entry.updatedAt)}</time></header>
    <p>${esc(entry.selectionReason)}</p><div class="fg-detail-metrics">${dataRows.map(([key, value]) => `<div><span>${fieldLabel(key)}</span><strong>${esc(displayValue(value, key))}</strong></div>`).join("")}</div>
    ${eventLink}<div class="fg-risk"><strong>主要风险</strong><span>${esc(entry.riskSummary)}</span></div><small>筛选依据：公开信息、结构化指标与规则评分；结论随数据更新动态调整。</small></section>`;
}

function drawerShell(content) { return `<div class="fg-drawer-layer"><div class="fg-drawer-scrim" data-close-drawer></div><aside class="fg-drawer">${content}</aside></div>`; }
function selectFilter(key, label, selected, options) { return `<label><span>${label}</span><select data-fg-filter="${key}">${options.map(([value, text]) => `<option value="${esc(value)}" ${selected === value ? "selected" : ""}>${esc(text)}</option>`).join("")}</select></label>`; }
function tag(value) { return `<span>${esc(value)}</span>`; }
function statusText(value) { return `<span class="fg-status">${esc(value || "数据不足")}</span>`; }
function zone(low, high) { return low != null && high != null ? `${number(low)} - ${number(high)}` : "数据不足"; }
function number(value, suffix = "") { return Number.isFinite(Number(value)) ? `${Number(value).toFixed(Number(value) % 1 ? 2 : 0)}${suffix}` : "-"; }
function eventTitle(entry) { const data = entry.categoryData; return esc(data.eventTitle || "待关联事件"); }
function eventStatus(value) { return {unverified: "待验证", confirmed: "已确认", tracking: "持续跟踪", corrected: "已修正", completed: "已完成"}[value] || value || "数据不足"; }
function historyAction(value) { return {added: "加入", updated: "更新", removed: "移除", expired: "失效", reactivated: "重新激活"}[value] || value; }
function fieldLabel(key) { return ({dataCompleteness: "有效因子覆盖率", factorScores: "分项评分", reportPeriod: "财务报告期", announcementDate: "公告日期", sources: "数据来源", industryPosition: "行业地位", roe: "ROE", grossMargin: "毛利率", cashflowQuality: "现金流质量", cashflowToRevenue: "经营现金流/收入", dividendYield: "股息率", profitGrowth: "盈利趋势", debtRatio: "资产负债率", competitiveAdvantage: "竞争优势", currentPrice: "当前价格", boxLow: "箱体下沿", boxHigh: "箱体上沿", watchZoneLow: "观察区间下沿", watchZoneHigh: "观察区间上沿", supportPrice: "支撑位", invalidPrice: "失效价格", technicalSignal: "技术状态", capitalFlowSignal: "资金状态", pe: "PE", pb: "PB", ps: "PS", peg: "PEG", historicalPePercentile: "历史PE分位", profitGrowthExpectation: "利润增长预期", valuationStatus: "估值状态", industryValuationComparison: "行业估值对比", eventTitle: "事件", eventType: "事件类型", eventScore: "催化评分", eventStatus: "事件状态", impactDirection: "影响方向", confidence: "可信度", catalystSummary: "催化逻辑", industryChain: "产业链", chainPosition: "产业链环节", elasticityScore: "弹性评分", themeRelevanceScore: "热点关联度", relativeStrength: "相对强度", marketCapLevel: "市值区间"})[key] || key; }
function displayValue(value, key) { if (["roe", "grossMargin", "cashflowToRevenue", "dividendYield", "debtRatio", "historicalPePercentile", "confidence", "dataCompleteness"].includes(key)) return number(value, "%"); if (typeof value === "number") return number(value); if (Array.isArray(value)) return value.join("、"); if (value && typeof value === "object") return Object.entries(value).map(([name, score]) => `${fieldLabel(name)} ${number(score)}`).join(" · "); return String(value); }
function poolExplanation(category) { return {good_company: "回答这是否是一家值得长期跟踪的优质公司，不代表当前价格便宜。", good_timing: "研究相对有利的观察位置，不生成确定性买卖指令。", good_price: "判断当前估值相对基本面价值是否具有吸引力。", good_story: "跟踪可能改变市场关注度和预期的明确事件催化。", good_stock: "寻找热点产业链核心环节中行情弹性较高的股票。"}[category]; }
function renderInlineEmpty() { return '<p class="fg-inline-empty">暂无入选记录</p>'; }
function formatDateTime(value) { if (!value) return "-"; const date = new Date(value); return Number.isNaN(date.getTime()) ? esc(String(value)) : new Intl.DateTimeFormat("zh-CN", {month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false}).format(date); }
function esc(value) { return String(value ?? "").replace(/[&<>'"]/g, (char) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"}[char])); }
