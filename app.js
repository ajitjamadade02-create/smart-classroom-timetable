/* =========================================================================
   app.js — Smart Classroom & Timetable Scheduler, frontend logic.
   Every function here calls an endpoint defined in app.py and writes into
   an element id/class defined in index.html. See the header comment in
   app.py for the full file-to-file map.
   ========================================================================= */

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
let YEAR_LABELS = [];        // filled from /api/years on first load
let LOOKUPS = null;          // cached {years,divisions,batches,faculty,classrooms}

// ---------------------------------------------------------------- fetch helper
async function api(path, opts = {}) {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: opts.body instanceof FormData ? {} : { "Content-Type": "application/json" },
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw { status: res.status, ...data };
  return data;
}

function flash(msg, ok = true) {
  const area = document.getElementById("flashArea");
  area.innerHTML = `<div class="msg ${ok ? "msg-ok" : "msg-error"}">${msg}</div>`;
  setTimeout(() => { area.innerHTML = ""; }, 4000);
}

function esc(s) {
  return (s ?? "").toString().replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ================================================================== AUTH
document.getElementById("tabLogin").onclick = (e) => { e.preventDefault(); switchAuthTab("login"); };
document.getElementById("tabRegister").onclick = (e) => { e.preventDefault(); switchAuthTab("register"); };

function switchAuthTab(which) {
  document.getElementById("tabLogin").classList.toggle("active", which === "login");
  document.getElementById("tabRegister").classList.toggle("active", which === "register");
  document.getElementById("loginForm").style.display = which === "login" ? "block" : "none";
  document.getElementById("registerForm").style.display = which === "register" ? "block" : "none";
  document.getElementById("authHeading").textContent = which === "login" ? "Administrator Login" : "Register Administrator";
  document.getElementById("authMsg").innerHTML = "";
}

document.getElementById("loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const data = await api("/api/login", {
      method: "POST",
      body: JSON.stringify({
        login_id: document.getElementById("li_login_id").value,
        password: document.getElementById("li_password").value,
      }),
    });
    enterApp(data.admin);
  } catch (err) {
    document.getElementById("authMsg").innerHTML = `<div class="msg msg-error">${esc(err.error || "Login failed")}</div>`;
  }
});

document.getElementById("registerForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const data = await api("/api/register", {
      method: "POST",
      body: JSON.stringify({
        login_id: document.getElementById("rg_login_id").value,
        name: document.getElementById("rg_name").value,
        password: document.getElementById("rg_password").value,
        confirm: document.getElementById("rg_confirm").value,
      }),
    });
    enterApp(data.admin);
  } catch (err) {
    document.getElementById("authMsg").innerHTML = `<div class="msg msg-error">${esc(err.error || "Registration failed")}</div>`;
  }
});

document.getElementById("logoutBtn").onclick = async (e) => {
  e.preventDefault();
  await api("/api/logout", { method: "POST" });
  location.reload();
};

function enterApp(admin) {
  document.getElementById("authScreen").style.display = "none";
  document.getElementById("appShell").style.display = "flex";
  document.getElementById("whoName").textContent = admin.name;
  showSection("dashboard");
}

// ================================================================== NAV
document.querySelectorAll(".nav-link").forEach(link => {
  link.addEventListener("click", (e) => {
    e.preventDefault();
    showSection(link.dataset.section);
  });
});
document.querySelectorAll(".jump-tt").forEach(link => {
  link.addEventListener("click", (e) => {
    e.preventDefault();
    showSection("timetable");
    setTTView(link.dataset.view);
  });
});

const SECTION_LOADERS = {
  dashboard: loadDashboard,
  hours: loadHours,
  breaks: loadBreaks,
  years: loadYears,
  divisions: loadDivisions,
  faculty: loadFaculty,
  classrooms: loadClassrooms,
  subjects: loadSubjects,
  timetable: () => loadTimetable(),
  portal: loadPortalAccounts,
};

function showSection(name) {
  document.querySelectorAll(".section-panel").forEach(p => p.classList.remove("active"));
  document.getElementById(`section-${name}`).classList.add("active");
  document.querySelectorAll("#sideNav .nav-link").forEach(a => a.classList.toggle("active", a.dataset.section === name));
  const loader = SECTION_LOADERS[name];
  if (loader) loader();
}

// ================================================================== LOOKUPS (shared dropdown data)
async function refreshLookups() {
  LOOKUPS = await api("/api/lookups");
  return LOOKUPS;
}

// ================================================================== DASHBOARD
async function loadDashboard() {
  const [{ institute, breaks }, { years }, { divisions }, { faculty }, { classrooms }, { subjects }] = await Promise.all([
    api("/api/institute"), api("/api/years"), api("/api/divisions"), api("/api/faculty"), api("/api/classrooms"), api("/api/subjects"),
  ]);
  document.getElementById("dashInstName").textContent = institute.name;
  document.getElementById("dashInstHours").textContent = `${institute.day_start} – ${institute.day_end} · ${(institute.working_days || []).length} working days/week`;
  const rows = [
    ["03", "Years & semesters configured", years.length, "years"],
    ["04", "Divisions", divisions.length, "divisions"],
    ["05", "Faculty", faculty.length, "faculty"],
    ["06", "Classrooms / Labs", classrooms.length, "classrooms"],
    ["07", "Subjects", subjects.length, "subjects"],
    ["02", "Breaks configured", breaks.length, "breaks"],
  ];
  document.getElementById("dashCounts").innerHTML = rows.map(([step, label, count, sec]) => `
    <tr><td class="mono">${step}</td><td>${label}</td><td>${count}</td>
    <td><a href="#" class="btn btn-ghost btn-sm nav-link" data-section="${sec}">Open</a></td></tr>
  `).join("");
  document.querySelectorAll("#dashCounts .nav-link").forEach(a => a.addEventListener("click", (e) => { e.preventDefault(); showSection(a.dataset.section); }));
}

// ================================================================== HOURS
function renderDayCheckboxes(containerId, name, checkedList) {
  renderCheckboxGroup(containerId, name, WEEKDAYS.map(d => ({ value: d, label: d })), checkedList);
}

// Generic checkbox-group renderer used by hours (working days/holidays) and
// by the subject form's fixed-day / combine-division / combine-batch pickers
// — replaces earlier <select multiple> versions of these fields, since
// checkboxes are far more discoverable than requiring Ctrl/Cmd-click.
function renderCheckboxGroup(containerId, name, options, checkedValues = []) {
  const box = document.getElementById(containerId);
  const checkedStrs = checkedValues.map(String);
  box.innerHTML = options.map(o => `
    <label style="font-weight:400; display:flex; align-items:center; gap:6px;">
      <input type="checkbox" name="${name}" value="${o.value}" style="width:auto;" ${checkedStrs.includes(String(o.value)) ? "checked" : ""}> ${esc(o.label)}
    </label>`).join("") || `<span class="hint">Nothing to pick yet.</span>`;
}

