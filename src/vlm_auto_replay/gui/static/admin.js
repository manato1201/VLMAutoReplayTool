// 管理画面(admin だけ): スキルの追加・削除と、入力バックエンド(HID)の設定。
// サーバーが /admin.html・/admin.js を admin に限る(本番では他の役割には、このコードは配られない)。操作パネル(app.js)は読み取りだけ。

const skillListEl = document.getElementById("skill-list");
const skillGameTitleEl = document.getElementById("skill-game-title");
const skillProceduralTextEl = document.getElementById("skill-procedural-text");
const addSkillBtn = document.getElementById("add-skill-btn");
const skillErrorEl = document.getElementById("skill-error");
const hidBackendOptionsEl = document.getElementById("hid-backend-options");
const hidBackendErrorEl = document.getElementById("hid-backend-error");

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

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
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

function renderHidBackendOptions(hidBackend) {
  hidBackendOptionsEl.innerHTML = "";
  for (const option of hidBackend.options) {
    const label = document.createElement("label");
    label.className = "hid-backend-option" + (option.name === hidBackend.current ? " selected" : "");

    const radio = document.createElement("input");
    radio.type = "radio";
    radio.name = "hid-backend";
    radio.value = option.name;
    radio.checked = option.name === hidBackend.current;
    radio.onchange = async () => {
      hidBackendErrorEl.textContent = "";
      try {
        await postJson("/api/settings/hid-backend", { name: option.name });
        await refresh();
      } catch (e) {
        hidBackendErrorEl.textContent = e.message;
        await refresh(); // 失敗時も現在の選択状態(ラジオ)を最新化する
      }
    };

    const text = document.createElement("span");
    text.className = "todo-desc";
    text.textContent = option.label;

    label.appendChild(radio);
    label.appendChild(text);
    hidBackendOptionsEl.appendChild(label);
  }
  hidBackendErrorEl.textContent = hidBackend.error || "";
}

async function refresh() {
  const snapshot = await (await fetch("/api/status")).json();
  renderSkills(snapshot.skills);
  const settings = await (await fetch("/api/settings")).json();
  renderHidBackendOptions(settings.hidBackend);
}

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
setInterval(refresh, 3000);
