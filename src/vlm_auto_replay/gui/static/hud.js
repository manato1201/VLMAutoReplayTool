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

function connect() {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${protocol}//${location.host}/ws/hud`);

  ws.onopen = () => {
    wsStatusEl.textContent = "WS: 接続済み";
    wsStatusEl.classList.add("connected");
  };

  ws.onclose = () => {
    wsStatusEl.textContent = "WS: 切断(再接続します…)";
    wsStatusEl.classList.remove("connected");
    setTimeout(connect, 1500);
  };

  ws.onmessage = (raw) => {
    const event = JSON.parse(raw.data);
    switch (event.type) {
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
        pushToast(
          "Watchdog介入",
          event.diagnosis ? event.diagnosis.rootCause : event.message,
          "stall"
        );
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
  };
}

connect();
