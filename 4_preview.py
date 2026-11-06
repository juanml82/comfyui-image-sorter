#!/usr/bin/env python3
"""Generate an interactive preview.html for manual tag merging."""

import json
import http.server
import socketserver
import webbrowser
import threading
from urllib.parse import quote
from pathlib import Path
from collections import defaultdict

from config import IMG_DIR

INPUT  = Path(IMG_DIR) / "merged.jsonl"
OUTPUT = Path(IMG_DIR) / "preview.html"

HTML_TEMPLATE = r"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>ComfyUI Organizer – Tag Preview</title>
<style>
:root {
  --bg: #1a1b26;
  --surface: #24283b;
  --surface2: #2f3349;
  --accent: #7aa2f7;
  --accent-dim: #4a5a8a;
  --text: #c0caf5;
  --text-dim: #565f89;
  --danger: #f7768e;
  --success: #9ece6a;
  --border: #3b4261;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: "Segoe UI", system-ui, sans-serif;
  font-size: 14px;
  line-height: 1.5;
}

/* ── Top bar ─────────────────────────────────────────────────────── */
#toolbar {
  position: sticky; top: 0; z-index: 100;
  display: flex; align-items: center; gap: 12px;
  padding: 10px 16px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  flex-wrap: wrap;
}
#toolbar button {
  padding: 6px 14px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--surface2);
  color: var(--text);
  cursor: pointer;
  font-size: 13px;
  transition: background .15s;
}
#toolbar button:hover { background: var(--accent-dim); }
#toolbar button.primary {
  background: var(--accent);
  color: #1a1b26;
  border-color: var(--accent);
  font-weight: 600;
}
#toolbar button.primary:hover { background: #96b4ff; }
#toolbar button:disabled { opacity: .4; cursor: default; }
#toolbar .stats { margin-left: auto; color: var(--text-dim); font-size: 12px; }

/* ── Group sections ──────────────────────────────────────────────── */
.group {
  margin: 0 16px 20px;
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
  background: var(--surface);
}
.group.selected { border-color: var(--accent); }
.group-header {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 14px;
  background: var(--surface2);
  border-bottom: 1px solid var(--border);
  cursor: pointer;
  user-select: none;
}
.group-header input[type="checkbox"] {
  width: 16px; height: 16px;
  accent-color: var(--accent);
  cursor: pointer;
}
.group-header .tag-name {
  font-weight: 600;
  font-size: 15px;
  flex: 1;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.group-header .count {
  color: var(--text-dim);
  font-size: 12px;
  min-width: 30px; text-align: right;
}

/* ── Thumbnail grid ──────────────────────────────────────────────── */
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 6px;
  padding: 10px;
}
.thumb {
  aspect-ratio: 1;
  overflow: hidden;
  border-radius: 4px;
  background: var(--bg);
}
.thumb img {
  width: 100%; height: 100%;
  object-fit: cover;
  display: block;
  opacity: 0;
  transition: opacity .3s;
}
.thumb img.loaded { opacity: 1; }

