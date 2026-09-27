"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString([], { dateStyle: "short", timeStyle: "medium" }) : "—");
const fmtDur = (s) => {
  s = Math.max(0, Math.round(s || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return h ? `${h}h ${m}m` : m ? `${m}m ${String(r).padStart(2, "0")}s` : `${r}s`;
};

let me = null;
let tab = "live";
let pollTimer = null;
let historyFilter = {};

async function api(path, opts = {}) {
  const res = await fetch(`/api${path}`, {
    credentials: "same-origin",
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 401) { showLogin(); throw new Error("unauthorized"); }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return res.json();
}

/* ---------------- auth ---------------- */
function showLogin() {
  me = null;
  clearInterval(pollTimer);
  $("#app").classList.add("hidden");
  $("#login").classList.remove("hidden");
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  $("#login-error").textContent = "";
  try {
    await api("/login", { method: "POST", body: { username: f.get("username"), password: f.get("password") } });
    e.target.reset();
    boot();
  } catch (err) {
    if (err.message !== "unauthorized") $("#login-error").textContent = err.message;
    else $("#login-error").textContent = "Invalid credentials";
  }
});

$("#logout").addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST", credentials: "same-origin" });
  showLogin();
});

async function boot() {
  try { me = await api("/me"); } catch { return; }
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  $("#me-name").textContent = `${me.display_name} · ${me.role}`;
  $("#brand-name").textContent = (me.assistant_name || "Alfred").toUpperCase();
  $$("[data-admin]").forEach((el) => el.classList.toggle("hidden", me.role !== "admin"));
  try { tab = localStorage.getItem("alfred-tab") || "live"; } catch {}
  if (tab === "people" && me.role !== "admin") tab = "live";
  switchTab(tab);
}

/* ---------------- tabs ---------------- */
$("#tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-tab]");
  if (b) switchTab(b.dataset.tab);
});

function switchTab(name) {
  tab = name;
  try { localStorage.setItem("alfred-tab", name); } catch {}
  $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$("[data-panel]").forEach((p) => p.classList.toggle("hidden", p.dataset.panel !== name));
  clearInterval(pollTimer);
  refresh();
  const every = { live: 2000, activity: 5000, health: 15000 }[name];
  if (every) pollTimer = setInterval(refresh, every);
}

async function refresh() {
  try {
    loadStats();
    await ({ live: loadLive, history: loadHistory, tasks: loadTasks, activity: loadActivity, health: loadHealth, people: loadPeople }[tab])();
  } catch (e) {
    if (e.message !== "unauthorized") console.error(e);
  }
}

/* ---------------- overview ---------------- */
async function loadStats() {
  const o = await api("/overview");
  const items = [
    ["Active now", o.active],
    ["Calls today", o.calls_today],
    ["Chats today", o.chats_today],
  ];
  if (o.blocked_today !== null) items.push(["Blocked today", o.blocked_today]);
  const ch = Object.entries(o.channels).map(([k, v]) => `<span class="tag ${v ? "ok" : "bad"}">${esc(k)}</span>`).join(" ");
  $("#stats").innerHTML =
    items.map(([k, v]) => `<div class="stat"><div class="v">${esc(v)}</div><div class="k">${esc(k)}</div></div>`).join("") +
    `<div class="stat"><div class="k" style="margin-bottom:6px">Channels · ${esc(o.model)}</div>${ch}</div>`;
}

/* ---------------- live ---------------- */
async function loadLive() {
  const rows = await api("/sessions/active");
  if (!rows.length) {
    $("#live-list").innerHTML = `<div class="empty">No calls or chats in progress.</div>`;
    return;
  }
  $("#live-list").innerHTML = rows.map((s) => {
    const state = s.speaking ? `<span class="tag busy">speaking</span>` : s.busy ? `<span class="tag busy">thinking</span>` : "";
    const tail = s.tail.map((t) => `<div class="${t.speaker === "user" ? "u" : "a"}"><b>${t.speaker === "user" ? esc(s.display_name.split(" ")[0]) : "Alfred"}:</b> ${esc(t.text)}</div>`).join("");
    const partial = s.partial ? `<div class="p">… ${esc(s.partial)}</div>` : "";
    return `<article class="card ${s.channel}">
      <header><span class="who">${esc(s.display_name)}</span>
        <span><span class="tag">${s.channel === "call" ? "📞 call" : "✈ telegram"} · ${esc(s.direction)}</span> ${state}</span></header>
      <div class="meta muted" style="font-size:12px;margin-bottom:8px">running ${fmtDur(s.duration_s)} · idle ${fmtDur(s.idle_s)}</div>
      <div class="tail">${tail || '<div class="p">waiting…</div>'}${partial}</div>
    </article>`;
  }).join("");
  $$(".tail").forEach((t) => (t.scrollTop = t.scrollHeight));
}

