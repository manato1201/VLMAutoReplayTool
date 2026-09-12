// Phase8: モニタリングHUD。/ws/hud からの片方向配信のみを消費する(サーバへの書き込みは行わない)。

const wsStatusEl = document.getElementById("ws-status");
const todoListEl = document.getElementById("hud-todo-list");
const dotChartEl = document.getElementById("hud-dot-chart");
const logFeedEl = document.getElementById("hud-log-feed");
const skillListEl = document.getElementById("hud-skill-list");
const toastStackEl = document.getElementById("toast-stack");
const radialFillEl = document.getElementById("radial-fill");
const radialLabelEl = document.getElementById("radial-label");

const RADIAL_CIRCUMFERENCE = 100.5; // 2 * PI * r(16) の近似値。design仕様の値をそのまま使う。

const state = {
  todos: [],
  completedTodoIds: [],
  stepLogs: [],
  skills: [],
  stallStepIndices: new Set(),
  lastStepTodoId: null,
};

function escapeHtml(value) {
  return String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

// --- 1. セグメントレール: 単一のWebSocket購読を共有し、表示対象の切り替えのみ行う ---
const views = {
  todo: document.getElementById("view-todo"),
  steplog: document.getElementById("view-steplog"),
  skills: document.getElementById("view-skills"),
};
for (const btn of document.querySelectorAll(".segment-rail button")) {
  btn.onclick = () => {
    for (const other of document.querySelectorAll(".segment-rail button")) {
      other.setAttribute("aria-selected", String(other === btn));
    }
    for (const [name, el] of Object.entries(views)) {
      el.hidden = name !== btn.dataset.view;
    }
  };
}

// --- 2. フォーカスリング ---
function renderTodoView() {
  todoListEl.innerHTML = "";
  if (state.todos.length === 0) {
    todoListEl.innerHTML = '<p class="empty-hint">まだTODOがありません。操作パネルでゴールを分解してください。</p>';
    return;
  }
  for (const todo of state.todos) {
    const card = document.createElement("div");
    card.className = "todo-card" + (todo.todoId === state.lastStepTodoId ? " focused" : "");
    const done = state.completedTodoIds.includes(todo.todoId);
    card.innerHTML = `
      <div>
        <div class="todo-desc">${done ? "✅ " : ""}${escapeHtml(todo.description)}</div>
        <div class="todo-done">完了条件: ${escapeHtml(todo.doneCriteria)}</div>
      </div>
    `;
    todoListEl.appendChild(card);
  }
}

// --- 3. ドットチャート ---
function renderDotChart() {
  dotChartEl.innerHTML = "";
  for (const log of state.stepLogs) {
    const dot = document.createElement("span");
    dot.className = "dot" + (state.stallStepIndices.has(log.stepIndex) ? " stall" : "");
    dot.title = `step ${log.stepIndex}: ${log.reasoning}`;
    dotChartEl.appendChild(dot);
  }
}

function renderLogFeed() {
  logFeedEl.innerHTML = "";
  if (state.stepLogs.length === 0) {
    logFeedEl.innerHTML = '<p class="empty-hint">まだ実行ログがありません。</p>';
    return;
  }
  for (const log of [...state.stepLogs].reverse()) {
    const entry = document.createElement("div");
    entry.className = "log-entry";
    const thumb = document.createElement("img");
    thumb.className = "log-thumb";
    thumb.alt = `step ${log.stepIndex} observation`;
    thumb.src = `/api/observation/${encodeURIComponent(log.observationRef)}`;
    const body = document.createElement("div");
    body.className = "log-body";
    body.innerHTML = `
      <div class="log-step">STEP ${log.stepIndex}${state.stallStepIndices.has(log.stepIndex) ? " — Watchdog介入" : ""}</div>
      <div class="log-reasoning">${escapeHtml(log.reasoning)}</div>
      <div class="log-summary">result: ${escapeHtml(log.resultObservationSummary)}</div>
    `;
    entry.appendChild(thumb);
    entry.appendChild(body);
    logFeedEl.appendChild(entry);
  }
}

// --- 5. ラジアルプログレス ---
function renderRadialProgress() {
  const total = state.todos.length;
  const done = state.completedTodoIds.length;
  const ratio = total > 0 ? done / total : 0;
  radialFillEl.setAttribute("stroke-dashoffset", String(RADIAL_CIRCUMFERENCE * (1 - ratio)));
  radialLabelEl.textContent = `${done} / ${total}`;
}

function renderSkillsView() {
  skillListEl.innerHTML = "";
  if (state.skills.length === 0) {
    skillListEl.innerHTML = '<p class="empty-hint">スキルライブラリは空です。</p>';
    return;
  }
  for (const skill of state.skills) {
    const card = document.createElement("div");
    card.className = "skill-card";
    const text = skill.type === "procedure" ? skill.proceduralText : skill.scriptCode;
    card.innerHTML = `
      <div>
        <div class="todo-desc">${escapeHtml(skill.gameTitle)} — ${escapeHtml(skill.skillId)}</div>
        <div class="todo-done">type=${skill.type} / createdBy=${skill.createdBy}</div>
        <pre class="skill-procedural-text">${escapeHtml(text || "")}</pre>
      </div>
    `;
    skillListEl.appendChild(card);
  }
}

// --- 4. スタックトースト ---
function pushToast(title, body, variant) {
  const toast = document.createElement("div");
  toast.className = "toast" + (variant === "stall" ? " stall" : "");
  toast.innerHTML = `<div class="toast-title">${escapeHtml(title)}</div><div>${escapeHtml(body)}</div>`;
  toastStackEl.appendChild(toast);
  setTimeout(() => toast.remove(), 8000);
}

// 単一のWebSocketイベントをstateへ適用し、影響を受けるビューだけ再描画する。
// hydrate()からの初期スナップショット適用と、ws.onmessageからのライブ更新の両方が
// このstate変更ロジックを共有する(セグメントレールと同じ「単一の状態源」原則)。
// run_started(新しいRun開始)とlogs_reset(新しいゴールの分解)はどちらも
// 「前のStepLogの蓄積を引き継がない」という同じ効果を持つ。stepIndexはRun/ゴールが
// 変わるたびに0から振り直されるため、クリアしないとドットチャート/ログフィードが
// 混在して誤表示になる。
function clearStepLogState() {
  state.stepLogs = [];
  state.stallStepIndices = new Set();
  state.lastStepTodoId = null;
  renderDotChart();
  renderLogFeed();
  renderTodoView();
}

function applyEvent(event) {
  switch (event.type) {
    case "run_started":
    case "logs_reset":
      clearStepLogState();
      break;

    case "todos":
      state.todos = event.todos;
      state.completedTodoIds = event.completedTodoIds;
      renderTodoView();
      renderRadialProgress();
      break;

    case "step_log":
      state.stepLogs.push(event.log);
      state.lastStepTodoId = event.log.todoId;
      renderDotChart();
      renderLogFeed();
      renderTodoView(); // フォーカスリングの対象が変わりうる
      break;

    case "watchdog_intervened":
      if (event.stepIndex !== null && event.stepIndex !== undefined) {
        state.stallStepIndices.add(event.stepIndex);
      }
      renderDotChart();
      renderLogFeed();
      pushToast("Watchdog介入", event.diagnosis ? event.diagnosis.rootCause : event.message, "stall");
      break;

    case "experience":
      pushToast(event.experience.title, event.experience.summary, "ok");
      break;

    case "skills":
      state.skills = event.skills;
      renderSkillsView();
      break;

    default:
      break;
  }
}

// ページ読み込み時点(または再接続時点)で既に進行中のセッションが存在する場合、
// WebSocketは「これから起きること」しか配信しないため、そのままでは次のイベントが
// 届くまで画面が空のままになる。/api/status(既存のREST、ポーリング版ダッシュボードと
// 同じもの)から現在のスナップショットを取得して初期状態を埋める。
//
// ハイドレーション中(fetch応答待ち)に届いたライブイベントは`pending`に退避し、
// スナップショット適用後にまとめて再生する — そうしないと、フェッチ完了時に
// スナップショットで丸ごと上書きしてしまい、その間に届いたイベントを取りこぼす。
async function hydrate(pending) {
  try {
    const res = await fetch("/api/status");
    if (!res.ok) return;
    const snapshot = await res.json();

    state.todos = snapshot.todos;
    state.completedTodoIds = snapshot.completedTodoIds;
    state.stepLogs = snapshot.logs;
    state.skills = snapshot.skills;
    if (snapshot.logs.length > 0) {
      state.lastStepTodoId = snapshot.logs[snapshot.logs.length - 1].todoId;
    }
    if (snapshot.lastIntervention) {
      state.stallStepIndices.add(snapshot.lastIntervention.stepCount - 1);
    }
  } catch {
    // ハイドレーションに失敗してもWebSocket配信は引き続き機能するため、致命的ではない。
  }

  for (const event of pending) {
    applyEvent(event);
  }
  renderTodoView();
  renderDotChart();
  renderLogFeed();
  renderSkillsView();
  renderRadialProgress();
}

function connect() {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${protocol}//${location.host}/ws/hud`);
  let hydrating = true;
  const pendingDuringHydration = [];

  ws.onopen = () => {
    wsStatusEl.textContent = "WS: 接続済み";
    wsStatusEl.classList.add("connected");
    hydrate(pendingDuringHydration).finally(() => {
      hydrating = false;
    });
  };

  ws.onclose = () => {
    wsStatusEl.textContent = "WS: 切断(再接続します…)";
    wsStatusEl.classList.remove("connected");
    setTimeout(connect, 1500);
  };

  ws.onmessage = (raw) => {
    const event = JSON.parse(raw.data);
    if (hydrating) {
      pendingDuringHydration.push(event);
      return;
    }
    applyEvent(event);
  };
}

connect();
