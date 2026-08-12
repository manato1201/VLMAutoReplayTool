const statusPill = document.getElementById("status-pill");
const goalInput = document.getElementById("goal-input");
const decomposeBtn = document.getElementById("decompose-btn");
const todoListEl = document.getElementById("todo-list");
const logFeedEl = document.getElementById("log-feed");
const skillListEl = document.getElementById("skill-list");
const watchdogInfoEl = document.getElementById("watchdog-info");
const stopBtn = document.getElementById("stop-btn");
const runErrorEl = document.getElementById("run-error");

async function postJson(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `${url} failed: ${res.status}`);
  }
  return res.json();
}

function renderTodos(todos, activeTodoId, status) {
  todoListEl.innerHTML = "";
  if (todos.length === 0) {
    todoListEl.innerHTML = '<p class="empty-hint">まだTODOがありません。ゴールを入力して分解してください。</p>';
    return;
  }
  for (const todo of todos) {
    const card = document.createElement("div");
    card.className = "todo-card" + (todo.todoId === activeTodoId ? " active" : "");
    const info = document.createElement("div");
    info.innerHTML = `<div class="todo-desc">${escapeHtml(todo.description)}</div><div class="todo-done">完了条件: ${escapeHtml(todo.doneCriteria)}</div>`;
    const btn = document.createElement("button");
    btn.className = "btn-small";
    btn.textContent = "実行開始";
    btn.disabled = status === "running";
    btn.onclick = async () => {
      runErrorEl.textContent = "";
      try {
        await postJson("/api/run/start", { todoId: todo.todoId, maxSteps: 12 });
      } catch (e) {
        runErrorEl.textContent = e.message;
      }
    };
    card.appendChild(info);
    card.appendChild(btn);
    todoListEl.appendChild(card);
  }
}

function renderLogs(logs) {
  logFeedEl.innerHTML = "";
  if (logs.length === 0) {
    logFeedEl.innerHTML = '<p class="empty-hint">まだ実行ログがありません。</p>';
    return;
  }
  for (const log of logs) {
    const entry = document.createElement("div");
    entry.className = "log-entry";
    entry.innerHTML = `
      <div class="log-step">STEP ${log.stepIndex} — ${escapeHtml(log.timestamp)}</div>
      <div class="log-reasoning">${escapeHtml(log.reasoning)}</div>
      <div class="log-action">action: ${escapeHtml(JSON.stringify(log.actionTaken))}</div>
      <div class="log-summary">result: ${escapeHtml(log.resultObservationSummary)}</div>
    `;
    logFeedEl.appendChild(entry);
  }
}

function renderSkills(skills) {
  skillListEl.innerHTML = "";
  if (skills.length === 0) {
    skillListEl.innerHTML = '<p class="empty-hint">スキルライブラリは空です。</p>';
    return;
  }
  for (const skill of skills) {
    const card = document.createElement("div");
    card.className = "skill-card";
    card.innerHTML = `<div><div class="todo-desc">${escapeHtml(skill.skillId)}</div><div class="todo-done">type=${skill.type} / createdBy=${skill.createdBy}</div></div>`;
    skillListEl.appendChild(card);
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

async function refresh() {
  const res = await fetch("/api/status");
  const snapshot = await res.json();

  statusPill.textContent = `STATUS: ${snapshot.status.toUpperCase()}`;
  statusPill.classList.toggle("running", snapshot.status === "running");

  renderTodos(snapshot.todos, snapshot.activeTodoId, snapshot.status);
  renderLogs(snapshot.logs);
  renderSkills(snapshot.skills);

  watchdogInfoEl.textContent = `同一TODO継続ステップ数がしきい値(${snapshot.watchdogThreshold})に達するか、diagnose_stallが回復不能と判断すると自動でTODOを再構築します。`;

  stopBtn.disabled = snapshot.status !== "running";
  if (snapshot.error) {
    runErrorEl.textContent = snapshot.error;
  }
}

decomposeBtn.onclick = async () => {
  const goal = goalInput.value.trim();
  if (!goal) return;
  runErrorEl.textContent = "";
  try {
    await postJson("/api/todo/decompose", { goal });
  } catch (e) {
    runErrorEl.textContent = e.message;
  }
};

stopBtn.onclick = async () => {
  await postJson("/api/run/stop", {});
};

refresh();
setInterval(refresh, 1200);
