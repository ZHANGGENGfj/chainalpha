const APP_BASE = location.pathname.startsWith("/app") ? "/app" : "";

bootstrap();

async function bootstrap() {
  const auth = await fetch("/api/auth/me", {credentials: "same-origin"})
    .then((response) => response.ok ? response.json() : {authenticated: false, auth_enabled: true})
    .catch(() => ({authenticated: false, auth_enabled: true}));
  if (auth.auth_enabled && !auth.authenticated) {
    const {mountAuthPage} = await import("./authPage.js");
    mountAuthPage({onAuthenticated: bootstrap});
    return;
  }
  mountWorkspace();
}

function mountWorkspace() {
  const route = normalizeRoute(location.pathname);
  if (route === "/" || route === "/five-good" || route.startsWith("/five-good/")) {
    import("./fiveGoodPage.js").then(({mountFiveGoodPage}) => {
      mountFiveGoodPage({navigate, appBase: APP_BASE});
    });
  } else if (route === "/research" || route.startsWith("/research/")) {
    import("./researchApp.js");
  } else {
    navigate("/five-good");
  }
}

window.addEventListener("popstate", () => location.reload());

function normalizeRoute(pathname) {
  const stripped = APP_BASE && pathname.startsWith(APP_BASE)
    ? pathname.slice(APP_BASE.length)
    : pathname;
  return stripped || "/";
}

function navigate(path) {
  const [pathname, query = ""] = path.split("?", 2);
  const params = new URLSearchParams(query);
  const explicitEventMode = new URLSearchParams(location.search).get("eventData");
  if (explicitEventMode && !params.has("eventData")) params.set("eventData", explicitEventMode);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  const target = `${APP_BASE}${pathname === "/" ? "/" : pathname}${suffix}`;
  if (`${location.pathname}${location.search}` === target) return;
  history.pushState({}, "", target);
  location.reload();
}