function checkedValuesIn(containerId) {
  return [...document.querySelectorAll(`#${containerId} input:checked`)].map(i => i.value);
}

async function loadHours() {
  const { institute } = await api("/api/institute");
  document.getElementById("h_name").value = institute.name || "";
  document.getElementById("h_start").value = institute.day_start;
  document.getElementById("h_end").value = institute.day_end;
  document.getElementById("h_period").value = institute.period_minutes;
  renderDayCheckboxes("workingDaysBox", "wd", institute.working_days || []);
  renderDayCheckboxes("holidaysBox", "hd", institute.holidays || []);
}

document.getElementById("hoursForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const working_days = [...document.querySelectorAll('#workingDaysBox input:checked')].map(i => i.value);
  const holidays = [...document.querySelectorAll('#holidaysBox input:checked')].map(i => i.value);
  await api("/api/institute", {
    method: "POST",
    body: JSON.stringify({
      name: document.getElementById("h_name").value,
      day_start: document.getElementById("h_start").value,
      day_end: document.getElementById("h_end").value,
      period_minutes: document.getElementById("h_period").value,
      working_days, holidays,
    }),
  });
  flash("Institute hours saved.");
  showSection("breaks");
});

// ================================================================== BREAKS
async function loadBreaks() {
  const { breaks } = await api("/api/institute");
  document.getElementById("breaksList").innerHTML = breaks.map(b => `
    <tr><td>${esc(b.label)}</td><td><span class="tag tag-${b.kind === "lunch" ? "lecture" : "tutorial"}">${b.kind}</span></td>
    <td class="mono">${b.start_time} – ${b.end_time}</td>
    <td><button class="btn btn-danger btn-sm del-break" data-id="${b.id}">Remove</button></td></tr>
  `).join("") || `<tr><td colspan="4" class="hint">No breaks configured yet.</td></tr>`;
  document.querySelectorAll(".del-break").forEach(btn => btn.addEventListener("click", async () => {
    await api(`/api/breaks/${btn.dataset.id}`, { method: "DELETE" });
    loadBreaks();
  }));
}

document.getElementById("breakForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  await api("/api/breaks", {
    method: "POST",
    body: JSON.stringify({
      label: document.getElementById("b_label").value,
      kind: document.getElementById("b_kind").value,
      start_time: document.getElementById("b_start").value,
      end_time: document.getElementById("b_end").value,
    }),
  });
  e.target.reset();
  flash("Break added.");
  loadBreaks();
});

// ================================================================== YEARS
function syncSem() {
  const labelSel = document.getElementById("y_label");
  const opt = labelSel.options[labelSel.selectedIndex];
  if (!opt) return;
  const parity = document.getElementById("y_parity").value;
  document.getElementById("y_sem").value = parity === "odd" ? opt.dataset.odd : opt.dataset.even;
}

async function loadYears() {
  const { years, year_labels } = await api("/api/years");
  YEAR_LABELS = year_labels;
  document.getElementById("y_label").innerHTML = YEAR_LABELS.map(([code, full, sems]) =>
    `<option value="${code}" data-odd="${sems[0]}" data-even="${sems[1]}">${code} — ${full}</option>`).join("");
  syncSem();
  document.getElementById("yearsList").innerHTML = years.map(y => `
    <tr><td><b>${esc(y.label)}</b></td><td style="text-transform:capitalize;">${y.parity}</td><td class="mono">${y.sem_no}</td>
    <td><button class="btn btn-danger btn-sm del-year" data-id="${y.id}">Remove</button></td></tr>
  `).join("") || `<tr><td colspan="4" class="hint">No years added yet.</td></tr>`;
  document.querySelectorAll(".del-year").forEach(btn => btn.addEventListener("click", async () => {
    await api(`/api/years/${btn.dataset.id}`, { method: "DELETE" });
    loadYears();
  }));
}
document.getElementById("y_parity").addEventListener("change", syncSem);
document.getElementById("y_label").addEventListener("change", syncSem);

document.getElementById("yearForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/api/years", {
      method: "POST",
      body: JSON.stringify({
        label: document.getElementById("y_label").value,
        parity: document.getElementById("y_parity").value,
        sem_no: document.getElementById("y_sem").value,
      }),
    });
    flash("Year/semester added.");
    loadYears();
  } catch (err) { flash(err.error || "Could not add year.", false); }
});

