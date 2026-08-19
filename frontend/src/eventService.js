import {MOCK_HOT_SECTORS, MOCK_MARKET_EVENTS} from "./mockEvents.js";

const API_BASE = inferApiBase();
const responseCache = new Map();
const inflight = new Map();
const CACHE_TTL = 12_000;
const SIMULATED_DELAY = 180;

export function eventDataMode() {
  const queryMode = new URLSearchParams(location.search).get("eventData");
  const configured = window.CHAINALPHA_EVENT_DATA_MODE || localStorage.getItem("chainalpha-event-data-mode");
  return queryMode === "mock" || configured === "mock" ? "mock" : "api";
}

export async function getEventDashboard(params = {}, options = {}) {
  if (eventDataMode() === "mock") return getMockDashboard(params);
  const query = toQuery({
    category: params.category === "all" ? "" : params.category,
    time_range: toTimeRange(params.hours),
    keyword: params.query,
    limit: params.limit || 20,
    timezone: "Asia/Shanghai",
  });
  const payload = await requestJson(`/api/events/dashboard?${query}`, options);
  return normalizeDashboard(payload);
}

export async function getEventFeed(params = {}, options = {}) {
  if (eventDataMode() === "mock") {
    const items = filterMockEvents(MOCK_MARKET_EVENTS, params);
    return {items, nextCursor: null, hasMore: false, totalEstimate: items.length};
  }
  const query = toQuery({
    category: params.category === "all" ? "" : params.category,
    status: params.status,
    impact_direction: params.impactDirection,
    time_range: toTimeRange(params.hours),
    keyword: params.query,
    sector: params.sector,
    company: params.company,
    cursor: params.cursor,
    limit: params.limit || 20,
    sort: params.sort || "updated_desc",
  });
  const payload = await requestJson(`/api/events?${query}`, options);
  return {
    items: (payload.items || []).map(normalizeEvent),
    nextCursor: payload.next_cursor || null,
    hasMore: Boolean(payload.has_more),
    totalEstimate: Number(payload.total_estimate || 0),
  };
}

export async function getEventDetail(eventId, options = {}) {
  if (eventDataMode() === "mock") {
    await wait(SIMULATED_DELAY);
    const event = MOCK_MARKET_EVENTS.find((item) => item.id === eventId);
    if (!event) throw new EventApiError("Event not found", 404);
    return {...event, sources: mockSources(event)};
  }
  return normalizeEvent(await requestJson(`/api/events/${encodeURIComponent(eventId)}`, options));
}

export async function getEventTimeline(eventId, options = {}) {
  if (eventDataMode() === "mock") {
    const event = await getEventDetail(eventId, options);
    return event.timeline || [];
  }
  const rows = await requestJson(`/api/events/${encodeURIComponent(eventId)}/timeline`, options);
  return (rows || []).map(normalizeTimelineItem);
}

export async function getHotSectors(params = {}, options = {}) {
  if (eventDataMode() === "mock") return MOCK_HOT_SECTORS.map((item) => ({...item}));
  const query = toQuery({
    time_range: toTimeRange(params.hours),
    limit: params.limit || 5,
    category: params.category === "all" ? "" : params.category,
  });
  const rows = await requestJson(`/api/sectors/hot?${query}`, options);
  return (rows || []).map(normalizeSector);
}

export async function getEventDistribution(params = {}, options = {}) {
  if (eventDataMode() === "mock") return mockDistribution(params);
  const query = toQuery({time_range: toTimeRange(params.hours)});
  const rows = await requestJson(`/api/events/distribution?${query}`, options);
  return rows || [];
}

export async function getCollectorStatus(options = {}) {
  if (eventDataMode() === "mock") {
    return {
      enabled: false,
      running: false,
      state: "mock",
      message: "演示模式不运行新闻采集",
      intervalSeconds: 120,
      completedAt: null,
      nextRunAt: null,
      fetched: 0,
      kept: 0,
      newEvents: 0,
    };
  }
  return normalizeCollectorStatus(
    await requestJson("/api/events/refresh/status", options),
  );
}

export async function requestEventRefresh(options = {}) {
  if (eventDataMode() === "mock") {
    throw new EventApiError("演示模式不运行新闻采集", 409);
  }
  const payload = await requestJson("/api/events/refresh", {
    ...options,
    method: "POST",
    fresh: true,
  });
  return normalizeCollectorStatus(payload.status || {});
}

export function createEventStream(handlers = {}) {
  if (eventDataMode() === "mock" || typeof EventSource === "undefined") {
    return {close() {}, mode: "polling"};
  }
  const source = new EventSource(`${API_BASE}/api/events/stream`);
  const eventTypes = [
    "event.created",
    "event.updated",
    "event.corrected",
    "event.status_changed",
    "sector.heat_updated",
    "heartbeat",
  ];
  eventTypes.forEach((type) => {
    source.addEventListener(type, (event) => {
      let payload = {};
      try {
        payload = JSON.parse(event.data || "{}");
      } catch (_) {
        payload = {};
      }
      handlers.onEvent?.(type, payload);
    });
  });
  source.onopen = () => handlers.onOpen?.();
  source.onerror = () => handlers.onError?.();
  return {
    close: () => source.close(),
    mode: "sse",
  };
}

