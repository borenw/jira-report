#!/usr/bin/env python3
"""
chart.py — Read jira.db (produced by jira_to_db.py) and generate a single,
self-contained interactive HTML report: report.html

The report needs no internet and no libraries — all issue data is embedded in
the page and every chart / filter is drawn with plain JavaScript + inline SVG,
so you can open report.html by double-clicking it, even offline.

Features:
  * Pull-down filters: Project, User (assignee), Status, State (Open/Resolved).
  * Summary tiles: total / open / resolved.
  * Bar charts: issues by status, by project, by assignee.
  * Trend line (x-axis = day): cumulative open issues over time (burndown),
    honouring the same filters.
  * Forecast: best / likely / worst case burndown projections, each labelled
    with the date its line crosses the x-axis (i.e. projected "all done").

Usage:
    python3 chart.py                     # jira.db  -> report.html
    python3 chart.py --db jira.db --out report.html
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

COLS = ["key", "project", "summary", "issue_type", "status", "status_category",
        "priority", "assignee", "reporter", "created", "updated", "resolved",
        "labels"]


def get_version(base_dir):
    """Build a revision label from the VERSION file + git short SHA (if any)."""
    ver = "0.0.0"
    vf = os.path.join(base_dir, "VERSION")
    if os.path.exists(vf):
        with open(vf) as fh:
            ver = fh.read().strip() or ver
    label = "v" + ver
    try:
        sha = subprocess.check_output(
            ["git", "-C", base_dir, "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL, text=True).strip()
        if sha:
            label += " · " + sha   # "v1.1.0 · a1b2c3d"
    except Exception:
        pass
    return label

TEMPLATE = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Jira Report</title>
<style>
  :root {
    --bg:#f6f7f9; --card:#ffffff; --ink:#1f2430; --muted:#6b7280; --line:#e5e7eb;
    --blue:#2563eb; --green:#16a34a; --red:#dc2626; --amber:#d97706; --bar:#93c5fd;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }
  header { padding:18px 24px; background:var(--card); border-bottom:1px solid var(--line); }
  h1 { margin:0; font-size:18px; }
  .sub { color:var(--muted); font-size:12px; margin-top:2px; }
  main { padding:20px 24px; max-width:1000px; margin:0 auto; }
  .filters { display:flex; flex-wrap:wrap; gap:14px; margin-bottom:18px; }
  .filters label { display:flex; flex-direction:column; font-size:11px;
                   color:var(--muted); gap:4px; }
  select { font-size:14px; padding:6px 8px; border:1px solid var(--line);
           border-radius:8px; background:var(--card); color:var(--ink); min-width:150px; }
  .tiles { display:flex; gap:14px; flex-wrap:wrap; margin-bottom:18px; }
  .tile { flex:1; min-width:120px; background:var(--card); border:1px solid var(--line);
          border-radius:12px; padding:14px 16px; }
  .tile .n { font-size:26px; font-weight:600; }
  .tile .l { font-size:12px; color:var(--muted); }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px;
          padding:16px 18px; margin-bottom:18px; }
  .card h2 { margin:0 0 12px; font-size:14px; }
  .barrow { display:flex; align-items:center; gap:10px; margin:5px 0; font-size:13px; }
  .barrow .name { width:150px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .barrow .track { flex:1; background:#eef2f7; border-radius:6px; height:16px; overflow:hidden; }
  .barrow .fill { height:100%; background:var(--bar); }
  .barrow .val { width:44px; text-align:right; color:var(--muted); }
  svg { width:100%; height:auto; display:block; }
  .legend { display:flex; gap:16px; font-size:12px; color:var(--muted); margin-top:8px; flex-wrap:wrap; }
  .swatch { display:inline-block; width:12px; height:3px; vertical-align:middle; margin-right:5px; }
  .empty { color:var(--muted); font-style:italic; }
  .tblwrap { overflow:auto; max-height:540px; border:1px solid var(--line); border-radius:8px; }
  table.itbl { border-collapse:collapse; width:100%; font-size:13px; }
  table.itbl th, table.itbl td { text-align:left; padding:6px 10px; border-bottom:1px solid var(--line); white-space:nowrap; }
  table.itbl th { cursor:pointer; user-select:none; color:var(--muted); font-weight:600;
                  position:sticky; top:0; background:var(--card); z-index:1; }
  table.itbl th:hover { color:var(--ink); }
  table.itbl tbody tr:hover { background:#f9fafb; }
  table.itbl td.mono { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
  .arrow { font-size:10px; color:var(--blue); }
  .foot { max-width:1000px; margin:0 auto; padding:8px 24px 40px; color:var(--muted); font-size:12px; }
</style>
</head>
<body>
<header>
  <h1>Jira Report</h1>
  <div class="sub" id="meta"></div>
</header>
<main>
  <div class="filters">
    <label>Project <select id="f-project"></select></label>
    <label>User (assignee) <select id="f-assignee"></select></label>
    <label>Status <select id="f-status"></select></label>
    <label>State <select id="f-state">
      <option value="open" selected>Open (not done / resolved / closed)</option>
      <option value="resolved">Resolved / Closed</option>
      <option value="">All</option>
    </select></label>
    <label>Forecast window <select id="f-window">
      <option value="30">last 30 days</option>
      <option value="60" selected>last 60 days</option>
      <option value="90">last 90 days</option>
      <option value="0">all history</option>
    </select></label>
  </div>

  <div class="tiles">
    <div class="tile"><div class="n" id="t-total">0</div><div class="l">Issues in scope</div></div>
    <div class="tile"><div class="n" id="t-open">0</div><div class="l">Open</div></div>
    <div class="tile"><div class="n" id="t-resolved">0</div><div class="l">Resolved / Closed</div></div>
  </div>

  <div class="card">
    <h2>Open issues over time &amp; forecast</h2>
    <div id="trend"></div>
    <div class="legend">
      <span><span class="swatch" style="background:#1f2430"></span>Open (history)</span>
      <span><span class="swatch" style="background:var(--green)"></span>Best case</span>
      <span><span class="swatch" style="background:var(--blue)"></span>Likely</span>
      <span><span class="swatch" style="background:var(--red)"></span>Worst case</span>
    </div>
    <div class="legend" id="forecast-text"></div>
    <div class="sub" style="margin-top:8px">Burndown &amp; forecast always use every issue
      (open + resolved) in the selected <b>project</b>/<b>user</b> scope, so the resolution rate is
      real. The <b>Status</b> / <b>State</b> selectors drive the bars and the issue list below;
      the tiles reflect the full scope.</div>
  </div>

  <div class="card"><h2>By status</h2><div id="by-status"></div></div>
  <div class="card"><h2>By project</h2><div id="by-project"></div></div>
  <div class="card"><h2>By assignee (top 15)</h2><div id="by-assignee"></div></div>

  <div class="card">
    <h2>Issues in scope — <span id="list-count">0</span> rows
        <span class="sub" style="font-weight:400">(matches the forecast backlog · click a column header to sort)</span></h2>
    <div class="tblwrap"><table id="issue-table" class="itbl"></table></div>
  </div>
</main>
<footer class="foot" id="foot"></footer>

<script>
const ISSUES = __DATA__;
const BUILD = "__VERSION__";
const GENERATED = "__GENERATED__";

// ---------- helpers ----------
const $ = s => document.querySelector(s);
const day = s => (s ? s.slice(0,10) : null);
const DAY_MS = 86400000;
const isResolved = it => !!(it.resolved || it.status_category === "Done");

function uniq(arr){ return [...new Set(arr.filter(v => v !== null && v !== undefined && v !== ""))].sort(); }

function fillSelect(id, values, allLabel){
  const sel = $(id);
  sel.innerHTML = "";
  const optAll = document.createElement("option");
  optAll.value = ""; optAll.textContent = allLabel;
  sel.appendChild(optAll);
  values.forEach(v => {
    const o = document.createElement("option");
    o.value = v; o.textContent = v;
    sel.appendChild(o);
  });
}

function percentile(a, p){
  if(!a.length) return 0;
  const s = [...a].sort((x,y)=>x-y);
  const i = (s.length-1)*p, lo = Math.floor(i), hi = Math.ceil(i);
  return lo===hi ? s[lo] : s[lo] + (s[hi]-s[lo])*(i-lo);
}

function fmtDate(ms){ return new Date(ms).toISOString().slice(0,10); }

// ---------- filtering ----------
function currentFilters(){
  return {
    project:  $("#f-project").value,
    assignee: $("#f-assignee").value,
    status:   $("#f-status").value,
    state:    $("#f-state").value,
    window:   parseInt($("#f-window").value, 10),
  };
}

// project + user scope: drives the tiles and the burndown/forecast (needs
// resolved issues too, so it deliberately ignores the status/state selectors).
function scopePA(f){
  return ISSUES.filter(it => {
    if(f.project  && it.project  !== f.project)  return false;
    if(f.assignee && it.assignee !== f.assignee) return false;
    return true;
  });
}

// scope + status/state: drives the bars and the issue list. Defaults to open.
function applyDrill(f){
  return scopePA(f).filter(it => {
    if(f.status   && it.status   !== f.status)   return false;
    if(f.state === "open"     && isResolved(it)) return false;
    if(f.state === "resolved" && !isResolved(it)) return false;
    return true;
  });
}

// ---------- bar charts ----------
function countBy(issues, key){
  const m = {};
  issues.forEach(it => { const v = it[key] || "(none)"; m[v] = (m[v]||0)+1; });
  return Object.entries(m).sort((a,b)=>b[1]-a[1]);
}

function renderBars(id, pairs, limit){
  const el = $(id);
  if(!pairs.length){ el.innerHTML = '<div class="empty">No data.</div>'; return; }
  const rows = limit ? pairs.slice(0, limit) : pairs;
  const max = Math.max(...rows.map(r=>r[1]));
  el.innerHTML = rows.map(([name,n]) =>
    '<div class="barrow"><div class="name" title="'+name+'">'+name+'</div>'+
    '<div class="track"><div class="fill" style="width:'+(100*n/max)+'%"></div></div>'+
    '<div class="val">'+n+'</div></div>').join("");
}

// ---------- trend + forecast ----------
function buildTrend(issues){
  const created = {}, resolved = {};
  let minDay = null;
  issues.forEach(it => {
    const c = day(it.created);
    if(c){ created[c] = (created[c]||0)+1; if(!minDay || c<minDay) minDay = c; }
    const r = day(it.resolved);
    if(r){ resolved[r] = (resolved[r]||0)+1; }
  });
  if(!minDay) return null;
  const start = new Date(minDay + "T00:00:00Z");
  const today = new Date(); today.setUTCHours(0,0,0,0);
  const days = [];
  let cumC = 0, cumR = 0;
  for(let t = new Date(start); t <= today; t.setUTCDate(t.getUTCDate()+1)){
    const k = t.toISOString().slice(0,10);
    cumC += created[k]||0; cumR += resolved[k]||0;
    days.push({ t: new Date(t).getTime(), open: cumC-cumR });
  }
  return days;
}

function forecast(days, windowDays){
  if(!days || days.length < 2) return null;
  const last = days[days.length-1];
  const currentOpen = last.open;
  const w = windowDays > 0 ? Math.min(windowDays, days.length-1) : days.length-1;
  const deltas = [];
  for(let i = days.length - w; i < days.length; i++){
    if(i > 0) deltas.push(days[i].open - days[i-1].open);
  }
  if(!deltas.length) return null;
  const likely = deltas.reduce((a,b)=>a+b,0)/deltas.length;
  const best   = percentile(deltas, 0.2);   // fastest burn (most negative)
  const worst  = percentile(deltas, 0.8);   // slowest burn
  const toZero = rate => (rate < -1e-9) ? currentOpen/(-rate) : null; // days
  return {
    today: last.t, currentOpen,
    cases: {
      best:   { rate: best,   days: toZero(best) },
      likely: { rate: likely, days: toZero(likely) },
      worst:  { rate: worst,  days: toZero(worst) },
    }
  };
}

function renderTrend(days, fc){
  const host = $("#trend");
  if(!days || days.length < 2){ host.innerHTML = '<div class="empty">Not enough dated history to plot a trend.</div>'; return; }

  const W = 920, H = 360, m = { l:48, r:20, t:16, b:56 };
  const pw = W - m.l - m.r, ph = H - m.t - m.b;

  const colors = { best:"#16a34a", likely:"#2563eb", worst:"#dc2626" };
  const today = fc ? fc.today : days[days.length-1].t;
  const spanMs = days[days.length-1].t - days[0].t || DAY_MS;
  const cap = today + spanMs * 3;   // don't project further than 3x history

  // endpoints for each forecast case
  const proj = {};
  let maxProjT = today;
  if(fc){
    for(const key of ["best","likely","worst"]){
      const c = fc.cases[key];
      if(c.days !== null){
        let crossT = today + c.days * DAY_MS;
        const capped = crossT > cap;
        crossT = Math.min(crossT, cap);
        const endOpen = capped ? Math.max(0, fc.currentOpen + c.rate*((crossT-today)/DAY_MS)) : 0;
        proj[key] = { endT: crossT, endOpen, crossT: capped ? null : crossT };
        maxProjT = Math.max(maxProjT, crossT);
      } else {
        // not burning down -> extend the (flat/rising) rate a little for context
        const endT = today + spanMs*0.4;
        proj[key] = { endT, endOpen: Math.max(0, fc.currentOpen + c.rate*((endT-today)/DAY_MS)), crossT: null };
        maxProjT = Math.max(maxProjT, endT);
      }
    }
  }

  const minT = days[0].t, maxT = Math.max(today, maxProjT);
  const yMax = Math.max(1, ...days.map(d=>d.open), fc ? fc.currentOpen : 0);

  const x = t => m.l + (t - minT)/(maxT - minT || 1) * pw;
  const y = v => m.t + (1 - v/yMax) * ph;

  const parts = [];
  parts.push('<svg viewBox="0 0 '+W+' '+H+'" role="img">');

  // y gridlines + labels
  const yticks = 5;
  for(let i=0;i<=yticks;i++){
    const v = Math.round(yMax*i/yticks);
    const yy = y(v);
    parts.push('<line x1="'+m.l+'" y1="'+yy+'" x2="'+(W-m.r)+'" y2="'+yy+'" stroke="#eef2f7"/>');
    parts.push('<text x="'+(m.l-8)+'" y="'+(yy+4)+'" font-size="11" fill="#6b7280" text-anchor="end">'+v+'</text>');
  }

  // x ticks (dates)
  const xticks = 6;
  for(let i=0;i<=xticks;i++){
    const t = minT + (maxT-minT)*i/xticks;
    const xx = x(t);
    parts.push('<line x1="'+xx+'" y1="'+(m.t+ph)+'" x2="'+xx+'" y2="'+(m.t+ph+5)+'" stroke="#9ca3af"/>');
    parts.push('<text x="'+xx+'" y="'+(m.t+ph+20)+'" font-size="10" fill="#6b7280" text-anchor="middle">'+fmtDate(t)+'</text>');
  }

  // "today" marker
  const tx = x(today);
  parts.push('<line x1="'+tx+'" y1="'+m.t+'" x2="'+tx+'" y2="'+(m.t+ph)+'" stroke="#9ca3af" stroke-dasharray="3 3"/>');
  parts.push('<text x="'+tx+'" y="'+(m.t-4)+'" font-size="10" fill="#9ca3af" text-anchor="middle">today</text>');

  // historical open line
  const hist = days.map(d => x(d.t)+","+y(d.open)).join(" ");
  parts.push('<polyline points="'+hist+'" fill="none" stroke="#1f2430" stroke-width="2"/>');

  // forecast lines + crossing labels
  if(fc){
    for(const key of ["best","likely","worst"]){
      const p = proj[key];
      parts.push('<line x1="'+x(today)+'" y1="'+y(fc.currentOpen)+'" x2="'+x(p.endT)+'" y2="'+y(p.endOpen)+
                 '" stroke="'+colors[key]+'" stroke-width="2" stroke-dasharray="5 4"/>');
      if(p.crossT !== null){
        const cx = x(p.crossT);
        parts.push('<circle cx="'+cx+'" cy="'+y(0)+'" r="3.5" fill="'+colors[key]+'"/>');
        parts.push('<text x="'+cx+'" y="'+(y(0)-6)+'" font-size="10" fill="'+colors[key]+
                   '" text-anchor="middle">'+fmtDate(p.crossT)+'</text>');
      }
    }
  }

  parts.push('</svg>');
  host.innerHTML = parts.join("");

  // textual forecast summary
  const ft = $("#forecast-text");
  if(fc){
    const labels = { best:"Best", likely:"Likely", worst:"Worst" };
    ft.innerHTML = ["best","likely","worst"].map(key => {
      const c = fc.cases[key], p = proj[key];
      const when = (c.days !== null && p.crossT !== null)
        ? fmtDate(p.crossT) + " (~" + Math.round(c.days) + "d)"
        : (c.days !== null ? "beyond chart" : "no completion — backlog not shrinking");
      return '<span><span class="swatch" style="background:'+colors[key]+'"></span>'+labels[key]+': '+when+'</span>';
    }).join("");
  } else { ft.innerHTML = ""; }
}

// ---------- sortable issue list ----------
const COLUMNS = [
  { k:"key",        l:"Key",      mono:true },
  { k:"project",    l:"Project"   },
  { k:"issue_type", l:"Type"      },
  { k:"status",     l:"Status"    },
  { k:"priority",   l:"Priority"  },
  { k:"assignee",   l:"Assignee"  },
  { k:"created",    l:"Created",  date:true },
  { k:"resolved",   l:"Resolved", date:true },
];
const PRANK = { Highest:0, High:1, Medium:2, Low:3, Lowest:4 };
let sortKey = "created", sortDir = -1;   // newest first by default
let listRows = [];

function sortBy(k){
  const col = COLUMNS.find(c => c.k === k);
  if(sortKey === k) sortDir = -sortDir;
  else { sortKey = k; sortDir = (col && col.date) ? -1 : 1; }
  renderTable();
}

function renderTable(){
  const val = it => (sortKey === "priority")
    ? (PRANK[it.priority] !== undefined ? PRANK[it.priority] : 99)
    : it[sortKey];
  const rows = [...listRows].sort((a,b) => {
    let x = val(a), y = val(b);
    const ax = (x === null || x === undefined || x === "");
    const ay = (y === null || y === undefined || y === "");
    if(ax && ay) return 0; if(ax) return 1; if(ay) return -1;   // blanks last
    if(x < y) return -sortDir; if(x > y) return sortDir; return 0;
  });
  $("#list-count").textContent = rows.length;

  const head = "<thead><tr>" + COLUMNS.map(c => {
    const arrow = c.k === sortKey ? ' <span class="arrow">' + (sortDir < 0 ? "▼" : "▲") + "</span>" : "";
    return '<th onclick="sortBy(\'' + c.k + '\')">' + c.l + arrow + "</th>";
  }).join("") + "</tr></thead>";

  const esc = s => String(s).replace(/[&<>]/g, m => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[m]));
  const body = "<tbody>" + rows.map(it => "<tr>" + COLUMNS.map(c => {
    let v = it[c.k];
    if(c.date) v = v ? v.slice(0,10) : "";
    if(v === null || v === undefined || v === "") v = "—";
    return '<td class="' + (c.mono ? "mono" : "") + '">' + esc(v) + "</td>";
  }).join("") + "</tr>").join("") + "</tbody>";

  $("#issue-table").innerHTML = head + body;
}

// ---------- orchestration ----------
function render(){
  const f = currentFilters();
  const scope = scopePA(f);      // project + user  -> tiles + burndown
  const view  = applyDrill(f);   // + status/state  -> bars + list

  $("#t-total").textContent    = scope.length;
  $("#t-open").textContent     = scope.filter(it => !isResolved(it)).length;
  $("#t-resolved").textContent = scope.filter(isResolved).length;

  renderBars("#by-status",   countBy(view, "status"));
  renderBars("#by-project",  countBy(view, "project"));
  renderBars("#by-assignee", countBy(view, "assignee"), 15);

  const days = buildTrend(scope);
  const fc = forecast(days, f.window);
  renderTrend(days, fc);

  listRows = view;
  renderTable();
}

function init(){
  $("#meta").textContent = ISSUES.length + " issues · " + BUILD + " · generated " + GENERATED;
  $("#foot").textContent = "Generated by chart.py " + BUILD + " · " + GENERATED;
  fillSelect("#f-project",  uniq(ISSUES.map(i=>i.project)),  "All projects");
  fillSelect("#f-assignee", uniq(ISSUES.map(i=>i.assignee)), "All users");
  fillSelect("#f-status",   uniq(ISSUES.map(i=>i.status)),   "All statuses");
  ["#f-project","#f-assignee","#f-status","#f-state","#f-window"]
    .forEach(id => $(id).addEventListener("change", render));
  render();
}
init();
</script>
</body>
</html>
'''


def main():
    ap = argparse.ArgumentParser(description="Generate an HTML report from jira.db")
    ap.add_argument("--db", default="jira.db", help="input SQLite database")
    ap.add_argument("--out", default="report.html", help="output HTML file")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        sys.exit(f"error: database not found: {args.db} (run jira_to_db.py first)")

    conn = sqlite3.connect(args.db)
    rows = conn.execute(f"SELECT {', '.join(COLS)} FROM issues").fetchall()
    conn.close()
    issues = [dict(zip(COLS, r)) for r in rows]

    base_dir = os.path.dirname(os.path.abspath(__file__))
    version = get_version(base_dir)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Replace the version/timestamp tokens before the data blob, so issue text
    # that happens to contain a token can never be clobbered.
    html = (TEMPLATE
            .replace("__VERSION__", version)
            .replace("__GENERATED__", generated)
            .replace("__DATA__", json.dumps(issues)))
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"wrote {args.out} ({len(issues)} issues, {version}). Open it in a browser.")


if __name__ == "__main__":
    main()