// ================================================================== DIVISIONS
async function loadDivisions() {
  await refreshLookups();
  document.getElementById("d_year").innerHTML = LOOKUPS.years.map(y => `<option value="${y.id}">${y.label} — Sem ${y.sem_no}</option>`).join("");
  const { divisions } = await api("/api/divisions");
  const classrooms = LOOKUPS.classrooms.filter(r => r.room_type === "classroom");
  document.getElementById("roomOptions").innerHTML = classrooms.map(r => `<option value="${esc(r.room_no)}">cap ${r.capacity}</option>`).join("");
  const roomNote = (d) => {
    if (!d.home_room) return `<span class="hint">Any classroom — back-to-back periods stay in one room; after a break or lab the room may change.</span>`;
    const r = classrooms.find(x => x.room_no.toLowerCase() === d.home_room.toLowerCase());
    if (!r) return `<span class="hint" style="color:var(--brick);">⚠ "${esc(d.home_room)}" is not in your classrooms yet (step 06).</span>`;
    const shared = divisions.filter(o => o.id !== d.id && (o.home_room || "").toLowerCase() === d.home_room.toLowerCase()).map(o => o.name);
    const notes = [];
    if (r.capacity < d.strength) notes.push(`seats ${r.capacity} but the division has ${d.strength}`);
    if (shared.length) notes.push(`also assigned to ${shared.join(", ")} — their lectures can't overlap`);
    return notes.length ? `<span class="hint" style="color:var(--brick);">⚠ ${esc(notes.join("; "))}</span>` : "";
  };
  document.getElementById("divisionsList").innerHTML = divisions.map(d => `
    <div class="repeat-row">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <div><b>${esc(d.name)}</b> <span class="hint">(${d.year_label} Sem ${d.year_sem}) · Class teacher: ${esc(d.class_teacher) || "—"} · ${d.strength} students</span></div>
        <div style="display:flex; gap:8px;">
          <a href="/api/divisions/${d.id}/rollcall.pdf" target="_blank" class="btn btn-ghost btn-sm">⬇ Roll Call PDF</a>
          <button type="button" class="btn btn-ghost btn-sm upload-rollcall" data-id="${d.id}">⬆ ${d.rollcall_name ? "Replace" : "Upload"} Roll Call</button>
          <input type="file" class="rollcall-file" data-id="${d.id}" style="display:none;">
          <button class="btn btn-danger btn-sm del-division" data-id="${d.id}">Remove division</button>
        </div>
      </div>
      ${d.rollcall_name ? `<div class="hint" style="margin-top:8px;">Uploaded roll call (visible in student &amp; teacher portals):
        <a href="/api/divisions/${d.id}/rollcall/file" target="_blank">${esc(d.rollcall_name)}</a>
        · <a href="#" class="del-rollcall" data-id="${d.id}" style="color:var(--brick);">remove</a></div>` : ""}
      <form class="set-room-form" data-div="${d.id}" style="display:flex; gap:8px; align-items:center; margin-top:10px; flex-wrap:wrap;">
        <label style="margin:0;">Classroom</label>
        <select name="room_mode" class="room-mode" style="width:200px;">
          <option value="any" ${d.home_room ? "" : "selected"}>Any classroom</option>
          <option value="fixed" ${d.home_room ? "selected" : ""}>Specific classroom…</option>
        </select>
        <input type="text" name="home_room" list="roomOptions" value="${esc(d.home_room || "")}" placeholder="e.g. CSAMCR-06" style="width:200px; ${d.home_room ? "" : "display:none;"}" autocomplete="off">
        <button class="btn btn-ghost btn-sm">Save</button>
        ${roomNote(d)}
      </form>
      <table style="margin-top:10px;"><tr><th>Batch</th><th>Students</th><th></th></tr>
        ${d.batches.map(b => `<tr><td>${esc(b.name)}</td><td>${b.num_students}</td>
          <td><button class="btn btn-danger btn-sm del-batch" data-id="${b.id}">Remove</button></td></tr>`).join("") ||
          `<tr><td colspan="3" class="hint">No batches yet.</td></tr>`}
      </table>
      <form class="add-batch-form" data-div="${d.id}" style="display:flex; gap:8px; margin-top:8px;">
        <input type="text" placeholder="B1" required style="width:100px;" name="name">
        <input type="number" placeholder="Students" min="0" style="width:120px;" name="num_students">
        <button class="btn btn-ghost btn-sm">+ Add batch</button>
      </form>
    </div>
  `).join("") || `<p class="hint">No divisions yet.</p>`;

  document.querySelectorAll(".set-room-form").forEach(form => {
    form.room_mode.addEventListener("change", () => {
      const fixed = form.room_mode.value === "fixed";
      form.home_room.style.display = fixed ? "" : "none";
      if (fixed) form.home_room.focus();
    });
  });
  document.querySelectorAll(".set-room-form").forEach(form => form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fixed = form.room_mode.value === "fixed";
    if (fixed && !form.home_room.value.trim()) { flash("Type or pick a classroom, or choose \"Any classroom\".", false); return; }
    try {
      await api(`/api/divisions/${form.dataset.div}/room`, { method: "POST", body: JSON.stringify({ home_room: fixed ? form.home_room.value : "" }) });
      flash(fixed ? "Classroom saved. Click Generate again to apply it." : "Set to any classroom. Click Generate again to apply it.");
      loadDivisions();
    } catch (err) { flash(err.error || "Could not save the classroom.", false); }
  }));
  document.querySelectorAll(".upload-rollcall").forEach(btn => btn.addEventListener("click", () =>
    btn.parentElement.querySelector(".rollcall-file").click()));
  document.querySelectorAll(".rollcall-file").forEach(inp => inp.addEventListener("change", async () => {
    const file = inp.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    try {
      await api(`/api/divisions/${inp.dataset.id}/rollcall/upload`, { method: "POST", body: fd });
      flash("Roll call uploaded. Students and teachers can now see it.");
      loadDivisions();
    } catch (err) { flash(err.error || "Could not upload the roll call.", false); inp.value = ""; }
  }));
  document.querySelectorAll(".del-rollcall").forEach(a => a.addEventListener("click", async (e) => {
    e.preventDefault();
    if (!confirm("Remove the uploaded roll call for this division?")) return;
    await api(`/api/divisions/${a.dataset.id}/rollcall/file`, { method: "DELETE" });
    loadDivisions();
  }));
  document.querySelectorAll(".del-division").forEach(btn => btn.addEventListener("click", async () => {
    await api(`/api/divisions/${btn.dataset.id}`, { method: "DELETE" });
    loadDivisions();
  }));
  document.querySelectorAll(".del-batch").forEach(btn => btn.addEventListener("click", async () => {
    await api(`/api/batches/${btn.dataset.id}`, { method: "DELETE" });
    loadDivisions();
  }));
  document.querySelectorAll(".add-batch-form").forEach(form => form.addEventListener("submit", async (e) => {
    e.preventDefault();
    await api(`/api/divisions/${form.dataset.div}/batches`, {
      method: "POST",
      body: JSON.stringify({ name: form.name.value, num_students: form.num_students.value }),
    });
    loadDivisions();
  }));
}

document.getElementById("d_room_mode").addEventListener("change", () => {
  const fixed = document.getElementById("d_room_mode").value === "fixed";
  document.getElementById("d_room").style.display = fixed ? "" : "none";
  document.getElementById("d_room_hint").textContent = fixed
    ? "All this division's lectures are held in this room. Labs go to any lab."
    : "Lectures go to any free classroom, but back-to-back periods always stay in the same room. After a break or a lab the room may change.";
});

document.getElementById("divisionForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (document.getElementById("d_room_mode").value === "fixed" && !document.getElementById("d_room").value.trim()) {
    flash("Type or pick a classroom, or choose \"Any classroom\".", false); return;
  }
  await api("/api/divisions", {
    method: "POST",
    body: JSON.stringify({
      year_id: document.getElementById("d_year").value,
      name: document.getElementById("d_name").value,
      class_teacher: document.getElementById("d_teacher").value,
      strength: document.getElementById("d_strength").value,
      home_room: document.getElementById("d_room_mode").value === "fixed" ? document.getElementById("d_room").value : "",
    }),
  }).catch(err => { flash(err.error || "Could not add the division.", false); throw err; });
  document.getElementById("d_name").value = "";
  document.getElementById("d_room").value = "";
  document.getElementById("d_room_mode").value = "any";
  document.getElementById("d_room").style.display = "none";
  flash("Division added.");
  loadDivisions();
});

// ================================================================== FACULTY
async function loadWorkload() {
  const { faculty } = await api("/api/faculty-workload");
  const body = document.getElementById("workloadBody");
  const cell = v => v ? esc(v) : "";
  body.innerHTML = faculty.length ? faculty.map((f, i) => {
    const rows = f.rows.length ? f.rows : [null];
    return rows.map((r, k) => `<tr>
      ${k === 0 ? `<td rowspan="${rows.length}" style="text-align:center;">${i + 1}</td>
        <td rowspan="${rows.length}"><b>${esc(f.name)}</b></td>
        <td rowspan="${rows.length}">${esc(f.designation || "")}</td>` : ""}
      ${r ? `<td>${esc(r.class)}</td><td>${esc(r.course)}</td>
        <td class="mono" style="text-align:center;">${cell(r.theory)}</td><td class="mono" style="text-align:center;">${cell(r.tutorial)}</td>
        <td class="mono" style="text-align:center;">${cell(r.practical)}</td><td class="mono" style="text-align:center;">${r.n_divisions}</td>`
        : `<td colspan="6" class="hint">No subjects allocated yet.</td>`}
      ${k === 0 ? `<td rowspan="${rows.length}" class="mono" style="text-align:center;"><b>${f.total || ""}</b></td>` : ""}
    </tr>`).join("");
  }).join("") : `<tr><td colspan="10" class="hint">No faculty yet.</td></tr>`;
}

