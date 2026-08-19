export function mountAuthPage({onAuthenticated}) {
  let mode = "login";
  const root = document.querySelector("#app");

  function render(error = "") {
    root.innerHTML = `
      <main class="auth-shell">
        <section class="auth-panel" aria-labelledby="auth-title">
          <div class="auth-brand"><span>CA</span><strong>ChainAlpha</strong></div>
          <p class="auth-eyebrow">INVITE-ONLY RESEARCH WORKSPACE</p>
          <h1 id="auth-title">${mode === "login" ? "登录研究工作台" : "使用邀请码注册"}</h1>
          <p class="auth-description">${mode === "login" ? "登录后查看属于你的研究会话、运行记录和报告。" : "请输入管理员发放的邀请码创建账户。"}</p>
          <form id="auth-form" class="auth-form">
            <label>用户名<input name="username" autocomplete="username" minlength="3" maxlength="40" required /></label>
            <label>密码<input name="password" type="password" autocomplete="${mode === "login" ? "current-password" : "new-password"}" minlength="8" required /></label>
            ${mode === "register" ? '<label>邀请码<input name="invite_code" autocomplete="off" required /></label>' : ""}
            ${error ? `<p class="auth-error" role="alert">${escapeHtml(error)}</p>` : ""}
            <button class="auth-submit" type="submit">${mode === "login" ? "登录" : "创建账户"}</button>
          </form>
          <button class="auth-switch" id="auth-switch" type="button">${mode === "login" ? "没有账户？使用邀请码注册" : "已有账户？返回登录"}</button>
        </section>
      </main>
    `;
    root.querySelector("#auth-switch").addEventListener("click", () => { mode = mode === "login" ? "register" : "login"; render(); });
    root.querySelector("#auth-form").addEventListener("submit", submit);
  }

  async function submit(event) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const payload = Object.fromEntries(form.entries());
    const endpoint = mode === "login" ? "/api/auth/login" : "/api/auth/register";
    const response = await fetch(endpoint, {
      method: "POST",
      credentials: "same-origin",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      render(result.detail || "请求失败，请稍后重试。");
      return;
    }
    onAuthenticated();
  }

  render();
}

function escapeHtml(value) {
  return String(value || "").replace(/[&<>'"]/g, (character) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"}[character]));
}