/* ---------------- history / recordings ---------------- */
async function loadHistory() {
  const tree = await api("/recordings/tree");
  $("#tree").innerHTML = tree.length ? tree.map((p) => `
    <div class="person"><div>${esc(p.display_name)}</div>
      ${p.days.map((d) => `<div class="day ${historyFilter.user_id === p.user_id && historyFilter.date === d.day ? "sel" : ""}"
        data-user="${p.user_id}" data-day="${esc(d.day)}"><span>${esc(d.day)}</span><span>${d.sessions}${d.calls ? ` · ${d.calls}📞` : ""}</span></div>`).join("")}
    </div>`).join("") : `<div class="empty">Nothing recorded yet.</div>`;
  await loadSessionList();
}

$("#tree").addEventListener("click", (e) => {
  const d = e.target.closest(".day");
  if (!d) return;
  const uid = Number(d.dataset.user);
  historyFilter = historyFilter.user_id === uid && historyFilter.date === d.dataset.day ? {} : { user_id: uid, date: d.dataset.day };
  loadHistory();
});

async function loadSessionList() {
  const q = new URLSearchParams();
  if (historyFilter.user_id) q.set("user_id", historyFilter.user_id);
  if (historyFilter.date) q.set("date", historyFilter.date);
  const rows = await api(`/sessions?${q}`);
  $("#history-title").textContent = historyFilter.date ? `Sessions on ${historyFilter.date}` : "Recent sessions";
  $("#session-list").innerHTML = rows.length ? rows.map((s) => `
    <div class="row click" data-id="${esc(s.id)}">
      <div><div>${esc(s.display_name)} <span class="tag">${esc(s.channel)}</span> ${s.recording_id ? `<span class="tag ok">🎙 ${fmtDur(s.duration)}</span>` : ""}</div>
        <div class="meta">${fmtTime(s.started_at)} · ${s.turns} turns · ${esc(s.status)}</div></div>
      <span class="muted">›</span>
    </div>`).join("") : `<div class="empty">No sessions.</div>`;
}

$("#session-list").addEventListener("click", async (e) => {
  const r = e.target.closest(".row");
  if (!r) return;
  $$("#session-list .row").forEach((x) => x.classList.toggle("sel", x === r));
  const s = await api(`/sessions/${encodeURIComponent(r.dataset.id)}`);
  const audio = s.recordings.map((rec) => `<audio controls preload="none" src="/api/recordings/${rec.id}/audio"></audio>
    <div class="muted" style="font-size:12px">Stereo: left = ${esc(s.display_name)}, right = Alfred · ${fmtDur(rec.duration)}</div>`).join("");
  const turns = s.turns.map((t) => `<div class="turn ${t.speaker}"><div class="sp">${t.speaker === "user" ? esc(s.display_name) : "Alfred"}<br>${new Date(t.ts).toLocaleTimeString()}</div><div class="tx">${esc(t.text)}</div></div>`).join("");
  const tools = s.tool_calls.map((t) => `<div class="tool">${t.is_error ? "✖" : "✔"} ${esc(t.tool_name)} ${esc(t.payload)}\n→ ${esc((t.result || "").slice(0, 1500))}</div>`).join("");
  const d = $("#session-detail");
  d.classList.remove("hidden");
  d.innerHTML = `<h2 style="margin-top:0">${esc(s.display_name)} · ${esc(s.channel)} · ${fmtTime(s.started_at)}</h2>
    ${audio}${turns || '<div class="muted">No transcript.</div>'}
    ${tools ? `<h2>Tool calls</h2>${tools}` : ""}`;
  d.scrollIntoView({ behavior: "smooth", block: "start" });
});