async function loadFaculty() {
  loadWorkload();
  refreshDemoStatus();
  const { faculty } = await api("/api/faculty");
  document.getElementById("facultyList").innerHTML = faculty.map(f => `
    <tr><td>${esc(f.name)}</td><td>${esc(f.designation || "—")}</td><td>${(f.qualifications || []).map(q => `<span class="tag tag-lecture">${esc(q)}</span>`).join(" ")}</td>
    <td class="mono">${f.max_periods_per_day}</td><td><button class="btn btn-danger btn-sm del-faculty" data-id="${f.id}">Remove</button></td></tr>
  `).join("") || `<tr><td colspan="5" class="hint">No faculty added yet.</td></tr>`;
  document.querySelectorAll(".del-faculty").forEach(btn => btn.addEventListener("click", async () => {
    await api(`/api/faculty/${btn.dataset.id}`, { method: "DELETE" });
    loadFaculty();
  }));
}

document.getElementById("facultyForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  await api("/api/faculty", {
    method: "POST",
    body: JSON.stringify({
      name: document.getElementById("f_name").value,
      qualifications: document.getElementById("f_quals").value,
      max_periods_per_day: document.getElementById("f_max").value,
      designation: document.getElementById("f_desig").value,
    }),
  });
  e.target.reset();
  flash("Faculty added.");
  loadFaculty();
});

// ================================================================== CLASSROOMS
async function loadClassrooms() {
  const { classrooms } = await api("/api/classrooms");
  document.getElementById("classroomsList").innerHTML = classrooms.map(c => `
    <tr><td>${esc(c.room_no)}</td><td><span class="tag ${c.room_type === "lab" ? "tag-lab" : "tag-lecture"}">${c.room_type}</span></td>
    <td class="mono">${c.capacity}</td><td><button class="btn btn-danger btn-sm del-room" data-id="${c.id}">Remove</button></td></tr>
  `).join("") || `<tr><td colspan="4" class="hint">No rooms added yet.</td></tr>`;
  document.querySelectorAll(".del-room").forEach(btn => btn.addEventListener("click", async () => {
    await api(`/api/classrooms/${btn.dataset.id}`, { method: "DELETE" });
    loadClassrooms();
  }));
}

document.getElementById("classroomForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  await api("/api/classrooms", {
    method: "POST",
    body: JSON.stringify({
      room_no: document.getElementById("c_room").value,
      room_type: document.getElementById("c_type").value,
      capacity: document.getElementById("c_cap").value,
    }),
  });
  e.target.reset();
  flash("Room added.");
  loadClassrooms();
});

// ================================================================== SUBJECTS
function refreshCombineOptions() {
  // Combine-divisions must stay scoped to the year currently selected in
  // the subject form — combining SY A with a TY division makes no sense,
  // and showing everyone's divisions was the source of the "can't choose
  // divisions to combine" confusion.
  const yearId = document.getElementById("s_year").value;
  const divsInYear = LOOKUPS.divisions.filter(d => String(d.year_id) === String(yearId));
  renderCheckboxGroup("s_combdiv_box", "combine_divisions", divsInYear.map(d => ({ value: d.id, label: d.name })));
}

async function loadSubjects() {
  await refreshLookups();
  document.getElementById("s_year").innerHTML = LOOKUPS.years.map(y => `<option value="${y.id}">${y.label} — Sem ${y.sem_no}</option>`).join("");
  renderCheckboxGroup("s_fixedday_box", "external_fixed_days", WEEKDAYS.map(d => ({ value: d, label: d })));
  document.getElementById("s_year").onchange = refreshCombineOptions;
  refreshCombineOptions();

  const { subjects } = await api("/api/subjects");
  const divOptions = LOOKUPS.divisions.map(d => `<option value="${d.id}">${esc(d.name)}</option>`).join("");

  document.getElementById("subjectsList").innerHTML = subjects.map(s => `
    <div class="repeat-row">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <div>
          <b>${esc(s.code)} — ${esc(s.name)}</b> <span class="tag tag-${s.type}">${s.type}</span>
          <span class="hint">${s.year_label} Sem ${s.year_sem} · ${s.lectures_per_week}/week
            ${({ first: " · 1st period", last: " · last period", before_lunch: " · before lunch", after_lunch: " · after lunch" })[s.priority_slot] || ""}${s.type === "lecture" || s.type === "tutorial" ? ` · max ${s.max_per_day || 2}/day` : ""}${(s.external_fixed_days && s.external_fixed_days.length) ? ` · fixed on ${s.external_fixed_days.join(", ")}` : ""}
            ${s.multi_faculty ? " · multi-faculty" : ""}${s.syllabus_name ? ` · <a href="/api/subjects/${s.id}/syllabus" target="_blank">syllabus</a>` : ""}</span>
        </div>
        <button class="btn btn-danger btn-sm del-subject" data-id="${s.id}">Remove</button>
      </div>
      <table style="margin-top:8px;"><tr><th>Faculty</th><th>Division</th><th>Batch(es)</th><th></th></tr>
        ${s.allocations.map(a => `<tr><td>${esc(a.faculty_name)}</td><td>${esc(a.division_name)}</td><td>${(a.batch_names && a.batch_names.length) ? esc(a.batch_names.join(", ")) : "— whole division —"}</td>
          <td><button class="btn btn-danger btn-sm del-alloc" data-id="${a.id}">Remove</button></td></tr>`).join("") ||
          `<tr><td colspan="4" class="hint">No faculty allocated yet.</td></tr>`}
      </table>
      <form class="alloc-form" data-subj="${s.id}" style="display:flex; gap:16px; margin-top:8px; flex-wrap:wrap; align-items:flex-start;">
        <div>
          <div class="hint" style="margin-bottom:4px;">Faculty (check 2+ to co-teach)</div>
          <div style="max-height:110px; overflow-y:auto; border:1px solid var(--line); border-radius:6px; padding:8px; min-width:180px;">
            ${LOOKUPS.faculty.map(f => `
              <label style="font-weight:400; display:flex; align-items:center; gap:6px; margin-bottom:4px;">
                <input type="checkbox" name="faculty_id" value="${f.id}" style="width:auto;"> ${esc(f.name)}
              </label>`).join("") || `<span class="hint">Add faculty first (step 05).</span>`}
          </div>
        </div>
        <div>
          <div class="hint" style="margin-bottom:4px;">Division</div>
          <select name="division_id" required><option value="">Division…</option>${divOptions}</select>
        </div>
        <div>
          <div class="hint" style="margin-bottom:4px;">Batches (leave unchecked for whole division; check 2+ from the same division to merge them into one lab with a single teacher)</div>
          <div class="batch-box" style="max-height:110px; overflow-y:auto; border:1px solid var(--line); border-radius:6px; padding:8px; min-width:200px;">
            ${LOOKUPS.batches.map(b => `
              <label data-div="${b.division_id}" style="font-weight:400; display:flex; align-items:center; gap:6px; margin-bottom:4px;">
                <input type="checkbox" name="batch_id" value="${b.id}" style="width:auto;"> ${esc(b.division_name)} – ${esc(b.name)}
              </label>`).join("") || `<span class="hint">No batches yet.</span>`}
          </div>
        </div>
        <button class="btn btn-ghost btn-sm" style="align-self:flex-end;">+ Allocate</button>
      </form>
    </div>
  `).join("") || `<p class="hint">No subjects yet.</p>`;

  // Once a division is picked, only show that division's batches — and
  // uncheck any that fall outside it, so a submit can't mix batches from
  // two different divisions into one nonsensical "merge".
  document.querySelectorAll(".alloc-form").forEach(form => {
    const divSel = form.querySelector('select[name="division_id"]');
    const syncBatchVisibility = () => {
      form.querySelectorAll(".batch-box label").forEach(lbl => {
        const matches = !divSel.value || String(lbl.dataset.div) === String(divSel.value);
        lbl.style.display = matches ? "flex" : "none";
        if (!matches) lbl.querySelector("input").checked = false;
      });
    };
    divSel.addEventListener("change", syncBatchVisibility);
    syncBatchVisibility();
  });

  document.querySelectorAll(".del-subject").forEach(btn => btn.addEventListener("click", async () => {
    await api(`/api/subjects/${btn.dataset.id}`, { method: "DELETE" });
    loadSubjects();
  }));
  document.querySelectorAll(".del-alloc").forEach(btn => btn.addEventListener("click", async () => {
    await api(`/api/allocations/${btn.dataset.id}`, { method: "DELETE" });
    loadSubjects();
  }));
  document.querySelectorAll(".alloc-form").forEach(form => form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const facultyIds = [...form.querySelectorAll('input[name="faculty_id"]:checked')].map(i => i.value);
    const batchIds = [...form.querySelectorAll('input[name="batch_id"]:checked')].map(i => i.value);
    if (!facultyIds.length) { flash("Select at least one faculty.", false); return; }
    try {
      const result = await api(`/api/subjects/${form.dataset.subj}/allocate`, {
        method: "POST",
        body: JSON.stringify({
          faculty_ids: facultyIds, division_id: form.division_id.value, batch_ids: batchIds,
        }),
      });
      flash(result.count > 1 ? `${result.count} faculty allocated (co-teaching).` : "Faculty allocated.");
      loadSubjects();
    } catch (err) { flash(err.error || "Could not allocate.", false); }
  }));
}

