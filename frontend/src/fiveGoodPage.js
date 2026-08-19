import {renderSidebar} from "./eventComponents.js";
import {getFiveGoodOverview, getFiveGoodStockDetail, getFiveGoodStocks} from "./fiveGoodService.js";
import {renderDrawer, renderEmpty, renderError, renderFiveGoodFilters, renderFiveGoodHeader, renderFiveGoodTabs, renderOverview, renderPoolHeader, renderPoolTable, renderSkeleton} from "./fiveGoodComponents.js";

export function mountFiveGoodPage({navigate, appBase = ""}) {
  const state = {
    activeTab: new URLSearchParams(location.search).get("pool") || "overview",
    overview: null, list: null, loading: true, error: "", filtersByTab: {},
    collapsed: localStorage.getItem("chainalpha-sidebar-collapsed") === "1",
    mobileOpen: false, drawer: null, drawerLoading: false, drawerError: "",
  };
  let controller = null;
  let searchTimer = null;
  ensureFilters(state.activeTab);
  renderShell();
  loadActive();

  function defaultFilters() { return {keyword: "", market: "", industry: "", sector: "", status: "", updatedWithin: "all"}; }
  function ensureFilters(tab) { if (!state.filtersByTab[tab]) state.filtersByTab[tab] = defaultFilters(); return state.filtersByTab[tab]; }

  async function loadActive() {
    controller?.abort();
    controller = new AbortController();
    state.loading = true;
    state.error = "";
    renderMain();
    try {
      if (!state.overview) state.overview = await getFiveGoodOverview({signal: controller.signal});
      if (state.activeTab !== "overview") {
        state.list = await getFiveGoodStocks({category: state.activeTab, ...ensureFilters(state.activeTab), limit: 100}, {signal: controller.signal});
      }
    } catch (error) {
      if (error.name !== "AbortError") state.error = error.message || "五好精选加载失败";
    } finally {
      state.loading = false;
      renderMain();
    }
  }

  async function openDrawer(stockCode) {
    state.drawer = null;
    state.drawerLoading = true;
    state.drawerError = "";
    renderDrawerRoot();
    try { state.drawer = await getFiveGoodStockDetail(stockCode); }
    catch (error) { state.drawerError = error.message || "股票详情加载失败"; }
    finally { state.drawerLoading = false; renderDrawerRoot(); }
  }

  function renderShell() {
    document.querySelector("#app").innerHTML = `<div class="event-app-shell ${state.collapsed ? "sidebar-is-collapsed" : ""}">
      <div id="workspace-sidebar-root">${renderSidebar({route: "/five-good", collapsed: state.collapsed, mobileOpen: state.mobileOpen, appBase})}</div>
      <main class="event-workspace"><div id="five-good-root"></div></main><div id="five-good-drawer-root"></div></div>`;
    bindSidebar();
    renderMain();
  }

  function renderMain() {
    const root = document.querySelector("#five-good-root");
    if (!root) return;
    let content;
    if (state.loading && !state.overview) content = renderSkeleton();
    else if (state.error && !state.overview) content = renderError(state.error);
    else if (state.activeTab === "overview") content = renderOverview(state.overview);
    else {
      const filters = ensureFilters(state.activeTab);
      const body = state.loading ? renderSkeleton() : state.error ? renderError(state.error) : `${renderPoolHeader(state.activeTab, state.list?.totalEstimate || 0)}${state.list?.items?.length ? renderPoolTable(state.activeTab, state.list.items) : renderEmpty()}`;
      content = `${renderFiveGoodFilters(filters, state.list || {})}${body}`;
    }
    root.innerHTML = `<div class="five-good-page">${renderFiveGoodHeader(state.overview)}${renderFiveGoodTabs(state.activeTab, state.overview?.categoryCounts || {})}<div class="five-good-content">${content}</div><footer class="fg-disclaimer">“五好精选”基于公开信息、数据指标及AI辅助分析形成，仅用于研究与信息展示，不构成投资建议。相关筛选结果会随市场行情、公司基本面和事件变化动态调整。</footer></div>`;
    bindMain();
  }

  function renderDrawerRoot() {
    const root = document.querySelector("#five-good-drawer-root");
    if (!root) return;
    root.innerHTML = renderDrawer(state.drawer, {loading: state.drawerLoading, error: state.drawerError});
    root.querySelectorAll("[data-close-drawer]").forEach((element) => element.addEventListener("click", closeDrawer));
    bindRouteLinks(root);
  }

  function bindMain() {
    const root = document.querySelector("#five-good-root");
    root?.querySelectorAll("[data-fg-tab]").forEach((button) => button.addEventListener("click", () => {
      state.activeTab = button.dataset.fgTab;
      ensureFilters(state.activeTab);
      history.replaceState({}, "", `${appBase}${state.activeTab === "overview" ? "/five-good" : `/five-good?pool=${state.activeTab}`}`);
      state.list = null;
      loadActive();
    }));
    root?.querySelectorAll("[data-fg-filter]").forEach((control) => {
      control.addEventListener(control.tagName === "INPUT" ? "input" : "change", () => {
        ensureFilters(state.activeTab)[control.dataset.fgFilter] = control.value;
        clearTimeout(searchTimer);
        searchTimer = setTimeout(loadActive, control.tagName === "INPUT" ? 280 : 0);
      });
    });
    root?.querySelectorAll("[data-fg-reset]").forEach((button) => button.addEventListener("click", () => { state.filtersByTab[state.activeTab] = defaultFilters(); loadActive(); }));
    root?.querySelectorAll("[data-open-stock]").forEach((row) => row.addEventListener("click", (event) => { if (!event.target.closest("a")) openDrawer(row.dataset.openStock); }));
    root?.querySelector("[data-fg-retry]")?.addEventListener("click", loadActive);
    bindRouteLinks(root);
  }

  function bindSidebar() {
    const root = document.querySelector("#workspace-sidebar-root");
    bindRouteLinks(root);
    root?.querySelector("[data-collapse-sidebar]")?.addEventListener("click", () => { state.collapsed = !state.collapsed; localStorage.setItem("chainalpha-sidebar-collapsed", state.collapsed ? "1" : "0"); renderShell(); });
    root?.querySelector("[data-mobile-menu]")?.addEventListener("click", () => { state.mobileOpen = true; renderShell(); });
    root?.querySelector("[data-close-menu]")?.addEventListener("click", () => { state.mobileOpen = false; renderShell(); });
  }

  function bindRouteLinks(root) { root?.querySelectorAll("[data-route]").forEach((link) => link.addEventListener("click", (event) => { event.preventDefault(); navigate(link.dataset.route); })); }
  function closeDrawer() { state.drawer = null; state.drawerLoading = false; state.drawerError = ""; renderDrawerRoot(); }
}
