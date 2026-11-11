// 認証(VLM_AUTH=required)用のログイン画面。off(既定)のときは何も表示しない。
//
// app.js / hud.js は変えない。/api/* が 401 を返したときだけ、ログインの重ね表示を出す。
// API キーは JS からは読めない HttpOnly・SameSite=Strict のクッキーになる(サーバー側で設定)。
// 権限の判断はサーバー側(403)。ここは「入口」と「誰で入っているか」の表示だけ。
(() => {
  const nativeFetch = window.fetch.bind(window);
  let overlay = null;

  function el(tag, props = {}, children = []) {
    const node = Object.assign(document.createElement(tag), props);
    for (const c of children) node.append(c);
    return node;
  }

  function showLogin(message) {
    if (overlay) return;
    const input = el("input", {
      id: "login-api-key",
      type: "password",
      autocomplete: "off",
      required: true,
      className: "text-input",
    });
    const error = el("p", { className: "error-text", role: "alert", textContent: message || "" });
    const submit = el("button", { type: "submit", className: "btn-primary", textContent: "ログイン" });
    const form = el("form", {}, [
      el("label", { htmlFor: "login-api-key", textContent: "API キー" }),
      input,
      submit,
      error,
    ]);
    Object.assign(form.style, { display: "grid", gap: "8px" });
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      submit.disabled = true;
      try {
        const res = await nativeFetch("/api/auth/login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ api_key: input.value.trim() }),
        });
        if (res.ok) {
          location.reload();
          return;
        }
        const detail = await res.json().catch(() => ({}));
        error.textContent = detail.detail || `ログインに失敗しました(${res.status})`;
        input.value = "";
      } finally {
        submit.disabled = false;
      }
    });
    const card = el("div", { className: "panel" }, [
      el("h2", { className: "panel-title", textContent: "ログインが必要です" }),
      el("p", { className: "caption", textContent: "管理者から受け取った API キーを入れてください。" }),
      form,
    ]);
    Object.assign(card.style, { width: "min(420px, calc(100vw - 32px))" });
    overlay = el("div", { role: "dialog", ariaModal: "true" }, [card]);
    Object.assign(overlay.style, {
      position: "fixed",
      inset: "0",
      zIndex: "1000",
      display: "grid",
      placeItems: "center",
      background: "rgba(21, 15, 35, 0.92)",
    });
    document.body.append(overlay);
    input.focus();
  }

  window.fetch = async (input, init) => {
    const res = await nativeFetch(input, init);
    const url = typeof input === "string" ? input : input.url;
    if (res.status === 401 && url.startsWith("/api/") && !url.startsWith("/api/auth/")) {
      showLogin();
    }
    return res;
  };

  // ---- 役割に足りない操作は、押せなくして理由を出す。権限の判断はサーバー側(403)で、これは表示だけ。
  // 一覧のボタンなど、app.js が後から作る要素にも効くよう、DOM の変化を見て当て直す。
  const RANK = { viewer: 0, operator: 1, admin: 2 };
  const GATES = [
    ["#goal-input, #decompose-btn, #stop-btn, #stall-demo-checkbox, #todo-list button", "operator"],
    ["#skill-game-title, #skill-procedural-text, #add-skill-btn, #skill-list button", "admin"],
    ["#hid-backend-options input, #hid-backend-options button, #hid-backend-options select", "admin"],
  ];

  function applyGates(role) {
    for (const [selector, minimum] of GATES) {
      if (RANK[role] >= RANK[minimum]) continue;
      for (const node of document.querySelectorAll(selector)) {
        if (!node.disabled) node.disabled = true; // すでに無効なら触らない(監視の無限ループを避ける)
        const text = `この操作には ${minimum} 以上の権限が必要です`;
        if (node.title !== text) node.title = text;
      }
    }
  }

  function gateByRole(role) {
    if (!(role in RANK) || role === "admin") return;
    let scheduled = false;
    const run = () => {
      scheduled = false;
      applyGates(role);
    };
    new MutationObserver(() => {
      if (!scheduled) {
        scheduled = true;
        setTimeout(run, 0); // requestAnimationFrame は、見えていないタブでは止まるので使わない
      }
    }).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["disabled"] });
    run();
  }

  // required で入っているときだけ、誰で入っているか(と、ログアウト)を右上に出す。
  nativeFetch("/api/auth/me")
    .then((r) => r.json())
    .then((me) => {
      if (me.mode !== "required" || !me.authenticated) return;
      gateByRole(me.role);
      // 開発者・デバッグ向け(監視 HUD)が使えない人には、入口のリンクを出さない(サーバーも同じ判断で 404 / 403)
      if (me.debug !== true) {
        document.querySelectorAll('a[href="/hud.html"]').forEach((node) => {
          node.hidden = true;
        });
      }
      const logout = el("button", { type: "button", className: "btn-primary", textContent: "ログアウト" });
      logout.addEventListener("click", async () => {
        await nativeFetch("/api/auth/logout", { method: "POST" });
        location.reload();
      });
      const strip = el("div", { className: "eyebrow", textContent: `${me.name}(${me.role}) ` }, [logout]);
      Object.assign(strip.style, { position: "fixed", right: "12px", bottom: "8px", zIndex: "900" });
      document.body.append(strip);
    })
    .catch(() => {});
})();