// Labs may never sit in the first two periods, so "First period" is not offered for them.
function syncPriorityOptions() {
  const isLab = document.getElementById("s_type").value === "lab";
  const sel = document.getElementById("s_priority");
  const first = sel.querySelector('option[value="first"]');
  first.hidden = isLab; first.disabled = isLab;
  if (isLab && sel.value === "first") sel.value = "any";
  const md = document.getElementById("s_maxday");
  md.disabled = isLab;
}
document.getElementById("s_type").addEventListener("change", syncPriorityOptions);
syncPriorityOptions();

document.getElementById("subjectForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData();
  fd.append("year_id", document.getElementById("s_year").value);
  fd.append("code", document.getElementById("s_code").value);
  fd.append("name", document.getElementById("s_name").value);
  fd.append("type", document.getElementById("s_type").value);
  fd.append("lectures_per_week", document.getElementById("s_lpw").value);
  fd.append("priority", document.getElementById("s_priority").value);
  fd.append("max_per_day", document.getElementById("s_maxday").value);
  checkedValuesIn("s_fixedday_box").forEach(v => fd.append("external_fixed_days", v));
  fd.append("multi_faculty", document.getElementById("s_multi").checked ? "true" : "false");
  checkedValuesIn("s_combdiv_box").forEach(v => fd.append("combine_divisions", v));
  const file = document.getElementById("s_syllabus").files[0];
  if (file) fd.append("syllabus", file);

  await api("/api/subjects", { method: "POST", body: fd });
  e.target.reset();
  syncPriorityOptions();
  refreshCombineOptions(); // form.reset() doesn't fire s_year's onchange, so resync manually
  flash("Subject added. Allocate faculty to it below.");
  loadSubjects();
});

// ================================================================== TIMETABLE
let currentTTView = "master";
let currentTTId = null;

document.querySelectorAll(".tt-tab").forEach(btn => btn.addEventListener("click", () => setTTView(btn.dataset.view)));
document.getElementById("generateBtn").addEventListener("click", async () => {
  try {
    const result = await api("/api/timetable/generate", { method: "POST" });
    flash(result.unplaced.length
      ? `Generated with ${result.unplaced.length} session(s) that could not be placed.`
      : `Timetable generated — ${result.placed} periods placed.`, result.unplaced.length === 0);
    loadTimetable();
  } catch (err) { flash(err.error || "Could not generate timetable.", false); }
});

function setTTView(view) {
  currentTTView = view;
  currentTTId = null;
  document.querySelectorAll(".tt-tab").forEach(b => b.classList.toggle("active", b.dataset.view === view));
  document.getElementById("ttPill").textContent = view === "master" ? "master" : `${view}-wise`;
  loadTimetable();
}

async function populateTTPicker() {
  const picker = document.getElementById("ttPicker");
  if (currentTTView === "master") { picker.style.display = "none"; return; }
  await refreshLookups();
  let options = [];
  if (currentTTView === "class") options = LOOKUPS.divisions.map(d => ({ id: d.id, label: `${d.name} (${d.year_label})` }));
  if (currentTTView === "classroom") options = LOOKUPS.classrooms.filter(c => c.room_type === "classroom").map(c => ({ id: c.id, label: `${c.room_no} · cap ${c.capacity}` }));
  if (currentTTView === "lab") options = LOOKUPS.classrooms.filter(c => c.room_type === "lab").map(c => ({ id: c.id, label: `${c.room_no} · cap ${c.capacity}` }));
  if (currentTTView === "faculty") options = LOOKUPS.faculty.map(f => ({ id: f.id, label: f.name }));

  picker.style.display = options.length ? "block" : "none";
  picker.innerHTML = options.map(o => `<option value="${o.id}">${esc(o.label)}</option>`).join("");
  if (options.length) currentTTId = currentTTId || options[0].id;
  picker.value = currentTTId;
  picker.onchange = () => { currentTTId = picker.value; renderTimetable(); };
}

async function loadTimetable() {
  await populateTTPicker();
  await renderTimetable();
}

function endpointFor(view, id) {
  if (view === "master") return "/api/timetable/master";
  if (view === "class") return `/api/timetable/class/${id}`;
  if (view === "classroom" || view === "lab") return `/api/timetable/classroom/${id}`;
  if (view === "faculty") return `/api/timetable/faculty/${id}`;
}

