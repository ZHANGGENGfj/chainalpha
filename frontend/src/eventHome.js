import {
  clearEventCache,
  createEventStream,
  eventDataMode,
  getEventDashboard,
  getEventDetail,
  getEventFeed,
  getEventTimeline,
  getHotSectors,
  getCollectorStatus,
  requestEventRefresh,
} from "./eventService.js";
import {
  renderBasicPage,
  renderDashboardHeader,
  renderDashboardSkeleton,
  renderEmptyState,
  renderErrorState,
  renderEventFeed,
  renderFeedHeader,
  renderFilterBar,
  renderRightRail,
  renderSidebar,
  renderTopEvents,
} from "./eventComponents.js";
import {renderDetailError, renderDetailSkeleton, renderEventDetail} from "./eventDetail.js";
import {isFollowed, toggleFollow} from "./followService.js";

const FEED_PAGE_SIZE = 20;
const SSE_RECOVERY_TIMEOUT_MS = 30_000;

export function mountEventWorkspace({route, navigate, appBase = ""}) {
  const detailEventId = decodeDetailEventId(route);
  const state = {
    route,
    query: "",
    feedQuery: "",
    category: "all",
    hours: 24,
    data: null,
    loading: !detailEventId,
    refreshing: false,
    loadingMore: false,
    error: "",
    lastUpdated: null,
    nextCursor: null,
    hasMore: false,
    totalEstimate: 0,
    newEventCount: 0,
    dataMode: eventDataMode(),
    connectionStatus: eventDataMode() === "mock" ? "polling" : "connecting",
    collectorStatus: null,
    collapsed: localStorage.getItem("chainalpha-sidebar-collapsed") === "1",
    mobileOpen: false,
    expandedEvents: new Set(),
    timelineEvents: new Set(),
    highlightEvent: "",
    detailEventId,
    detail: null,
    detailTimeline: [],
    detailLoading: Boolean(detailEventId),
    detailError: "",
    detailStatus: 0,
  };

  let requestController = null;
  let refreshTimer = null;
  let streamRecoveryTimer = null;
  let collectorTimer = null;
  let searchTimer = null;
  let stream = null;
  let reconnectAttempts = 0;
  let destroyed = false;

  render();
  bindShell();

  if (detailEventId) {
    loadDetail();
  } else if (route === "/" || route === "/events") {
    loadDashboard({initial: true}).then(startRealtime);
    document.addEventListener("visibilitychange", handleVisibility);
  }

  window.addEventListener("beforeunload", cleanup, {once: true});

  async function loadDashboard({initial = false, preserveNewCount = false} = {}) {
    requestController?.abort();
    requestController = new AbortController();
    if (initial) state.loading = true;
    else state.refreshing = true;
    state.error = "";
    renderMain();

    try {
      const params = dashboardParams();
      const [dashboard, feedPage, collectorStatus] = await Promise.all([
        getEventDashboard(params, {signal: requestController.signal, fresh: !initial}),
        getEventFeed({...params, cursor: null, limit: FEED_PAGE_SIZE}, {
          signal: requestController.signal,
          fresh: !initial,
        }),
        state.dataMode === "api"
          ? getCollectorStatus({signal: requestController.signal, fresh: true}).catch(() => state.collectorStatus)
          : Promise.resolve(null),
      ]);
      if (destroyed) return;
      state.data = {...dashboard, eventFeed: feedPage.items};
      state.nextCursor = feedPage.nextCursor;
      state.hasMore = feedPage.hasMore;
      state.totalEstimate = feedPage.totalEstimate;
      state.collectorStatus = collectorStatus;
      state.lastUpdated = newestDataTime(dashboard.dataFreshness) || parseApiTime(dashboard.generatedAt);
      if (!preserveNewCount) state.newEventCount = 0;
    } catch (error) {
      if (error.name === "AbortError") return;
      state.error = error.message || "加载失败";
    } finally {
      if (!destroyed) {
        state.loading = false;
        state.refreshing = false;
        renderMain();
      }
    }
  }

  async function loadMore() {
    if (!state.hasMore || !state.nextCursor || state.loadingMore) return;
    state.loadingMore = true;
    renderMain();
    try {
      const page = await getEventFeed({
        ...dashboardParams(),
        cursor: state.nextCursor,
        limit: FEED_PAGE_SIZE,
      });
      const known = new Set(state.data.eventFeed.map((item) => item.id));
      state.data.eventFeed.push(...page.items.filter((item) => !known.has(item.id)));
      state.nextCursor = page.nextCursor;
      state.hasMore = page.hasMore;
      state.totalEstimate = page.totalEstimate;
    } catch (error) {
      state.error = error.message || "加载更多失败";
    } finally {
      state.loadingMore = false;
      renderMain();
    }
  }

  async function loadDetail({fresh = false} = {}) {
    state.detailLoading = true;
    state.detailError = "";
    renderMain();
    const controller = new AbortController();
    requestController?.abort();
    requestController = controller;
    try {
      const [detail, timeline] = await Promise.all([
        getEventDetail(detailEventId, {signal: controller.signal, fresh}),
        getEventTimeline(detailEventId, {signal: controller.signal, fresh}),
      ]);
      state.detail = detail;
      state.detailTimeline = timeline;
      state.detailStatus = 200;
    } catch (error) {
      if (error.name === "AbortError") return;
      state.detailError = error.message || "事件详情加载失败";
      state.detailStatus = Number(error.status || 0);
    } finally {
      state.detailLoading = false;
      renderMain();
    }
  }

  function render() {
    document.querySelector("#app").innerHTML = `
      <div class="event-app-shell ${state.collapsed ? "sidebar-is-collapsed" : ""}">
        <div id="workspace-sidebar-root">
          ${renderSidebar({route, collapsed: state.collapsed, mobileOpen: state.mobileOpen, appBase})}
        </div>
        <div class="event-workspace">
          <div id="event-main-root"></div>
        </div>
      </div>
    `;
    renderMain();
  }

  function renderMain() {
    const root = document.querySelector("#event-main-root");
    if (!root) return;

    if (state.detailEventId) {
      if (state.detailLoading && !state.detail) root.innerHTML = renderDetailSkeleton();
      else if (state.detailError && !state.detail) {
        root.innerHTML = renderDetailError(state.detailError, state.detailStatus);
      } else if (state.detail) {
        root.innerHTML = renderEventDetail(state.detail, state.detailTimeline, {
          dataMode: state.dataMode,
          followed: isFollowed("event", state.detail.id),
        });
      }
      bindDetail();
      return;
    }

    if (route === "/sectors" || route === "/market-daily") {
      root.innerHTML = renderBasicPage(route, new URLSearchParams(location.search));
      bindRouteLinks(root);
      return;
    }
    if (state.loading && !state.data) {
      root.innerHTML = renderDashboardSkeleton();
      return;
    }
    if (state.error && !state.data) {
      root.innerHTML = renderErrorState(state.error);
      root.querySelector("[data-retry-dashboard]")?.addEventListener("click", () => loadDashboard({initial: true}));
      return;
    }

    const data = state.data || {
      topEvents: [], eventFeed: [], hotSectors: [], eventDistribution: [], trackingEvents: [],
    };
    const hasFilters = Boolean(state.query || state.feedQuery || state.category !== "all" || Number(state.hours) !== 24);
    const showTopEvents = route === "/";

    root.innerHTML = `
      <div class="event-dashboard">
        ${renderDashboardHeader({
          query: state.query,
          lastUpdated: state.lastUpdated,
          dataMode: state.dataMode,
          connectionStatus: state.connectionStatus,
          collectorStatus: state.collectorStatus,
        })}
        ${renderFilterBar({category: state.category, hours: state.hours, hasFilters})}
        ${showTopEvents ? renderTopEvents(data.topEvents) : ""}
        <div class="dashboard-layout">
          <main class="feed-column">
            ${renderFeedHeader({
              lastUpdated: state.lastUpdated,
              refreshing: state.refreshing,
              feedQuery: state.feedQuery,
            })}
            ${state.newEventCount > 0 ? `
              <button class="new-events-button" data-apply-new-events>
                有 ${state.newEventCount} 条新事件，点击加载
              </button>
            ` : ""}
            ${state.error ? `<div class="refresh-warning">刷新失败，当前继续显示上一次数据。</div>` : ""}
            ${data.eventFeed.length
              ? renderEventFeed(data.eventFeed, state.expandedEvents, state.timelineEvents)
              : renderEmptyState(hasFilters)}
            ${state.hasMore ? `
              <button class="load-more-button" data-load-more ${state.loadingMore ? "disabled" : ""}>
                ${state.loadingMore ? "正在加载…" : `加载更多${state.totalEstimate ? `（约 ${state.totalEstimate} 条）` : ""}`}
              </button>
            ` : ""}
          </main>
          ${renderRightRail(data)}
        </div>
        <footer class="event-disclaimer">
          <p>本平台内容由规则系统自动整理，仅供信息参考，不构成投资建议。</p>
          <p>请以官方公告、公司财报及权威市场数据为准，谨慎决策。</p>
        </footer>
      </div>
    `;
    bindDashboard();
    restoreHighlight();
  }

  function bindShell() {
    const sidebarRoot = document.querySelector("#workspace-sidebar-root");
    bindRouteLinks(sidebarRoot);
    sidebarRoot?.querySelector("[data-collapse-sidebar]")?.addEventListener("click", () => {
      state.collapsed = !state.collapsed;
      localStorage.setItem("chainalpha-sidebar-collapsed", state.collapsed ? "1" : "0");
      render();
      bindShell();
    });
    sidebarRoot?.querySelector("[data-mobile-menu]")?.addEventListener("click", () => {
      state.mobileOpen = true;
      updateSidebar();
    });
    sidebarRoot?.querySelector("[data-close-menu]")?.addEventListener("click", () => {
      state.mobileOpen = false;
      updateSidebar();
    });
  }

  function updateSidebar() {
    const root = document.querySelector("#workspace-sidebar-root");
    root.innerHTML = renderSidebar({route, collapsed: state.collapsed, mobileOpen: state.mobileOpen, appBase});
    bindShell();
  }

  function bindDashboard() {
    const root = document.querySelector("#event-main-root");
    bindRouteLinks(root);
    root?.querySelector("[data-global-search]")?.addEventListener(
      "input",
      (event) => scheduleSearch("query", event.target.value),
    );
    root?.querySelector("[data-feed-search]")?.addEventListener(
      "input",
      (event) => scheduleSearch("feedQuery", event.target.value),
    );
    root?.querySelectorAll("[data-category]").forEach((button) => {
      button.addEventListener("click", () => {
        state.category = button.dataset.category;
        loadDashboard();
      });
      button.addEventListener("keydown", handleTabKeydown);
    });
    root?.querySelector("[data-hours]")?.addEventListener("change", (event) => {
      state.hours = Number(event.target.value);
      loadDashboard();
    });
    root?.querySelectorAll("[data-clear-filters]").forEach((button) => {
      button.addEventListener("click", clearFilters);
    });
    root?.querySelectorAll("[data-follow-custom], [data-follow-global]").forEach((button) => {
      button.addEventListener("click", () => {
        button.classList.toggle("selected");
        button.setAttribute("aria-pressed", button.classList.contains("selected") ? "true" : "false");
      });
    });
    root?.querySelector("[data-trigger-collection]")?.addEventListener("click", triggerCollection);
    root?.querySelectorAll("[data-jump-event]").forEach((element) => {
      element.addEventListener("click", () => jumpToEvent(element.dataset.jumpEvent));
      element.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          jumpToEvent(element.dataset.jumpEvent);
        }
      });
    });
    root?.querySelectorAll("[data-expand-event]").forEach((button) => {
      button.addEventListener("click", () => toggleSet(state.expandedEvents, button.dataset.expandEvent));
    });
    root?.querySelectorAll("[data-open-event-detail]").forEach((button) => {
      button.addEventListener("click", () => navigate(`/events/${encodeURIComponent(button.dataset.openEventDetail)}`));
    });
    root?.querySelector("[data-load-more]")?.addEventListener("click", loadMore);
    root?.querySelector("[data-apply-new-events]")?.addEventListener("click", () => {
      clearEventCache("/api/events");
      loadDashboard({preserveNewCount: false});
    });
    root?.querySelectorAll("[data-market-session]").forEach((link) => {
      link.addEventListener("click", (event) => {
        event.preventDefault();
        navigate(`/market-daily?session=${encodeURIComponent(link.dataset.marketSession)}`);
      });
    });
  }

  function bindDetail() {
    const root = document.querySelector("#event-main-root");
    bindRouteLinks(root);
    root?.querySelector("[data-retry-detail]")?.addEventListener("click", () => loadDetail({fresh: true}));
    root?.querySelector("[data-follow-event]")?.addEventListener("click", (event) => {
      toggleFollow("event", event.currentTarget.dataset.followEvent);
      renderMain();
    });
  }

  function bindRouteLinks(root) {
    root?.querySelectorAll("[data-route]").forEach((link) => {
      link.addEventListener("click", (event) => {
        event.preventDefault();
        navigate(link.dataset.route);
      });
    });
  }

  function scheduleSearch(key, value) {
    state[key] = value;
    clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => loadDashboard(), 260);
  }

  function clearFilters() {
    state.query = "";
    state.feedQuery = "";
    state.category = "all";
    state.hours = 24;
    loadDashboard();
  }

  function toggleSet(set, id) {
    set.has(id) ? set.delete(id) : set.add(id);
    renderMain();
  }

  function jumpToEvent(id) {
    const eventExists = state.data?.eventFeed.some((item) => item.id === id);
    if (!eventExists) {
      navigate(`/events/${encodeURIComponent(id)}`);
      return;
    }
    state.highlightEvent = id;
    document.querySelector(`#event-${CSS.escape(id)}`)?.scrollIntoView({behavior: "smooth", block: "center"});
    restoreHighlight();
  }

  function restoreHighlight() {
    if (!state.highlightEvent) return;
    const card = document.querySelector(`#event-${CSS.escape(state.highlightEvent)}`);
    card?.classList.add("highlighted");
    window.setTimeout(() => {
      card?.classList.remove("highlighted");
      if (state.highlightEvent === card?.dataset.eventId) state.highlightEvent = "";
    }, 1800);
  }

  function startRealtime() {
    if (destroyed) return;
    if (state.dataMode === "mock" || typeof EventSource === "undefined") {
      state.connectionStatus = "polling";
      renderMain();
      refreshTimer = window.setInterval(() => {
        if (!document.hidden) loadDashboard({preserveNewCount: true});
      }, 30_000);
      return;
    }
    startCollectorStatusPolling();
    connectStream();
  }

  function startCollectorStatusPolling() {
    if (collectorTimer || state.dataMode !== "api") return;
    collectorTimer = window.setInterval(refreshCollectorStatus, 5_000);
  }

  async function refreshCollectorStatus() {
    try {
      const previous = state.collectorStatus;
      state.collectorStatus = await getCollectorStatus({fresh: true});
      const justCompleted = previous?.running
        && !state.collectorStatus.running
        && state.collectorStatus.state === "completed";
      if (justCompleted) clearEventCache();
      renderMain();
    } catch (_) {
      // Collection status must not hide otherwise usable event data.
    }
  }

  async function triggerCollection() {
    try {
      state.collectorStatus = await requestEventRefresh();
      state.collectorStatus.running = true;
      state.collectorStatus.message = "新闻采集任务已提交";
      renderMain();
    } catch (error) {
      state.error = error.message || "无法启动新闻采集";
      renderMain();
    }
  }

  function connectStream() {
    if (destroyed || document.hidden || stream) return;
    state.connectionStatus = reconnectAttempts ? "reconnecting" : "connecting";
    renderMain();
    stream = createEventStream({
      onOpen: () => {
        const wasReconnect = reconnectAttempts > 0;
        reconnectAttempts = 0;
        clearTimeout(streamRecoveryTimer);
        streamRecoveryTimer = null;
        state.connectionStatus = "connected";
        renderMain();
        if (wasReconnect) {
          clearEventCache();
          loadDashboard({preserveNewCount: true});
        }
      },
      onError: () => {
        // EventSource already reconnects using the retry interval sent by the
        // server. Closing it here created a second reconnect loop and made the
        // dashboard visibly reconnect several times before settling.
        if (destroyed || document.hidden || !stream) return;
        reconnectAttempts += 1;
        if (state.connectionStatus !== "reconnecting") {
          state.connectionStatus = "reconnecting";
          renderMain();
        }
        schedulePollingFallback();
      },
      onEvent: handleStreamEvent,
    });
    if (stream.mode !== "sse") {
      stream = null;
      state.connectionStatus = "polling";
      startPollingFallback();
    }
  }

  function schedulePollingFallback() {
    if (streamRecoveryTimer) return;
    streamRecoveryTimer = window.setTimeout(() => {
      streamRecoveryTimer = null;
      if (destroyed || document.hidden || state.connectionStatus === "connected") return;
      stream?.close();
      stream = null;
      state.connectionStatus = "polling";
      renderMain();
      startPollingFallback();
    }, SSE_RECOVERY_TIMEOUT_MS);
  }

  function startPollingFallback() {
    if (refreshTimer) return;
    refreshTimer = window.setInterval(() => {
      if (!document.hidden) loadDashboard({preserveNewCount: true});
    }, 30_000);
  }

  async function handleStreamEvent(type, payload) {
    if (type === "heartbeat") return;
    if (type === "event.created") {
      state.newEventCount += 1;
      renderMain();
      return;
    }
    if (["event.updated", "event.corrected", "event.status_changed"].includes(type) && payload.event_id) {
      try {
        clearEventCache(`/api/events/${payload.event_id}`);
        const changed = await getEventDetail(payload.event_id, {fresh: true});
        replaceEvent(changed);
        renderMain();
      } catch (_) {
        state.newEventCount += 1;
        renderMain();
      }
      return;
    }
    if (type === "sector.heat_updated" && state.data) {
      try {
        clearEventCache("/api/sectors/hot");
        state.data.hotSectors = await getHotSectors({
          hours: state.hours,
          category: state.category,
          limit: 5,
        }, {fresh: true});
        renderMain();
      } catch (_) {
        // Keep the previous heat snapshot visible.
      }
    }
  }

  function replaceEvent(changed) {
    ["eventFeed", "topEvents", "trackingEvents"].forEach((collection) => {
      const rows = state.data?.[collection];
      if (!rows) return;
      const index = rows.findIndex((item) => item.id === changed.id);
      if (index >= 0) rows.splice(index, 1, changed);
    });
  }

  function handleTabKeydown(event) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const tabs = [...document.querySelectorAll("[data-category]")];
    const current = tabs.indexOf(event.currentTarget);
    let next = current;
    if (event.key === "ArrowRight") next = (current + 1) % tabs.length;
    if (event.key === "ArrowLeft") next = (current - 1 + tabs.length) % tabs.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = tabs.length - 1;
    event.preventDefault();
    tabs[next].focus();
    tabs[next].click();
  }

  function handleVisibility() {
    if (document.hidden) {
      stream?.close();
      stream = null;
      clearTimeout(streamRecoveryTimer);
      streamRecoveryTimer = null;
      return;
    }
    clearEventCache();
    loadDashboard({preserveNewCount: true}).then(() => {
      if (state.dataMode === "api" && !stream) connectStream();
    });
  }

  function dashboardParams() {
    return {
      query: [state.query, state.feedQuery].filter(Boolean).join(" "),
      category: state.category,
      hours: state.hours,
      limit: FEED_PAGE_SIZE,
    };
  }

  function cleanup() {
    destroyed = true;
    requestController?.abort();
    stream?.close();
    clearInterval(refreshTimer);
    clearInterval(collectorTimer);
    clearTimeout(streamRecoveryTimer);
    clearTimeout(searchTimer);
    document.removeEventListener("visibilitychange", handleVisibility);
  }
}

function decodeDetailEventId(route) {
  const match = route.match(/^\/events\/([^/]+)$/);
  if (!match) return null;
  try {
    return decodeURIComponent(match[1]);
  } catch (_) {
    return match[1];
  }
}

function parseApiTime(value) {
  if (!value) return new Date();
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? new Date() : parsed;
}

function newestDataTime(freshness = {}) {
  const values = Object.values(freshness)
    .filter(Boolean)
    .map((value) => new Date(value))
    .filter((value) => !Number.isNaN(value.getTime()));
  return values.length ? new Date(Math.max(...values.map((value) => value.getTime()))) : null;
}