/* ── Modal for merge tag input ───────────────────────────────────── */
.modal-overlay {
  display: none;
  position: fixed; inset: 0;
  background: rgba(0,0,0,.6);
  z-index: 200;
  align-items: center; justify-content: center;
}
.modal-overlay.open { display: flex; }
.modal {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 24px;
  width: 420px; max-width: 90vw;
}
.modal h3 { margin-bottom: 12px; font-size: 16px; }
.modal p { color: var(--text-dim); font-size: 13px; margin-bottom: 12px; }
.modal input[type="text"] {
  width: 100%; padding: 8px 12px;
  background: var(--bg); color: var(--text);
  border: 1px solid var(--border); border-radius: 6px;
  font-size: 14px;
}
.modal .btn-row { margin-top: 16px; display: flex; gap: 8px; justify-content: flex-end; }
.modal button {
  padding: 7px 16px; border-radius: 6px;
  border: 1px solid var(--border);
  background: var(--surface2); color: var(--text);
  cursor: pointer;
}
.modal button.primary { background: var(--accent); color: #1a1b26; border-color: var(--accent); font-weight: 600; }
</style>
</head>
<body>

<div id="toolbar">
  <button onclick="selectAll()">Select All</button>
  <button onclick="deselectAll()">Deselect All</button>
  <button class="primary" id="btnMerge" onclick="mergeSelected()" disabled>
    Merge Selected (0)
  </button>
  <button onclick="saveJsonl()">💾 Save merged.jsonl</button>
  <span class="stats" id="stats"></span>
</div>

<div id="instructions" style="
    margin: 0 16px 12px; padding: 8px 14px;
    background: var(--surface); border: 1px solid var(--border);
    border-left: 3px solid var(--accent);
    border-radius: 6px; font-size: 12px; color: var(--text-dim);
    display: flex; align-items: center; gap: 8px;
">
  <span style="font-size:16px;">ℹ️</span>
  <span>
    Select group checkboxes → <b>Merge Selected</b> → edit target tag →
    <b>💾 Save merged.jsonl</b> to <code style="color:var(--accent);">__IMG_DIR_HINT__</code>.
    Then run <b>4. Apply</b> to move files.
  </span>
</div>

<div class="filter-panel" id="filterPanel">
  <h4>Filter by tag</h4>
  <label class="toggle-row">
    <input type="checkbox" id="filterToggle" checked> Enable filter
  </label>
  <select id="filterPct" onchange="onPctChange()">
    <option value="5">Top 5%</option>
    <option value="10" selected>Top 10%</option>
    <option value="20">Top 20%</option>
    <option value="50">Top 50%</option>
  </select>
  <div id="filterList"></div>
</div>

<div id="groups"></div>

<!-- Merge modal -->
<div class="modal-overlay" id="modalOverlay">
  <div class="modal">
    <h3>Merge selected groups</h3>
    <p id="modalInfo"></p>
    <label>Target tag:</label>
    <input type="text" id="mergeTarget" />
    <div class="btn-row">
      <button onclick="closeModal()">Cancel</button>
      <button class="primary" onclick="confirmMerge()">Merge</button>
    </div>
  </div>
</div>

<script>
// ── Embedded data ──────────────────────────────────────────────────
const DATA = __DATA_JSON__;
// DATA = [{path, tag}, ...]

let records = JSON.parse(JSON.stringify(DATA));
let selectedTags = new Set();
const TAG_STOPLIST = new Set([
  "man","woman","girl","boy","person","people",
  "adult","child","cat","dog"
]);
const FILTER_PCT = 10;

let filterTags = new Set();   // individual tokens the user checked

function computeFilterTags() {
  const freq = {};
  for (const r of records) {
    for (const token of r.tag.split(/\s+/)) {
      if (TAG_STOPLIST.has(token)) continue;   // skip generic tokens
      freq[token] = (freq[token] || 0) + 1;
    }
  }
  const entries = Object.entries(freq)
    .sort((a, b) => b[1] - a[1]);
  const cutoff = Math.max(1, Math.ceil(entries.length * FILTER_PCT / 100));
  return entries.slice(0, cutoff);   // [[token, count], …]
}

let saveHandle = null; // File System Access API handle

// ── Render ─────────────────────────────────────────────────────────
function render() {
  const container = document.getElementById("groups");
  const groups = new Map();
  for (const r of records) {
    if (!groups.has(r.tag)) groups.set(r.tag, []);
    groups.get(r.tag).push(r.path);
  }

  const sorted = [...groups.keys()].sort((a, b) => a.localeCompare(b));
  const filterActive =
    document.getElementById("filterToggle").checked && filterTags.size > 0;

  let html = "";
  for (const tag of sorted) {
    if (filterActive) {
      const tokens = new Set(tag.split(/\s+/));
      const matches = [...filterTags].some(t => tokens.has(t));
      if (!matches) continue;
    }

    const files = groups.get(tag);
    const sel = selectedTags.has(tag);
    html += `<div class="group${sel ? " selected" : ""}" id="grp-${cssEsc(tag)}">`;
    html += `<div class="group-header" onclick="toggleGroup('${jsEsc(tag)}')">`;
    html += `<input type="checkbox" ${sel ? "checked" : ""} onclick="event.stopPropagation(); toggleGroup('${jsEsc(tag)}')" />`;
    html += `<span class="tag-name">${escHtml(tag)}</span>`;
    html += `<span class="count">${files.length}</span>`;
    html += `</div><div class="grid">`;

    for (const f of files) {
      html += `<div class="thumb"><img src="${escHtml(f)}" loading="lazy" onload="this.classList.add('loaded')" onerror="this.style.display='none'"></div>`;
      }

    html += `</div></div>`;
  }
  container.innerHTML = html;
  updateStats();
}

function updateStats() {
  const nGroups = new Set(records.map(r => r.tag)).size;
  const nSel = selectedTags.size;
  document.getElementById("stats").textContent =
    `${records.length} files · ${nGroups} groups · ${nSel} selected`;
  const btn = document.getElementById("btnMerge");
  btn.textContent = `Merge Selected (${nSel})`;
  btn.disabled = nSel < 2;
}

function renderFilterList() {
  const pct = parseInt(document.getElementById("filterPct").value, 10);
  const el = document.getElementById("filterList");
  el.innerHTML = "";

  const freq = {};
  for (const r of records) {
    for (const tok of r.tag.split(/\s+/)) {
      if (TAG_STOPLIST.has(tok)) continue;
      freq[tok] = (freq[tok] || 0) + 1;
    }
  }
  const entries = Object.entries(freq).sort((a,b) => b[1]-a[1]);
  const cutoff = Math.max(1, Math.ceil(entries.length * pct / 100));

  for (const [token, count] of entries.slice(0, cutoff)) {
    const lbl = document.createElement("label");
    lbl.className = "filter-item";
    lbl.innerHTML =
      `<input type="checkbox" value="${escHtml(token)}" ` +
      `${filterTags.has(token) ? "checked" : ""}> ` +
      `${escHtml(token)} <span class="count">${count}</span>`;
    lbl.querySelector("input").addEventListener("change", e => {
      e.target.checked ? filterTags.add(token) : filterTags.delete(token);
      render();
    });
    el.appendChild(lbl);
  }
}

function onPctChange() {
  renderFilterList();
  render();
}

// ── Selection ──────────────────────────────────────────────────────
function toggleGroup(tag) {
  if (selectedTags.has(tag)) selectedTags.delete(tag);
  else selectedTags.add(tag);
  render();
}
function selectAll() {
  selectedTags = new Set(new Set(records.map(r => r.tag)));
  render();
}
function deselectAll() {
  selectedTags.clear();
  render();
}

// ── Merge flow ─────────────────────────────────────────────────────
function mergeSelected() {
  if (selectedTags.size < 2) return;
  const sortedSel = [...selectedTags].sort();
  document.getElementById("modalInfo").textContent =
    `Merging ${sortedSel.length} groups. Canonical tag will be applied to all their files.`;
  document.getElementById("mergeTarget").value = sortedSel[0];
  document.getElementById("modalOverlay").classList.add("open");
  document.getElementById("mergeTarget").focus();
  document.getElementById("mergeTarget").select();
}
function closeModal() {
  document.getElementById("modalOverlay").classList.remove("open");
}
function confirmMerge() {
  const target = document.getElementById("mergeTarget").value.trim();
  if (!target) return;
  for (const r of records) {
    if (selectedTags.has(r.tag)) r.tag = target;
  }
  selectedTags.clear();
  closeModal();
  render();
  renderFilterList();
}

// ── Save (File System Access API → fallback to download) ───────────
async function saveJsonl() {
  const lines = records.map(r => JSON.stringify({path: r.path, tag: r.tag}));
  const blob = new Blob([lines.join("\n") + "\n"], {type: "application/jsonl"});

  // Try File System Access API (Chrome / Edge)
  if (window.showSaveFilePicker) {
    try {
      const handle = await showSaveFilePicker({
        suggestedName: "merged.jsonl",
        types: [{description: "JSON Lines", accept: {"application/jsonl": [".jsonl"]}}],
      });
      const writable = await handle.createWritable();
      await writable.write(blob);
      await writable.close();
      saveHandle = handle;
      alert("Saved merged.jsonl ✓");
      return;
    } catch (e) {
      if (e.name === "AbortError") return; // user cancelled
      console.warn("FS Access API failed, falling back:", e);
    }
  }

  // Fallback: trigger a download
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "merged.jsonl";
  a.click();
  URL.revokeObjectURL(url);
}

// ── Utilities ──────────────────────────────────────────────────────
function escHtml(s) {
  return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}
function jsEsc(s) {
  return s.replace(/\\\\/g,"\\\\\\\\").replace(/'/g,"\\\\'").replace(/\\n/g,"\\\\n");
}
function cssEsc(s) {
  return s.replace(/[^a-zA-Z0-9_-]/g, c => `%${c.charCodeAt(0).toString(16)}`);
}

// Init
render();
</script>
</body>
</html>
"""


def main():
    records = []
    with open(INPUT) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    if not records:
        print(f"No records in {INPUT}")
        return

    records.sort(key=lambda r: r["tag"].lower())
    data_json = json.dumps(records, ensure_ascii=False)

    html = HTML_TEMPLATE.replace("__DATA_JSON__", data_json)
    html = html.replace("__IMG_DIR_HINT__", str(Path(IMG_DIR).resolve()))

    OUTPUT.write_text(html, encoding="utf-8")

    n_groups = len({r["tag"] for r in records})
    # ── start a local web server so image paths resolve ──────────
    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory='/', **kwargs)
        def log_message(self, fmt, *args):
            pass

    # bind to port 0 → OS picks a free one
    with socketserver.TCPServer(("127.0.0.1", 0), QuietHandler) as httpd:
        port = httpd.server_address[1]
        url = f"http://127.0.0.1:{port}{quote(str(Path(IMG_DIR).resolve()))}/preview.html"

        n_groups = len({r["tag"] for r in records})
        print(f"Preview written: {OUTPUT}")
        print(f"  {len(records)} files, {n_groups} groups")
        print(f"  Server: {url}")
        print("  Press Ctrl+C to stop the server when you're done.\n")

        webbrowser.open(url)

        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