async function renderTimetable() {
  const wrap = document.getElementById("ttGridWrap");
  const summary = document.getElementById("ttSummary");
  document.getElementById("ttTitle").textContent =
    currentTTView === "master" ? "Master Timetable" : document.getElementById("ttPicker").selectedOptions[0]?.textContent || "Timetable";
  document.getElementById("ttSubtitle").textContent = currentTTView === "master" ? "Every division, room and faculty in one ledger." : "";

  if ((currentTTView !== "master") && !currentTTId) {
    wrap.innerHTML = `<p class="hint">Add at least one item in this category first (steps 04–06).</p>`;
    summary.innerHTML = "";
    return;
  }

  const data = await api(endpointFor(currentTTView, currentTTId));
  LAST_TT = data;
  if (!data.periods.length) {
    wrap.innerHTML = `<p class="hint">Set up institute hours (step 01) first, then Generate.</p>`;
    summary.innerHTML = "";
    return;
  }

  if (currentTTView === "master" && data.last_run) {
    const lr = data.last_run;
    summary.innerHTML = `<div class="panel" style="padding:16px 24px;">
      <div style="display:flex; gap:24px; align-items:center; flex-wrap:wrap;">
        <span class="pill" style="background:var(--sage-tint); color:var(--sage);">${lr.placed_count} periods placed</span>
        ${lr.unplaced.length ? `<span class="pill" style="background:var(--brick-tint); color:var(--brick);">${lr.unplaced.length} unplaced</span>` : ""}
      </div>
      ${lr.unplaced.length ? `<table style="margin-top:12px;"><tr><th>Subject</th><th>Division</th><th>Reason</th></tr>
        ${lr.unplaced.map(u => `<tr><td>${esc(u.subject)}</td><td>${esc(u.division)}</td><td>${esc(u.reason)}</td></tr>`).join("")}
      </table>` : ""}
    </div>`;
  } else {
    summary.innerHTML = "";
  }

  wrap.innerHTML = buildGridHTML(data.periods, data.working_days, data.entries);
}

function entryCardHTML(e) {
  const fac = (e.faculty_names || []).join(" & ");
  return `<div class="entry-card entry-${e.entry_type}" data-group="${esc(e.session_group_id || "")}" title="Click to remove this session">
    <b>${esc(e.room_no || "Room")}</b>
    <div class="entry-subject"><strong>${esc(e.subject_code)}</strong></div>
    <div class="meta">${esc(e.division_name || "")}${e.batch_name ? " · " + esc(e.batch_name) : ""}<br>${esc(fac)}</div>
  </div>`;
}

function buildGridHTML(periods, workingDays, entries) {
  const head = `<tr><th class="corner"></th>${workingDays.map(d => `<th>${d}</th>`).join("")}</tr>`;
  const rows = periods.map(p => {
    const time = `${String(Math.floor(p.start / 60)).padStart(2, "0")}:${String(p.start % 60).padStart(2, "0")}–${String(Math.floor(p.end / 60)).padStart(2, "0")}:${String(p.end % 60).padStart(2, "0")}`;
    const cells = workingDays.map(d => {
      if (p.is_break) return `<td class="cell-break"><div class="cell-label">${esc(p.label)}</div></td>`;
      const rows = (entries[`${d}|${p.index}`] || []).slice().sort((a, b) =>
        (a.room_no || "").localeCompare(b.room_no || "", undefined, { numeric: true, sensitivity: "base" })
      );
      // Keep every assignment for this exact day/time inside ONE timetable
      // cell. This allows, for example, CS03 A1&A2 in LAB-1 and CS04 A3&A4
      // in LAB-2 at 10:00–12:00, without creating another time/day column.
      const addAttr = `data-add-day="${d}" data-add-pi="${p.index}"`;
      const inner = rows.length ? rows.map(entryCardHTML).join("") : `<div class="empty-slot clickable" ${addAttr} title="Click to add a session here">—</div>`;
      return `<td class="cell-editable"><button class="cell-add" ${addAttr} title="Add a session in this slot">+</button><div class="cell-slot">${inner}</div></td>`;
    }).join("");
    return `<tr><td class="time-col">${time}${p.is_break ? `<br><span style="text-transform:uppercase;">${esc(p.label)}</span>` : ""}</td>${cells}</tr>`;
  }).join("");
  return `<div class="tt-wrap"><table class="tt-grid">${head}${rows}</table></div>`;
}

// ---------------------------------------------------------------- PDF DOWNLOAD (editable)
async function downloadTimetablePdf(source) {
  const p = new URLSearchParams({ view: currentTTView, source });
  if (currentTTView !== "master" && currentTTId) p.set("id", currentTTId);
  try {
    const res = await fetch(`/api/timetable/download.pdf?${p}`, { credentials: "same-origin" });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      flash(esc(d.error || "Could not create the PDF."), false);
      return;
    }
    const m = /filename="?([^";]+)"?/.exec(res.headers.get("Content-Disposition") || "");
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = m ? m[1] : "timetable_editable.pdf";
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  } catch (err) { flash("Could not create the PDF.", false); }
}

async function loadPdfTemplateForm() {
  const r = await api("/api/pdf-template");
  const t = r.template;
  document.getElementById("tp_college").value = t.college;
  document.getElementById("tp_dept").value = t.department;
  document.getElementById("tp_title").value = t.title;
  document.getElementById("tp_hod").value = t.hod_name;
  document.getElementById("tp_hoddept").value = t.hod_dept;
  document.getElementById("tpViewFile").style.display = r.has_file ? "inline-flex" : "none";
  if (r.has_file) document.getElementById("tpFileHint").textContent = `Saved reference: ${r.file_name}. Choose a new file to replace it.`;
}

document.getElementById("dlDefaultBtn").addEventListener("click", () => downloadTimetablePdf("default"));
document.getElementById("dlTemplateToggle").addEventListener("click", async () => {
  const f = document.getElementById("templateForm");
  const show = f.style.display === "none";
  f.style.display = show ? "block" : "none";
  if (show) { try { await loadPdfTemplateForm(); } catch (err) { flash("Could not load the saved template.", false); } }
});
document.getElementById("tpViewFile").addEventListener("click", (e) => {
  e.preventDefault();
  window.open("/api/pdf-template/file", "_blank");
});
document.getElementById("templateForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData();
  const file = document.getElementById("tp_file").files[0];
  if (file) fd.append("template", file);
  fd.append("college", document.getElementById("tp_college").value);
  fd.append("department", document.getElementById("tp_dept").value);
  fd.append("title", document.getElementById("tp_title").value);
  fd.append("hod_name", document.getElementById("tp_hod").value);
  fd.append("hod_dept", document.getElementById("tp_hoddept").value);
  try {
    await api("/api/pdf-template", { method: "POST", body: fd });
    await downloadTimetablePdf("template");
    loadPdfTemplateForm();
  } catch (err) { flash(esc(err.error || "Could not save the template."), false); }
});