/* ---------------- tasks ---------------- */
async function loadTasks() {
  const t = await api("/tasks");
  $("#task-list").innerHTML = t.local.length ? t.local.map((x) => `
    <div class="row"><div><div>${esc(x.title)}</div><div class="meta">${esc(x.schedule || "no schedule")}${x.next_run_at ? ` · next ${fmtTime(x.next_run_at)}` : ""}</div></div>
    ${me.role === "admin" ? `<button class="ghost small" data-done="${x.id}">Done</button>` : ""}</div>`).join("")
    : `<div class="empty">No tasks yet.</div>`;
  if (me.role === "admin") {
    $("#n8n-list").innerHTML = t.n8n_error ? `<div class="empty">n8n: ${esc(t.n8n_error)}</div>`
      : (t.n8n || []).length ? t.n8n.map((w) => `<div class="row"><div><div>${esc(w.name)}</div>
          <div class="meta">${esc((w.triggers || []).join(" · ") || "no trigger")} · updated ${fmtTime(w.updatedAt)}</div></div><span class="tag ok">active</span></div>`).join("")
      : `<div class="empty">No active n8n workflows${t.n8n ? "" : " (n8n not configured)"}.</div>`;
  }
}

$("#task-list").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-done]");
  if (!b) return;
  await api(`/tasks/${b.dataset.done}`, { method: "DELETE" });
  loadTasks();
});

$("#task-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  await api("/tasks", { method: "POST", body: { title: f.get("title"), schedule: f.get("schedule") || "" } });
  e.target.reset();
  loadTasks();
});

/* ---------------- activity ---------------- */
async function loadActivity() {
  const rows = await api("/activity?limit=150");
  $("#activity-list").innerHTML = rows.length ? rows.map((a) => `
    <div class="e"><span class="ts">${fmtTime(a.ts)}</span><span class="kind ${esc(a.kind)}">${esc(a.kind)}</span>
    <span>${a.user ? `<b>${esc(a.user)}</b> · ` : ""}${esc(a.summary)}</span></div>`).join("")
    : `<div class="empty">No activity yet.</div>`;
}

/* ---------------- health ---------------- */
async function loadHealth(rows) {
  rows = rows || (await api("/health"));
  $("#health-list").innerHTML = rows.length ? rows.map((h) => `
    <div class="card"><div><span class="dot" style="background:var(${h.ok ? "--ok" : "--bad"})"></span><b>${esc(h.service)}</b></div>
    <div class="muted" style="font-size:12px;margin-top:6px">${esc(h.detail)}</div>
    <div class="muted" style="font-size:11px">checked ${fmtTime(h.checked_at)}</div></div>`).join("")
    : `<div class="empty">No checks yet — they run every 5 minutes.</div>`;
}

$("#health-refresh").addEventListener("click", async (e) => {
  e.target.disabled = true;
  try { loadHealth(await api("/health/refresh", { method: "POST" })); } finally { e.target.disabled = false; }
});

/* ---------------- people ---------------- */
async function loadPeople() {
  const [people, attempts] = await Promise.all([api("/people"), api("/access-attempts?limit=80")]);
  $("#people-list").innerHTML = people.map((p) => `
    <div class="row"><div><div>#${p.id} ${esc(p.display_name)} <span class="tag">${esc(p.role)}</span> ${p.active ? "" : '<span class="tag bad">disabled</span>'}</div>
      <div class="meta">${p.identifiers.map((i) => `${esc(i.type)}: ${esc(i.value)}`).join(" · ") || "no identifiers"}</div></div></div>`).join("");
  $("#attempts-list").innerHTML = attempts.length ? attempts.map((a) => `
    <div class="e"><span class="ts">${fmtTime(a.ts)}</span><span class="kind ${a.resolved ? "" : "access"}">${a.resolved ? "allowed" : "blocked"}</span>
    <span>${esc(a.channel)} · ${esc(a.raw_identifier)} ${a.detail ? `· ${esc(a.detail)}` : ""}</span></div>`).join("")
    : `<div class="empty">No attempts recorded.</div>`;
}

boot().catch(showLogin);
