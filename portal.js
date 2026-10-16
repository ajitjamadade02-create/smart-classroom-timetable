/* =========================================================================
   portal.js — student + teacher portals (served at /portal with portal.html).
   READ-ONLY. It never writes the timetable: it only reads the same
   timetable_entry rows the administrator edits, so what you see here is
   always the admin's current version. Every POLL_MS it asks
   /api/portal/notifications for a "version"; when that moves (the admin
   generated / added / removed something) the grid is re-fetched and a toast
   shows what changed. What each role may open is enforced by app.py, not
   only by the buttons below.
   ========================================================================= */
const POLL_MS = 8000;
const state = { user: null, options: null, view: null, id: null, version: null, lastToastId: 0, timer: null };

async function api(path, opts = {}) {
  const res = await fetch(path, { credentials: "same-origin", headers: { "Content-Type": "application/json" }, ...opts });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw { status: res.status, ...data };
  return data;
}
const $ = id => document.getElementById(id);
function esc(s) {
  return (s ?? "").toString().replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function toast(msg) {
  const el = document.createElement("div");
  el.className = "toast";
  el.innerHTML = `<small>Timetable updated</small>${esc(msg)}`;
  $("toastHost").appendChild(el);
  setTimeout(() => el.remove(), 9000);
}

// ================================================================== LOGIN
let loginRole = "student";
function setRole(role) {
  loginRole = role;
  $("roleStudent").classList.toggle("active", role === "student");
  $("roleTeacher").classList.toggle("active", role === "teacher");
  $("authHeading").textContent = role === "student" ? "Student Portal" : "Teacher Portal";
  $("authMsg").innerHTML = "";
}
$("roleStudent").onclick = e => { e.preventDefault(); setRole("student"); };
$("roleTeacher").onclick = e => { e.preventDefault(); setRole("teacher"); };

$("loginForm").addEventListener("submit", async e => {
  e.preventDefault();
  try {
    const r = await api("/api/portal/login", { method: "POST", body: JSON.stringify({
      role: loginRole, login_id: $("li_login_id").value, password: $("li_password").value }) });
    enter(r.user);
  } catch (err) {
    $("authMsg").innerHTML = `<div class="msg msg-error">${esc(err.error || "Login failed")}</div>`;
  }
});
$("logoutBtn").onclick = async e => {
  e.preventDefault();
  await api("/api/portal/logout", { method: "POST" });
  location.reload();
};

// ================================================================== NAV
document.querySelectorAll(".nav-link").forEach(a => a.addEventListener("click", e => {
  e.preventDefault();
  document.querySelectorAll(".section-panel").forEach(p => p.classList.remove("active"));
  $(`section-${a.dataset.section}`).classList.add("active");
  if (a.dataset.section === "rollcall") loadRollcall();
  document.querySelectorAll("#sideNav .nav-link").forEach(x => x.classList.toggle("active", x === a));
}));

// ================================================================== ENTER
async function enter(user) {
  state.user = user;
  $("authScreen").style.display = "none";
  $("appShell").style.display = "flex";
  $("whoName").textContent = user.name;
  $("roleLabel").textContent = user.role === "student" ? "Student portal" : "Teacher portal";
  state.options = await api("/api/portal/options");
  buildTabs();
  await pollNotifications();          // sets the baseline version
  await setView(state.tabs[0].view);
  clearInterval(state.timer);
  state.timer = setInterval(pollNotifications, POLL_MS);
}

function buildTabs() {
  state.tabs = state.user.role === "student"
    ? [{ view: "class", label: "Division timetable" }]
    : [{ view: "faculty", label: "My timetable" }, { view: "class", label: "Class-wise" },
       { view: "classroom", label: "Classroom-wise" }, { view: "lab", label: "Lab-wise" }];
  $("ttTabs").innerHTML = state.tabs.length > 1
    ? state.tabs.map(t => `<button class="btn btn-ghost btn-sm tt-tab" data-view="${t.view}">${t.label}</button>`).join("") : "";
  document.querySelectorAll(".tt-tab").forEach(b => b.onclick = () => setView(b.dataset.view));
}

async function setView(view) {
  state.view = view;
  state.id = null;
  document.querySelectorAll(".tt-tab").forEach(b => b.classList.toggle("active", b.dataset.view === view));
  const o = state.options;
  let list = [];
  if (view === "class") list = o.divisions.map(d => ({ id: d.id, label: `${d.name} (${d.year_label})` }));
  if (view === "classroom") list = (o.classrooms || []).filter(c => c.room_type === "classroom").map(c => ({ id: c.id, label: `${c.room_no} · cap ${c.capacity}` }));
  if (view === "lab") list = (o.classrooms || []).filter(c => c.room_type === "lab").map(c => ({ id: c.id, label: `${c.room_no} · cap ${c.capacity}` }));
  const picker = $("ttPicker");
  picker.style.display = view === "faculty" ? "none" : "block";
  picker.innerHTML = list.map(x => `<option value="${x.id}">${esc(x.label)}</option>`).join("");
  if (view === "class" && state.user.division_id && list.some(x => x.id === state.user.division_id)) picker.value = state.user.division_id;
  state.id = view === "faculty" ? null : (picker.value || null);
  picker.onchange = () => { state.id = picker.value; renderTimetable(); };
  if (view !== "faculty" && !list.length) {
    $("ttGridWrap").innerHTML = `<p class="hint">Nothing to show here yet — the administrator hasn't added any.</p>`;
    $("ttTitle").textContent = "Timetable";
    return;
  }
  await renderTimetable();
}

// ================================================================== GRID
async function renderTimetable() {
  const { view, id } = state;
  const pill = { class: "division", classroom: "classroom", lab: "lab", faculty: "my timetable" }[view];
  $("ttPill").textContent = pill;
  $("ttTitle").textContent = view === "faculty" ? state.user.name : ($("ttPicker").selectedOptions[0]?.textContent || "Timetable");
  $("ttSubtitle").textContent = view === "faculty" ? "Only you can see this one." : "";
  try {
    const data = await api(`/api/portal/timetable?view=${view}&id=${id || ""}`);
    $("ttGridWrap").innerHTML = data.periods.length
      ? buildGridHTML(data.periods, data.working_days, data.entries)
      : `<p class="hint">The administrator hasn't set up the timetable yet.</p>`;
  } catch (err) {
    if (err.status === 401) return location.reload();
    $("ttGridWrap").innerHTML = `<div class="msg msg-error">${esc(err.error || "Could not load the timetable.")}</div>`;
  }
}

function entryCardHTML(e) {
  const fac = (e.faculty_names || []).join(" & ");
  return `<div class="entry-card entry-${esc(e.entry_type)}">
    <b>${esc(e.room_no || "Room")}</b>
    <div class="entry-subject"><strong>${esc(e.subject_code)}</strong> ${esc(e.subject_name || "")}</div>
    <div class="meta">${esc(e.division_name || "")}${e.batch_name ? " · " + esc(e.batch_name) : ""}<br>${esc(fac)}</div>
  </div>`;
}
const hhmm = m => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
function buildGridHTML(periods, workingDays, entries) {
  const head = `<tr><th class="corner"></th>${workingDays.map(d => `<th>${d}</th>`).join("")}</tr>`;
  const rows = periods.map(p => {
    const cells = workingDays.map(d => {
      if (p.is_break) return `<td class="cell-break"><div class="cell-label">${esc(p.label)}</div></td>`;
      const items = (entries[`${d}|${p.index}`] || []).slice().sort((a, b) =>
        (a.room_no || "").localeCompare(b.room_no || "", undefined, { numeric: true, sensitivity: "base" }));
      return `<td><div class="cell-slot">${items.length ? items.map(entryCardHTML).join("") : `<div class="empty-slot">—</div>`}</div></td>`;
    }).join("");
    return `<tr><td class="time-col">${hhmm(p.start)}–${hhmm(p.end)}${p.is_break ? `<br><span style="text-transform:uppercase;">${esc(p.label)}</span>` : ""}</td>${cells}</tr>`;
  }).join("");
  return `<div class="tt-wrap"><table class="tt-grid">${head}${rows}</table></div>`;
}

// ================================================================== ROLL CALL (view / download only)
async function loadRollcall() {
  const picker = $("rcPicker"), body = $("rcBody");
  const teacher = state.user.role === "teacher";
  if (teacher && !picker.options.length) {
    picker.innerHTML = state.options.divisions.map(d => `<option value="${d.id}">${esc(d.name)} (${esc(d.year_label)})</option>`).join("");
    picker.onchange = loadRollcall;
  }
  picker.style.display = teacher ? "block" : "none";
  if (teacher && !picker.options.length) { body.innerHTML = `<p class="hint">No divisions yet.</p>`; return; }
  const q = teacher ? `?division_id=${picker.value}` : "";
  try {
    const r = await api(`/api/portal/rollcall${q}`);
    $("rcTitle").textContent = `Roll call — ${r.division_name}`;
    if (!r.available) { body.innerHTML = `<p class="hint">The administrator hasn't uploaded a roll call for this class yet.</p>`; return; }
    const sep = q ? "&" : "?";
    const when = r.updated_at ? new Date(r.updated_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "";
    body.innerHTML = `<div style="display:flex; gap:14px; align-items:center; flex-wrap:wrap;">
      <div style="flex:1; min-width:200px;"><b>${esc(r.file_name)}</b><div class="hint">Updated ${esc(when)}</div></div>
      ${r.can_view ? `<a class="btn btn-ghost" target="_blank" rel="noopener" href="/api/portal/rollcall/file${q}${sep}inline=1">👁 View</a>` : ""}
      <a class="btn btn-primary" href="/api/portal/rollcall/file${q}">⬇ Download</a></div>
      ${r.can_view ? "" : `<p class="hint" style="margin-top:10px;">This file type can't be previewed in the browser — download it to open.</p>`}`;
  } catch (err) {
    if (err.status === 401) return location.reload();
    body.innerHTML = `<div class="msg msg-error">${esc(err.error || "Could not load the roll call.")}</div>`;
  }
}

// ================================================================== PDF DOWNLOAD
// Same PDF layout and heading/signature wording the administrator uses (saved template).
$("downloadPdfBtn").onclick = async () => {
  const btn = $("downloadPdfBtn");
  const { view, id } = state;
  btn.disabled = true;
  try {
    const res = await fetch(`/api/portal/timetable/download.pdf?view=${view}&id=${id || ""}`, { credentials: "same-origin" });
    if (!res.ok) { const e = await res.json().catch(() => ({})); throw { status: res.status, ...e }; }
    const cd = res.headers.get("Content-Disposition") || "";
    const name = (cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i) || [])[1] || "timetable.pdf";
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url; a.download = decodeURIComponent(name); document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  } catch (err) {
    if (err.status === 401) return location.reload();
    $("flashArea").innerHTML = `<div class="msg msg-error">${esc(err.error || "Could not download the PDF.")}</div>`;
    setTimeout(() => { $("flashArea").innerHTML = ""; }, 5000);
  } finally { btn.disabled = false; }
};

// ================================================================== LIVE UPDATES
function renderNotifs(data) {
  const badge = $("notifBadge");
  badge.textContent = data.unread_count;
  badge.classList.toggle("on", data.unread_count > 0);
  $("notifList").innerHTML = data.items.length
    ? data.items.map(i => `<div class="notif-item ${i.unread ? "unread" : ""}"><div style="flex:1;">${esc(i.message)}</div>
        <div class="when">${esc(new Date(i.created_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }))}</div></div>`).join("")
    : `<p class="hint" style="padding:18px;">No updates yet.</p>`;
}

async function pollNotifications() {
  let data;
  try { data = await api("/api/portal/notifications"); }
  catch (e) { if (e.status === 401) location.reload(); return; }
  const moved = state.version !== null && data.version !== state.version;
  state.version = data.version;
  renderNotifs(data);
  if (!moved) return;
  await renderTimetable();                       // admin changed something -> re-read the grid
  const newest = data.items.find(i => i.unread && i.id > state.lastToastId);
  if (newest) { toast(newest.message); state.lastToastId = newest.id; }
}
document.addEventListener("visibilitychange", () => { if (!document.hidden && state.user) pollNotifications(); });

$("markReadBtn").onclick = async () => {
  await api("/api/portal/notifications/read", { method: "POST" });
  pollNotifications();
};

// ================================================================== BOOT (restore an existing session)
(async () => {
  try { const { user } = await api("/api/portal/me"); enter(user); } catch (_) { /* show login */ }
})();