// ================================================================== BOOT
(async function boot() {
  try {
    const { admin } = await api("/api/me");
    enterApp(admin);
  } catch {
    // not logged in — auth screen (already visible by default) stays as-is
  }
})();

// ================================================================== MANUAL TIMETABLE EDITING
// Click an empty slot (or the + in a slot) to place a session by hand.
// Click a session card to remove it. All clash checks happen on the server.
let LAST_TT = null;

const _fmtMin = m => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;

document.getElementById("ttGridWrap").addEventListener("click", (ev) => {
  const add = ev.target.closest("[data-add-day]");
  if (add) return openAddDialog(add.dataset.addDay, Number(add.dataset.addPi));
  const card = ev.target.closest(".entry-card[data-group]");
  if (card && card.dataset.group) return removeSession(card.dataset.group, card.innerText.replace(/\s+/g, " ").trim());
});

async function removeSession(groupId, label) {
  if (!confirm(`Remove this session from the timetable?\n\n${label}\n\n(All its periods and batches are removed. You can add it back by clicking an empty slot.)`)) return;
  try {
    await api(`/api/timetable/session/${encodeURIComponent(groupId)}`, { method: "DELETE" });
    flash("Session removed.", true);
    await renderTimetable();
  } catch (e) { flash(e.error || "Could not remove the session.", false); }
}

function closeTTDialog() { document.getElementById("ttDialog")?.remove(); }

async function openAddDialog(day, pi) {
  await refreshLookups();
  closeTTDialog();
  const period = (LAST_TT?.periods || []).find(p => p.index === pi);
  const when = period ? `${day} · ${_fmtMin(period.start)}–${_fmtMin(period.end)}` : day;
  const defaultDiv = currentTTView === "class" ? Number(currentTTId) : null;

  const ov = document.createElement("div");
  ov.id = "ttDialog"; ov.className = "modal-back";
  ov.innerHTML = `<div class="modal-card">
    <h3>Add session</h3>
    <div class="hint" style="margin:-4px 0 14px;"><b>${esc(when)}</b></div>
    <div id="ttDlgMsg"></div>
    <div class="field"><label>Division</label>
      <select id="ttDlgDiv">${LOOKUPS.divisions.map(d => `<option value="${d.id}">${esc(d.name)} (${esc(d.year_label)})</option>`).join("")}</select></div>
    <div class="field"><label>Session</label><select id="ttDlgSess"></select>
      <div class="hint" id="ttDlgHint"></div></div>
    <div class="field"><label>Room</label><select id="ttDlgRoom"></select></div>
    <div style="display:flex; gap:10px; justify-content:flex-end; margin-top:18px;">
      <button class="btn btn-ghost" id="ttDlgCancel" type="button">Cancel</button>
      <button class="btn btn-primary" id="ttDlgSave" type="button">Add to timetable</button>
    </div></div>`;
  document.body.appendChild(ov);
  ov.addEventListener("mousedown", e => { if (e.target === ov) closeTTDialog(); });
  document.getElementById("ttDlgCancel").onclick = closeTTDialog;

  const divSel = document.getElementById("ttDlgDiv");
  const sessSel = document.getElementById("ttDlgSess");
  const roomSel = document.getElementById("ttDlgRoom");
  if (defaultDiv) divSel.value = defaultDiv;
  let OPTS = [];

  const fillRooms = () => {
    const o = OPTS[sessSel.value];
    const want = o && o.type === "lab" ? "lab" : "classroom";
    const rooms = LOOKUPS.classrooms.filter(r => r.room_type === want);
    roomSel.innerHTML = rooms.length
      ? rooms.map(r => `<option value="${r.id}">${esc(r.room_no)} · cap ${r.capacity}</option>`).join("")
      : `<option value="">No ${want} rooms defined</option>`;
    const home = want === "classroom" ? (LOOKUPS.divisions.find(d => d.id === Number(divSel.value))?.home_room || "") : "";
    const homeRoom = rooms.find(r => r.room_no.toLowerCase() === home.toLowerCase());
    if (homeRoom) roomSel.value = homeRoom.id;       // default to the division's assigned classroom (can be changed)
    document.getElementById("ttDlgHint").textContent = o
      ? `${o.type === "lab" || o.type === "tutorial" ? "Takes 2 back-to-back periods. " : ""}${o.placed} of ${o.per_week} placed so far.`
      : "";
  };
  const batchName = id => LOOKUPS.batches.find(b => b.id === id)?.name || id;
  const loadOptions = async () => {
    const r = await api(`/api/timetable/options?division_id=${divSel.value}`);
    OPTS = r.options;
    sessSel.innerHTML = OPTS.length
      ? OPTS.map((o, i) => `<option value="${i}">${esc(o.code)} · ${esc(o.name)} (${esc(o.type)})${o.batch_ids.length ? " · " + o.batch_ids.map(batchName).join("+") : ""} — ${esc(o.faculty_names.join(" & "))} — ${o.placed}/${o.per_week}</option>`).join("")
      : `<option value="">No faculty allocated for this division (step 06)</option>`;
    fillRooms();
  };
  divSel.onchange = loadOptions;
  sessSel.onchange = fillRooms;
  await loadOptions();

  document.getElementById("ttDlgSave").onclick = async () => {
    const o = OPTS[sessSel.value];
    const msg = document.getElementById("ttDlgMsg");
    if (!o || !roomSel.value) { msg.innerHTML = `<div class="msg msg-error">Pick a session and a room.</div>`; return; }
    try {
      await api("/api/timetable/entry", { method: "POST", body: JSON.stringify({
        day, period_index: pi, division_id: Number(divSel.value), subject_id: o.subject_id,
        faculty_ids: o.faculty_ids, batch_ids: o.batch_ids, classroom_id: Number(roomSel.value),
      })});
      closeTTDialog();
      flash("Session added to the timetable.", true);
      await renderTimetable();
    } catch (e) {
      msg.innerHTML = `<div class="msg msg-error">${esc(e.error || "Could not add the session.")}</div>`;
    }
  };
}

// ================================================================== PORTAL ACCOUNTS (step 09)
// Creates the logins used on /portal. The portals themselves live in portal.html + portal.js
// and read the same timetable_entry table, so nothing here needs to "push" changes to them.
function syncPortalRole() {
  const role = document.getElementById("pa_role").value;
  const sel = document.getElementById("pa_link");
  document.getElementById("pa_link_label").textContent = role === "teacher" ? "Faculty member" : "Division";
  const opts = role === "teacher"
    ? (LOOKUPS?.faculty || []).map(f => ({ id: f.id, label: f.name }))
    : (LOOKUPS?.divisions || []).map(d => ({ id: d.id, label: `${d.name} (${d.year_label})` }));
  sel.innerHTML = opts.length ? opts.map(o => `<option value="${o.id}">${esc(o.label)}</option>`).join("")
                              : `<option value="">Add ${role === "teacher" ? "faculty" : "divisions"} first</option>`;
}
document.getElementById("pa_role").addEventListener("change", syncPortalRole);
document.getElementById("pa_link").addEventListener("change", () => {
  const sel = document.getElementById("pa_link");
  const nameBox = document.getElementById("pa_name");
  if (!nameBox.value && sel.selectedOptions[0]) nameBox.value = sel.selectedOptions[0].textContent.replace(/\s*\(.*\)$/, "");
});

