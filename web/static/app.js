// No build step, no framework: fetch + direct DOM updates.

const selected = new Set();
let files = [];
let roots = [];       // [{label, path}]
let currentRoot = null;
let statusTimer = null;

function fmtBytes(n) {
  if (n === null || n === undefined) return "--";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return n.toFixed(i === 0 ? 0 : 1) + " " + units[i];
}

function render() {
  const listing = document.getElementById("listing");
  const empty = document.getElementById("empty");
  if (!files.length) {
    listing.innerHTML = "";
    const path = (roots.find(r => r.label === currentRoot) || {}).path || currentRoot || "";
    empty.textContent = `No .pkg files found under ${path}.`;
    empty.hidden = false;
    return;
  }
  empty.hidden = true;

  const groups = new Map();
  for (const f of files) {
    const g = f.group || "(ungrouped)";
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(f);
  }

  let html = "";
  for (const [group, items] of groups) {
    const groupSize = items.reduce((sum, f) => sum + f.size, 0);
    html += `<div class="group">`;
    html += `<div class="group-header">`;
    html += `<label><input type="checkbox" class="group-select" data-group="${escapeAttr(group)}"> `;
    html += `<strong>${escapeHtml(group)}</strong> <span class="muted">(${items.length} file(s), ${fmtBytes(groupSize)})</span></label>`;
    html += `</div>`;
    for (const f of items) {
      const checked = selected.has(f.path) ? "checked" : "";
      html += `<label class="file-row">`;
      html += `<input type="checkbox" class="file-select" data-path="${escapeAttr(f.path)}" ${checked}>`;
      html += `<span class="file-name">${escapeHtml(f.path.split("/").pop())}</span>`;
      html += `<span class="file-size">${fmtBytes(f.size)}</span>`;
      html += `</label>`;
    }
    html += `</div>`;
  }
  listing.innerHTML = html;

  for (const el of listing.querySelectorAll(".file-select")) {
    el.addEventListener("change", (e) => {
      const path = e.target.dataset.path;
      if (e.target.checked) selected.add(path); else selected.delete(path);
      syncGroupCheckboxes();
      syncSelectAll();
    });
  }
  for (const el of listing.querySelectorAll(".group-select")) {
    el.addEventListener("change", (e) => {
      const group = e.target.dataset.group;
      const groupFiles = files.filter(f => (f.group || "(ungrouped)") === group);
      for (const f of groupFiles) {
        if (e.target.checked) selected.add(f.path); else selected.delete(f.path);
      }
      render();
    });
  }
  syncGroupCheckboxes();
  syncSelectAll();
}

function syncGroupCheckboxes() {
  for (const el of document.querySelectorAll(".group-select")) {
    const group = el.dataset.group;
    const groupFiles = files.filter(f => (f.group || "(ungrouped)") === group);
    el.checked = groupFiles.length > 0 && groupFiles.every(f => selected.has(f.path));
  }
}

function syncSelectAll() {
  const all = document.getElementById("select-all");
  all.checked = files.length > 0 && files.every(f => selected.has(f.path));
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function escapeAttr(s) { return escapeHtml(s); }

async function loadFiles() {
  if (!currentRoot) return;
  const res = await fetch(`/api/files?root=${encodeURIComponent(currentRoot)}`);
  const data = await res.json();
  files = data.files;
  document.getElementById("watch-dir").textContent = data.path;
  // drop selections for files that no longer exist
  const present = new Set(files.map(f => f.path));
  for (const p of [...selected]) if (!present.has(p)) selected.delete(p);
  render();
}

function renderRootSelect() {
  const sel = document.getElementById("root-select");
  sel.innerHTML = roots.map(r =>
    `<option value="${escapeAttr(r.label)}" ${r.label === currentRoot ? "selected" : ""}>${escapeHtml(r.label)}</option>`
  ).join("");
}

async function loadRoots() {
  const res = await fetch("/api/roots");
  const data = await res.json();
  roots = data.roots;
  if (!currentRoot || !roots.some(r => r.label === currentRoot)) {
    currentRoot = roots.length ? roots[0].label : null;
  }
  renderRootSelect();
}

document.getElementById("root-select").addEventListener("change", (e) => {
  currentRoot = e.target.value;
  selected.clear();
  loadFiles();
});

document.getElementById("refresh").addEventListener("click", loadFiles);

document.getElementById("select-all").addEventListener("change", (e) => {
  selected.clear();
  if (e.target.checked) for (const f of files) selected.add(f.path);
  render();
});

document.getElementById("add-root-btn").addEventListener("click", async () => {
  const label = document.getElementById("add-root-label").value.trim();
  const path = document.getElementById("add-root-path").value.trim();
  const err = document.getElementById("add-root-error");
  err.textContent = "";
  if (!label || !path) { err.textContent = "label and path both required."; return; }
  const res = await fetch("/api/roots", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ label, path }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { err.textContent = data.detail || res.statusText; return; }
  document.getElementById("add-root-label").value = "";
  document.getElementById("add-root-path").value = "";
  currentRoot = data.label;
  selected.clear();
  await loadRoots();
  await loadFiles();
});

document.getElementById("push").addEventListener("click", async () => {
  const host = document.getElementById("host").value.trim();
  if (!host) { alert("Enter the console's address first."); return; }
  if (!selected.size) { alert("Select at least one file."); return; }
  const streams = parseInt(document.getElementById("streams").value, 10) || 8;
  const res = await fetch("/api/push", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      host, root: currentRoot, paths: [...selected], streams,
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert("Push failed to start: " + (err.detail || res.statusText));
    return;
  }
  document.getElementById("status-card").hidden = false;
  pollStatus();
});

function renderStatus(s) {
  if (!s.running && s.files_total === 0) return; // nothing has ever pushed yet
  document.getElementById("status-card").hidden = false;
  const summary = document.getElementById("status-summary");
  const state = s.running ? "running" : (s.error ? "failed" : (s.finished_at ? "done" : "idle"));
  summary.innerHTML =
    `<span class="state state-${state}">${state}</span> ` +
    `${s.files_done}/${s.files_total} file(s) &middot; host ${escapeHtml(s.host || "--")}` +
    (s.current_rate_mb_s ? ` &middot; ${s.current_rate_mb_s.toFixed(1)} MB/s` : "") +
    (s.error ? `<div class="error">${escapeHtml(s.error)}</div>` : "");

  const pct = s.current_total ? Math.min(100, (s.current_sent / s.current_total) * 100) : 0;
  document.getElementById("progress-bar").style.width = pct + "%";
  document.getElementById("status-log").textContent = s.log.join("\n");
}

async function pollStatus() {
  const res = await fetch("/api/status");
  const s = await res.json();
  renderStatus(s);
  clearTimeout(statusTimer);
  if (s.running) {
    statusTimer = setTimeout(pollStatus, 1500);
  } else if (s.files_total > 0) {
    loadFiles(); // refresh listing once a push completes (pushed files may now show as up to date)
  }
}

(async function init() {
  await loadRoots();
  await loadFiles();
  pollStatus(); // in case a push from before this page load is still in progress
})();
