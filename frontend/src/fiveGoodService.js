import {MOCK_FIVE_GOOD_ENTRIES, MOCK_FIVE_GOOD_HISTORY} from "./mocks/fiveGoodMock.js";

const API_BASE = inferApiBase();

export function fiveGoodDataMode() {
  const query = new URLSearchParams(location.search).get("fiveGoodData");
  return query === "mock" ? "mock" : "api";
}

export async function getFiveGoodOverview(options = {}) {
  if (fiveGoodDataMode() === "mock") return mockOverview();
  return normalizeOverview(await requestJson("/api/five-good/overview", options));
}

export async function getFiveGoodStocks(filters = {}, options = {}) {
  if (fiveGoodDataMode() === "mock") return mockList(filters);
  const query = toQuery({
    category: filters.category === "all" ? "" : filters.category,
    keyword: filters.keyword, market: filters.market, industry: filters.industry,
    sector: filters.sector, status: filters.status, updated_within: filters.updatedWithin,
    sort: filters.sort || "score_desc", cursor: filters.cursor, limit: filters.limit || 50,
  });
  return normalizeList(await requestJson(`/api/five-good/stocks?${query}`, options));
}

export async function getFiveGoodStockDetail(stockCode, options = {}) {
  if (fiveGoodDataMode() === "mock") return mockDetail(stockCode);
  return normalizeDetail(await requestJson(`/api/five-good/stocks/${encodeURIComponent(stockCode)}`, options));
}

export async function getFiveGoodHistory(stockCode, options = {}) {
  if (fiveGoodDataMode() === "mock") return MOCK_FIVE_GOOD_HISTORY.filter((row) => row.stockCode === stockCode);
  return (await requestJson(`/api/five-good/stocks/${encodeURIComponent(stockCode)}/history`, options)).map(normalizeHistory);
}

async function requestJson(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {headers: {Accept: "application/json"}, signal: options.signal});
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || `请求失败 (${response.status})`);
  return response.json();
}

function normalizeOverview(payload) {
  return {coverageCount: payload.coverage_count, poolCount: payload.pool_count, screenedUniverseCount: payload.screened_universe_count || 0, eligibleCount: payload.eligible_count || 0, categoryCounts: payload.category_counts || {}, categories: (payload.categories || []).map((group) => ({category: group.category, count: group.count, entries: group.entries.map(normalizeEntry)})), updatedAt: payload.updated_at, dataMode: payload.data_mode || "api"};
}

function normalizeList(payload) {
  return {items: (payload.items || []).map(normalizeEntry), nextCursor: payload.next_cursor, hasMore: payload.has_more, totalEstimate: payload.total_estimate, industries: payload.industries || [], sectors: payload.sectors || [], generatedAt: payload.generated_at, dataMode: payload.data_mode || "api"};
}

function normalizeDetail(payload) {
  return {stockCode: payload.stock_code, stockName: payload.stock_name, market: payload.market, industry: payload.industry, subIndustry: payload.sub_industry, tags: payload.tags || [], entries: (payload.entries || []).map(normalizeEntry), history: (payload.history || []).map(normalizeHistory), dataMode: payload.data_mode || "api"};
}

function normalizeEntry(row) {
  return {id: row.id, stockCode: row.stock_code, stockName: row.stock_name, market: row.market, category: row.category, industry: row.industry, subIndustry: row.sub_industry, sector: row.sector, score: row.score, status: row.status, selectionReason: row.selection_reason, riskSummary: row.risk_summary, tags: row.tags || [], validFrom: row.valid_from, validUntil: row.valid_until, asOfDate: row.as_of_date, createdAt: row.created_at, updatedAt: row.updated_at, categoryData: camelizeObject(row.category_data || {})};
}

function normalizeHistory(row) {
  return {id: row.id, entryId: row.entry_id, stockCode: row.stock_code, category: row.category, action: row.action, previousScore: row.previous_score, newScore: row.new_score, reason: row.reason, asOfDate: row.as_of_date, createdAt: row.created_at};
}

function camelizeObject(value) {
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key.replace(/_([a-z])/g, (_, char) => char.toUpperCase()), item]));
}

function mockOverview() {
  const categories = Object.keys({good_company: 1, good_timing: 1, good_price: 1, good_story: 1, good_stock: 1}).map((category) => { const entries = MOCK_FIVE_GOOD_ENTRIES.filter((item) => item.category === category).sort((a, b) => b.score - a.score); return {category, count: entries.length, entries: entries.slice(0, 6)}; });
  return {coverageCount: new Set(MOCK_FIVE_GOOD_ENTRIES.map((item) => item.stockCode)).size, poolCount: MOCK_FIVE_GOOD_ENTRIES.length, categoryCounts: Object.fromEntries(categories.map((group) => [group.category, group.count])), categories, updatedAt: new Date().toISOString(), dataMode: "demo"};
}

function mockList(filters) {
  let items = [...MOCK_FIVE_GOOD_ENTRIES];
  if (filters.category && filters.category !== "all") items = items.filter((item) => item.category === filters.category);
  if (filters.keyword) items = items.filter((item) => `${item.stockName}${item.stockCode}`.toLowerCase().includes(filters.keyword.toLowerCase()));
  for (const key of ["market", "industry", "sector", "status"]) if (filters[key]) items = items.filter((item) => item[key] === filters[key]);
  items.sort((a, b) => b.score - a.score);
  return {items, nextCursor: null, hasMore: false, totalEstimate: items.length, industries: [...new Set(MOCK_FIVE_GOOD_ENTRIES.map((item) => item.industry))], sectors: [...new Set(MOCK_FIVE_GOOD_ENTRIES.map((item) => item.sector))], generatedAt: new Date().toISOString(), dataMode: "demo"};
}

function mockDetail(stockCode) {
  const entries = MOCK_FIVE_GOOD_ENTRIES.filter((item) => item.stockCode === stockCode);
  if (!entries.length) throw new Error("股票不在五好精选池中");
  const first = entries[0];
  return {stockCode, stockName: first.stockName, market: first.market, industry: first.industry, subIndustry: first.subIndustry, tags: [...new Set(entries.flatMap((item) => item.tags))], entries, history: MOCK_FIVE_GOOD_HISTORY.filter((item) => item.stockCode === stockCode), dataMode: "demo"};
}

function toQuery(values) {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => { if (value !== undefined && value !== null && value !== "") params.set(key, value); });
  return params.toString();
}

function inferApiBase() {
  if (window.CHAINALPHA_API_BASE) return window.CHAINALPHA_API_BASE;
  if (location.port === "5173" || location.port === "5174") return "http://127.0.0.1:8000";
  return "";
}
