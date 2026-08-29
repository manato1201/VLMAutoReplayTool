const statusPill = document.getElementById("status-pill");
const goalInput = document.getElementById("goal-input");
const decomposeBtn = document.getElementById("decompose-btn");
const todoListEl = document.getElementById("todo-list");
const logFeedEl = document.getElementById("log-feed");
const skillListEl = document.getElementById("skill-list");
const watchdogInfoEl = document.getElementById("watchdog-info");
const stopBtn = document.getElementById("stop-btn");
const runErrorEl = document.getElementById("run-error");
const progressBarEl = document.getElementById("progress-bar");
const progressLabelEl = document.getElementById("progress-label");
const skillGameTitleEl = document.getElementById("skill-game-title");
const skillProceduralTextEl = document.getElementById("skill-procedural-text");
const addSkillBtn = document.getElementById("add-skill-btn");
const skillErrorEl = document.getElementById("skill-error");
const historyListEl = document.getElementById("history-list");
const historyDetailEl = document.getElementById("history-detail");

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

async function deleteJson(url) {
  const res = await fetch(url, { method: "DELETE" });
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

function renderProgress(progress, stepsToWin) {
  const ratio = stepsToWin > 0 ? Math.min(progress / stepsToWin, 1) : 0;
  progressBarEl.style.width = `${Math.round(ratio * 100)}%`;
  progressLabelEl.textContent = `${progress} / ${stepsToWin}`;
}

function renderLogs(logs, container = logFeedEl, emptyMessage = "まだ実行ログがありません。") {
  container.innerHTML = "";
  if (logs.length === 0) {
    container.innerHTML = `<p class="empty-hint">${escapeHtml(emptyMessage)}</p>`;
    return;
  }
  for (const log of logs) {
    const entry = document.createElement("div");
    entry.className = "log-entry";

    const thumb = document.createElement("img");
    thumb.className = "log-thumb";
    thumb.alt = `step ${log.stepIndex} observation`;
    thumb.src = `/api/observation/${encodeURIComponent(log.observationRef)}`;

    const body = document.createElement("div");
    body.className = "log-body";
    body.innerHTML = `
      <div class="log-step">STEP ${log.stepIndex} — ${escapeHtml(log.timestamp)}</div>
      <div class="log-reasoning">${escapeHtml(log.reasoning)}</div>
      <div class="log-action">action: ${escapeHtml(JSON.stringify(log.actionTaken))}</div>
      <div class="log-summary">result: ${escapeHtml(log.resultObservationSummary)}</div>
    `;

    entry.appendChild(thumb);
    entry.appendChild(body);
    container.appendChild(entry);
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

    const info = document.createElement("div");
    const proceduralText = skill.type === "procedure" ? skill.proceduralText : skill.scriptCode;
    info.innerHTML = `
      <div class="todo-desc">${escapeHtml(skill.gameTitle)} — ${escapeHtml(skill.skillId)}</div>
      <div class="todo-done">type=${skill.type} / createdBy=${skill.createdBy}</div>
      <pre class="skill-procedural-text">${escapeHtml(proceduralText || "")}</pre>
    `;

    const deleteBtn = document.createElement("button");
    deleteBtn.className = "btn-delete";
    deleteBtn.textContent = "削除";
    deleteBtn.onclick = async () => {
      skillErrorEl.textContent = "";
      try {
        await deleteJson(`/api/skills/${encodeURIComponent(skill.skillId)}`);
        await refresh();
      } catch (e) {
        skillErrorEl.textContent = e.message;
      }
    };

    card.appendChild(info);
    card.appendChild(deleteBtn);
    skillListEl.appendChild(card);
  }
}

function renderHistory(runs) {
  historyListEl.innerHTML = "";
  if (runs.length === 0) {
    historyListEl.innerHTML = '<p class="empty-hint">まだ実行履歴がありません。</p>';
    return;
  }
  for (const run of runs) {
    const entry = document.createElement("div");
    entry.className = "history-entry";

    const meta = document.createElement("div");
    meta.className = "history-meta";
    meta.innerHTML = `<div class="todo-desc">${escapeHtml(run.todoDescription)}</div><div class="todo-done">${escapeHtml(run.startedAt)}</div>`;

    const status = document.createElement("span");
    status.className = `history-status status-${run.status}`;
    status.textContent = run.status;

    const viewBtn = document.createElement("button");
    viewBtn.className = "btn-small";
    viewBtn.textContent = "ログを見る";
    viewBtn.onclick = async () => {
      const res = await fetch(`/api/history/${encodeURIComponent(run.runId)}`);
      if (!res.ok) return;
      const detail = await res.json();
      renderLogs(detail.logs, historyDetailEl, "このRunにはログがありません。");
    };

    entry.appendChild(meta);
    entry.appendChild(status);
    entry.appendChild(viewBtn);
    historyListEl.appendChild(entry);
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
  statusPill.classList.toggle("error", snapshot.status === "error");

  renderTodos(snapshot.todos, snapshot.activeTodoId, snapshot.status);
  renderProgress(snapshot.progress, snapshot.stepsToWin);
  renderLogs(snapshot.logs);
  renderSkills(snapshot.skills);

  watchdogInfoEl.textContent = `同一TODO継続ステップ数がしきい値(${snapshot.watchdogThreshold})に達するか、diagnose_stallが回復不能と判断すると自動でTODOを再構築します。`;

  stopBtn.disabled = snapshot.status !== "running";
  if (snapshot.error) {
    runErrorEl.textContent = snapshot.error;
  }

  const historyRes = await fetch("/api/history");
  const history = await historyRes.json();
  renderHistory(history.runs);
}

decomposeBtn.onclick = async () => {
  const goal = goalInput.value.trim();
  if (!goal) return;
  runErrorEl.textContent = "";
  try {
    await postJson("/api/todo/decompose", { goal });
    await refresh();
  } catch (e) {
    runErrorEl.textContent = e.message;
  }
};

stopBtn.onclick = async () => {
  await postJson("/api/run/stop", {});
};

addSkillBtn.onclick = async () => {
  const proceduralText = skillProceduralTextEl.value.trim();
  if (!proceduralText) {
    skillErrorEl.textContent = "手順テキストを入力してください。";
    return;
  }
  skillErrorEl.textContent = "";
  try {
    await postJson("/api/skills", {
      gameTitle: skillGameTitleEl.value.trim() || "MyGame",
      proceduralText,
    });
    skillGameTitleEl.value = "";
    skillProceduralTextEl.value = "";
    await refresh();
  } catch (e) {
    skillErrorEl.textContent = e.message;
  }
};

refresh();
setInterval(refresh, 1200);