export function clearEventCache(prefix = "") {
  for (const key of responseCache.keys()) {
    if (!prefix || key.includes(prefix)) responseCache.delete(key);
  }
}

export class EventApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.name = "EventApiError";
    this.status = status;
  }
}

async function requestJson(path, options = {}) {
  const method = options.method || "GET";
  const key = `${method}:${path}`;
  const cached = responseCache.get(key);
  if (method === "GET" && !options.fresh && cached && Date.now() - cached.savedAt < CACHE_TTL) {
    return cached.value;
  }
  if (method === "GET" && !options.fresh && inflight.has(key)) return inflight.get(key);
  const promise = fetch(`${API_BASE}${path}`, {
    method,
    signal: options.signal,
    headers: {"Accept": "application/json"},
  }).then(async (response) => {
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const payload = await response.json();
        detail = payload.detail || detail;
      } catch (_) {
        // Preserve the status message.
      }
      throw new EventApiError(detail, response.status);
    }
    const value = await response.json();
    if (method === "GET") responseCache.set(key, {value, savedAt: Date.now()});
    return value;
  }).finally(() => inflight.delete(key));
  inflight.set(key, promise);
  return promise;
}

function normalizeCollectorStatus(item) {
  return {
    enabled: Boolean(item.enabled),
    running: Boolean(item.running),
    state: item.state || "idle",
    message: item.message || "",
    intervalSeconds: Number(item.interval_seconds ?? item.intervalSeconds ?? 120),
    startedAt: item.started_at ?? item.startedAt ?? null,
    completedAt: item.completed_at ?? item.completedAt ?? null,
    nextRunAt: item.next_run_at ?? item.nextRunAt ?? null,
    fetched: Number(item.fetched || 0),
    kept: Number(item.kept || 0),
    processed: Number(item.processed || 0),
    newEvents: Number(item.new_events ?? item.newEvents ?? 0),
    dedup: item.dedup || {},
    llmAnalysis: item.llm_analysis ?? item.llmAnalysis ?? null,
    error: item.error || "",
  };
}

function normalizeDashboard(payload) {
  return {
    topEvents: (payload.top_events || []).map(normalizeEvent),
    eventFeed: (payload.event_feed || []).map(normalizeEvent),
    hotSectors: (payload.hot_sectors || []).map(normalizeSector),
    eventDistribution: payload.event_distribution || [],
    trackingEvents: (payload.tracking_events || []).map(normalizeEvent),
    generatedAt: payload.generated_at || "",
    dataFreshness: payload.data_freshness || {},
    dataMode: payload.data_mode || "api",
  };
}

function normalizeEvent(item) {
  return {
    id: item.id,
    title: item.title || "",
    category: item.category || "industry",
    status: item.status || "unverified",
    impactDirection: item.impact_direction ?? item.impactDirection ?? "uncertain",
    impactHorizon: item.impact_horizon ?? item.impactHorizon ?? "short_term",
    occurredAt: item.occurred_at ?? item.occurredAt ?? null,
    occurredAtEstimated: Boolean(item.occurred_at_estimated ?? item.occurredAtEstimated),
    firstSeenAt: item.first_seen_at ?? item.firstSeenAt ?? null,
    updatedAt: item.updated_at ?? item.updatedAt ?? null,
    factSummary: item.fact_summary ?? item.factSummary ?? "",
    importanceReason: item.importance_reason ?? item.importanceReason ?? "",
    directImpact: item.direct_impact ?? item.directImpact ?? "暂时无法判断",
    inferredImpact: item.inferred_impact ?? item.inferredImpact ?? "暂时无法判断",
    pendingVerification: item.pending_verification ?? item.pendingVerification ?? [],
    riskNote: item.risk_note ?? item.riskNote ?? "",
    sectors: item.sectors || [],
    concepts: item.concepts || [],
    companies: item.companies || [],
    sourceCount: Number(item.source_count ?? item.sourceCount ?? 0),
    updateCount: Number(item.update_count ?? item.updateCount ?? 0),
    importanceScore: Number(item.importance_score ?? item.importanceScore ?? 0),
    confidenceScore: Number(item.confidence_score ?? item.confidenceScore ?? 0),
    analysisMethod: item.analysis_method ?? item.analysisMethod ?? "rule",
    analysisProvider: item.analysis_provider ?? item.analysisProvider ?? "",
    analysisModel: item.analysis_model ?? item.analysisModel ?? "",
    analysisUpdatedAt: item.analysis_updated_at ?? item.analysisUpdatedAt ?? null,
    llmConfidenceScore: item.llm_confidence_score == null
      ? null
      : Number(item.llm_confidence_score),
    timeline: (item.timeline || []).map(normalizeTimelineItem),
    sources: (item.sources || []).map(normalizeSource),
  };
}