async function loadPortalAccounts() {
  await refreshLookups();
  syncPortalRole();
  const { users } = await api("/api/portal-users");
  const body = document.getElementById("portalList");
  body.innerHTML = users.length ? users.map(u => `<tr>
      <td><span class="tag ${u.role === "teacher" ? "tag-lab" : "tag-tutorial"}">${esc(u.role)}</span></td>
      <td>${esc(u.name)}</td><td class="mono">${esc(u.login_id)}</td>
      <td>${esc(u.role === "teacher" ? u.faculty_name : u.division_name || "—")}</td>
      <td style="text-align:right; white-space:nowrap;">
        <button class="btn btn-ghost btn-sm" data-pw="${u.id}">Reset password</button>
        <button class="btn btn-danger btn-sm" data-del="${u.id}">Delete</button></td></tr>`).join("")
    : `<tr><td colspan="5" class="hint">No portal logins yet.</td></tr>`;
  body.querySelectorAll("[data-del]").forEach(b => b.onclick = async () => {
    if (!confirm("Delete this login?")) return;
    await api(`/api/portal-users/${b.dataset.del}`, { method: "DELETE" });
    loadPortalAccounts();
  });
  body.querySelectorAll("[data-pw]").forEach(b => b.onclick = async () => {
    const pw = prompt("New password for this login:");
    if (!pw) return;
    try { await api(`/api/portal-users/${b.dataset.pw}/password`, { method: "POST", body: JSON.stringify({ password: pw }) }); flash("Password updated."); }
    catch (e) { flash(e.error || "Could not update the password.", false); }
  });
}

document.getElementById("portalForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const role = document.getElementById("pa_role").value;
  const link = document.getElementById("pa_link").value;
  try {
    await api("/api/portal-users", { method: "POST", body: JSON.stringify({
      role, name: document.getElementById("pa_name").value, login_id: document.getElementById("pa_login").value,
      password: document.getElementById("pa_pass").value,
      faculty_id: role === "teacher" ? link : null, division_id: role === "student" ? link : null,
    })});
    flash("Login created.");
    ["pa_name", "pa_login", "pa_pass"].forEach(id => document.getElementById(id).value = "");
    loadPortalAccounts();
  } catch (err) { flash(err.error || "Could not create the login.", false); }
});

async function bulkCreate(role) {
  try {
    const r = await api("/api/portal-users/bulk", { method: "POST", body: JSON.stringify({ role, password: document.getElementById("pb_pass").value }) });
    document.getElementById("bulkResult").innerHTML = r.created.length
      ? `<table style="margin-top:12px;"><tr><th>Name</th><th>Login ID</th></tr>${r.created.map(c => `<tr><td>${esc(c.name)}</td><td class="mono">${esc(c.login_id)}</td></tr>`).join("")}</table>`
      : `<p class="hint">Everyone already has a login.</p>`;
    loadPortalAccounts();
  } catch (err) { flash(err.error || "Could not create the logins.", false); }
}
document.getElementById("bulkTeachers").onclick = () => bulkCreate("teacher");
document.getElementById("bulkStudents").onclick = () => bulkCreate("student");


// ================================================================== FACULTY ALLOCATION IMPORT (Excel / CSV / PDF)
document.getElementById("importForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = document.getElementById("importBtn");
  const out = document.getElementById("importReport");
  const fd = new FormData();
  fd.append("file", document.getElementById("imp_file").files[0]);
  btn.disabled = true; btn.textContent = "Reading file…"; out.innerHTML = "";
  try {
    const r = await api("/api/faculty-allocation/import", { method: "POST", body: fd });
    const list = (arr) => arr.length ? esc(arr.join(", ")) : "—";
    out.innerHTML = `<div class="msg msg-ok" style="margin-top:16px;">
        Read ${r.rows} row(s): <b>${r.allocations_created}</b> allocation(s) added${r.duplicates ? `, ${r.duplicates} already existed` : ""}.
      </div>
      <table><tbody>
        <tr><td>New faculty</td><td>${list(r.faculty_created)}</td></tr>
        <tr><td>Faculty updated (subjects / max periods)</td><td>${list(r.faculty_updated)}</td></tr>
        <tr><td>Subjects created from the file</td><td>${list(r.subjects_created)}</td></tr>
        <tr><td>Subjects whose weekly load changed</td><td>${list(r.subjects_updated)}</td></tr>
      </tbody></table>
      ${r.skipped.length ? `<h3 style="margin-top:16px;">Not imported (${r.skipped.length})</h3>
        <table><tr><th>Row</th><th>Faculty</th><th>Why</th></tr>
        ${r.skipped.map(s => `<tr><td>${s.row}</td><td>${esc(s.faculty)}</td><td>${esc(s.reason)}</td></tr>`).join("")}</table>` : ""}`;
    document.getElementById("imp_file").value = "";
    LOOKUPS = null;
    loadFaculty();           // the imported faculty appear in the list below; allocations show in step 07
  } catch (err) {
    out.innerHTML = `<div class="msg msg-error" style="margin-top:16px;">${esc(err.error || "Import failed.")}</div>`;
  } finally {
    btn.disabled = false; btn.textContent = "Read file & allocate";
  }
});


// ================================================================== FACULTY DEMO SHEET
async function refreshDemoStatus() {
  try {
    const { custom } = await api("/api/faculty-demo-sheet/info");
    document.getElementById("demoStatus").textContent = custom ? `Using your own demo sheet: ${custom}` : "Using the built-in demo sheet.";
    document.getElementById("demoReset").style.display = custom ? "inline-flex" : "none";
  } catch (_) { /* not logged in yet */ }
}
document.getElementById("demoUploadForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData();
  fd.append("file", document.getElementById("demo_file").files[0]);
  const msg = document.getElementById("demoMsg");
  try {
    const r = await api("/api/faculty-demo-sheet", { method: "POST", body: fd });
    msg.innerHTML = `<div class="msg msg-ok" style="margin-top:12px;">Saved. "Download demo sheet" now gives ${esc(r.file_name)}.</div>`;
    document.getElementById("demo_file").value = "";
    refreshDemoStatus();
  } catch (err) { msg.innerHTML = `<div class="msg msg-error" style="margin-top:12px;">${esc(err.error || "Could not save the file.")}</div>`; }
});
document.getElementById("demoReset").onclick = async () => {
  await api("/api/faculty-demo-sheet", { method: "DELETE" });
  document.getElementById("demoMsg").innerHTML = `<div class="msg msg-ok" style="margin-top:12px;">Back to the built-in demo sheet.</div>`;
  refreshDemoStatus();
};