function normalizeTimelineItem(item) {
  return {
    id: item.id,
    eventId: item.event_id ?? item.eventId ?? "",
    occurredAt: item.occurred_at ?? item.occurredAt ?? null,
    title: item.title || "",
    summary: item.summary || "",
    status: item.status || "unverified",
    sourceIds: item.source_ids ?? item.sourceIds ?? [],
    createdAt: item.created_at ?? item.createdAt ?? null,
  };
}

function normalizeSource(item) {
  return {
    id: item.id,
    sourceType: item.source_type ?? item.sourceType ?? "unknown",
    publisher: item.publisher || "未知来源",
    title: item.title || "",
    url: safeExternalUrl(item.url),
    summary: item.summary || "",
    publishedAt: item.published_at ?? item.publishedAt ?? null,
    retrievedAt: item.retrieved_at ?? item.retrievedAt ?? null,
    isPrimarySource: Boolean(item.is_primary_source ?? item.isPrimarySource),
    authorityScore: Number(item.authority_score ?? item.authorityScore ?? 0),
    timeMissing: Boolean(item.time_missing ?? item.timeMissing),
  };
}

function normalizeSector(item) {
  return {
    id: item.id,
    name: item.name || "",
    heatScore: Number(item.heat_score ?? item.heatScore ?? 0),
    heatChange: item.heat_change ?? item.heatChange ?? null,
    eventCount: Number(item.event_count ?? item.eventCount ?? 0),
    positiveEventCount: Number(item.positive_event_count ?? item.positiveEventCount ?? 0),
    negativeEventCount: Number(item.negative_event_count ?? item.negativeEventCount ?? 0),
    primaryCatalyst: item.primary_catalyst ?? item.primaryCatalyst ?? "",
    updatedAt: item.updated_at ?? item.updatedAt ?? null,
  };
}

async function getMockDashboard(params) {
  await wait(SIMULATED_DELAY);
  maybeThrowDevelopmentError();
  const eventFeed = filterMockEvents(MOCK_MARKET_EVENTS, params);
  return {
    topEvents: [...eventFeed].sort((a, b) => b.importanceScore - a.importanceScore).slice(0, 5),
    eventFeed: eventFeed.slice(0, params.limit || 20),
    hotSectors: MOCK_HOT_SECTORS.map((item) => ({...item})),
    eventDistribution: mockDistribution(params),
    trackingEvents: eventFeed.filter((item) => item.status === "tracking").slice(0, 5),
    generatedAt: new Date().toISOString(),
    dataFreshness: {},
    dataMode: "mock",
  };
}

function filterMockEvents(events, params) {
  const query = String(params.query || "").trim().toLowerCase();
  const category = params.category || "all";
  const hours = Number(params.hours || 24);
  const threshold = Date.now() - hours * 60 * 60 * 1000;
  return events
    .filter((item) => category === "all" || item.category === category)
    .filter((item) => new Date(item.occurredAt).getTime() >= threshold)
    .filter((item) => {
      if (!query) return true;
      return [
        item.title, item.factSummary, item.importanceReason,
        ...item.sectors, ...item.concepts, ...item.companies,
      ].join(" ").toLowerCase().includes(query);
    })
    .sort((a, b) => new Date(b.updatedAt || b.occurredAt) - new Date(a.updatedAt || a.occurredAt))
    .map((item) => ({...item, timeline: item.timeline?.map((row) => ({...row}))}));
}

function mockDistribution(params) {
  const events = filterMockEvents(MOCK_MARKET_EVENTS, {...params, category: "all", query: ""});
  const categories = ["policy", "industry", "company", "earnings", "overseas", "risk"];
  return categories.map((category) => {
    const count = events.filter((item) => item.category === category).length;
    return {category, count, percentage: events.length ? Math.round((count / events.length) * 100) : 0};
  });
}

function mockSources(event) {
  return [{
    id: `${event.id}-source`,
    sourceType: "news",
    publisher: "演示来源",
    title: event.title,
    url: null,
    summary: event.factSummary.slice(0, 160),
    publishedAt: event.occurredAt,
    retrievedAt: event.firstSeenAt,
    isPrimarySource: false,
    authorityScore: 60,
    timeMissing: false,
  }];
}

function toQuery(values) {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  });
  return params.toString();
}

function toTimeRange(hours) {
  const value = Number(hours || 24);
  if (value <= 1) return "1h";
  if (value <= 6) return "6h";
  if (value <= 24) return "24h";
  if (value <= 72) return "3d";
  if (value <= 168) return "7d";
  return "30d";
}

function safeExternalUrl(value) {
  if (!value) return null;
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch (_) {
    return null;
  }
}

function inferApiBase() {
  if (window.CHAINALPHA_API_BASE) return window.CHAINALPHA_API_BASE;
  if (location.port === "5173" || location.port === "5174") return "http://127.0.0.1:8000";
  return "";
}

function maybeThrowDevelopmentError() {
  if (new URLSearchParams(location.search).get("mockError") === "1") {
    throw new EventApiError("Mock 服务暂时不可用");
  }
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
