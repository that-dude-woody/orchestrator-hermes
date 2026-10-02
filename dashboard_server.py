#!/usr/bin/env python3
"""
Orchestrator Hermes - results dashboard.

Serves a single-page visual dashboard of the orchestrator's live pairings.
Reads orchestrator_hermes/dashboard/results.db (written by the orchestrator
each cycle via persist_pairings). No external dependencies - stdlib only.

Usage:
    python dashboard_server.py            # http://127.0.0.1:8787
    python dashboard_server.py --port 9000
"""

import argparse
import json
import sqlite3
import threading
import os
from http.server import HTTPServer, BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS_DB = os.path.join(HERE, "orchestrator_hermes", "dashboard", "results.db")

# HTML page served at /
PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Hermes Orchestrator - Live Pairings</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {
    --bg: #0e1116; --panel: #161b22; --border: #21262d; --text: #e6edf3;
    --dim: #8b949e; --green: #3fb950; --blue: #58a6ff; --amber: #d29922;
    --red: #f85149; --purple: #bc8cff;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text);
         font-family: -apple-system, 'Segoe UI', Roboto, sans-serif; padding: 24px; }
  h1 { font-size: 22px; font-weight: 600; margin-bottom: 4px; }
  .sub { color: var(--dim); font-size: 13px; margin-bottom: 20px; }
  .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
           gap: 12px; margin-bottom: 24px; }
  .stat { background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
          padding: 14px 16px; }
  .stat .v { font-size: 26px; font-weight: 700; color: var(--blue); }
  .stat.green .v { color: var(--green); }
  .stat.amber .v { color: var(--amber); }
  .stat.purple .v { color: var(--purple); }
  .stat .k { font-size: 12px; color: var(--dim); margin-top: 4px; }
  .section { background: var(--panel); border: 1px solid var(--border);
             border-radius: 10px; padding: 18px; margin-bottom: 24px; }
  .section h2 { font-size: 15px; font-weight: 600; margin-bottom: 14px; }
  .search { width: 100%; background: var(--bg); border: 1px solid var(--border);
            border-radius: 8px; color: var(--text); padding: 8px 12px;
            margin-bottom: 14px; font-size: 13px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; color: var(--dim); font-weight: 500; padding: 8px 10px;
       border-bottom: 1px solid var(--border); cursor: pointer; user-select: none; }
  th:hover { color: var(--text); }
  td { padding: 9px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
  tr:hover td { background: rgba(88,166,255,0.04); }
  .pill { display: inline-block; padding: 2px 8px; border-radius: 20px;
          font-size: 11px; font-weight: 600; }
  .pill.ok { background: rgba(63,185,80,.15); color: var(--green); }
  .pill.mid { background: rgba(210,153,34,.15); color: var(--amber); }
  .pill.no { background: rgba(248,81,73,.15); color: var(--red); }
  .mono { font-family: ui-monospace, Consolas, monospace; font-size: 12px; }
  .bar { height: 6px; border-radius: 4px; background: var(--border); overflow: hidden;
         margin-top: 4px; }
  .bar i { display: block; height: 100%; background: var(--blue); }
  .city-chart .row { display: grid; grid-template-columns: 90px 1fr 40px;
                     gap: 10px; align-items: center; margin-bottom: 6px; font-size: 12px; }
  .city-chart .label { color: var(--dim); white-space: nowrap; overflow: hidden;
                       text-overflow: ellipsis; }
  footer { color: var(--dim); font-size: 12px; padding: 12px 0; }
  .empty { color: var(--dim); text-align: center; padding: 30px; }
  @media (max-width: 720px) { body { padding: 12px; } table { font-size: 12px; } }
</style>
</head>
<body>
<h1>Hermes Orchestrator &mdash; Live Pairings</h1>
<div class="sub">Real buyer replies from Ytel SMS matched to cross-agent listings &middot;
auto-refreshes every 20s</div>

<div class="stats" id="stats"></div>

<div class="section">
  <h2>Pairings per run</h2>
  <div class="city-chart" id="runs"></div>
</div>

<div class="section">
  <h2>Where the buyers are looking</h2>
  <div class="city-chart" id="cities"></div>
</div>

<div class="section">
  <h2>Pairings</h2>
  <input class="search" id="q" placeholder="Filter by agent, address, city, phone...">
  <div style="overflow-x:auto">
  <table id="tbl">
    <thead><tr>
      <th data-k="generated_at">When</th>
      <th data-k="rejected_property">Rejected listing</th>
      <th data-k="rejected_agent">Rejected agent</th>
      <th data-k="bw">Buyer wants</th>
      <th data-k="best_address">Matched listing</th>
      <th data-k="best_agent">Matched agent</th>
      <th data-k="score">Score</th>
    </tr></thead>
    <tbody id="rows"></tbody>
  </table>
  </div>
  <div class="empty" id="empty" style="display:none">No pairings yet - the orchestrator
  writes them here every cycle.</div>
</div>

<footer>results.db &middot;
<button onclick="fetch('/api/refresh')" style="background:none;color:var(--dim);
border:1px solid var(--border);border-radius:6px;padding:2px 8px;cursor:pointer">refresh now</button>
</footer>

<script>
let DATA = { pairings: [], runs: [], stats: {} };

function fmtMoney(n) { return n ? ('$' + Number(n).toLocaleString()) : ''; }
function esc(s) { return String(s ?? '').replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

async function load() {
  const r = await fetch('/api/data');
  DATA = await r.json();
  render();
}

function render() {
  const s = DATA.stats || {};
  document.getElementById('stats').innerHTML = [
    `<div class="stat"><div class="v">${s.total_pairings ?? 0}</div>
     <div class="k">pairings generated</div></div>`,
    `<div class="stat green"><div class="v">${s.with_phone ?? 0}</div>
     <div class="k">matched agent phone on file</div></div>`,
    `<div class="stat amber"><div class="v">${s.campaigns ?? 0}</div>
     <div class="k">live listings in catalog</div></div>`,
    `<div class="stat purple"><div class="v">${s.replies ?? 0}</div>
     <div class="k">buyer replies analyzed</div></div>`,
    `<div class="stat"><div class="v">${s.cycles ?? 0}</div>
     <div class="k">orchestrator cycles</div></div>`,
    `<div class="stat green"><div class="v">${s.last_run ?? '-'}</div>
     <div class="k">last cycle time</div></div>`,
  ].join('');

  // runs bar chart (last 30)
  const runs = (DATA.runs || []).slice(-30);
  const maxNew = Math.max(1, ...runs.map(r => r.new_matches));
  const maxTotal = Math.max(1, ...runs.map(r => r.total_matches));
  document.getElementById('runs').innerHTML = runs.map(r => `
    <div class="row">
      <span class="label mono">${esc(String(r.ts).slice(11,19))}</span>
      <div style="display:flex;gap:2px">
        <div class="bar" style="flex:1"><i style="width:${r.total_matches/maxTotal*100}%"></i></div>
        <div class="bar" style="flex:1"><i style="width:${r.new_matches/maxNew*100}%;
             background:var(--green)"></i></div>
      </div>
      <span class="mono" style="color:var(--dim)">${r.new_matches}/${r.total_matches}</span>
    </div>`).join('') || '<div class="empty">No runs recorded yet</div>';

  // cities histogram
  const cities = {};
  for (const p of DATA.pairings || []) {
    const c = (p.best_city || p.rej_city || 'unknown');
    cities[c] = (cities[c] || 0) + 1;
  }
  const top = Object.entries(cities).sort((a,b) => b[1]-a[1]).slice(0, 12);
  const maxC = Math.max(1, ...top.map(t => t[1]));
  document.getElementById('cities').innerHTML = top.map(([c, n]) => `
    <div class="row">
      <span class="label">${esc(c)}</span>
      <div class="bar"><i style="width:${n/maxC*100}%; background:var(--purple)"></i></div>
      <span class="mono" style="color:var(--dim)">${n}</span>
    </div>`).join('') || '<div class="empty">No data yet</div>';

  renderRows();
}

function renderRows() {
  const q = document.getElementById('q').value.toLowerCase();
  const tbody = document.getElementById('rows');
  const list = (DATA.pairings || []).filter(p =>
    !q || [p.rejected_property, p.rejected_agent, p.best_agent, p.best_address,
           p.best_city, p.consumer_phone, p.best_phone]
          .join(' ').toLowerCase().includes(q));
  document.getElementById('empty').style.display = list.length ? 'none' : 'block';
  tbody.innerHTML = list.map(p => {
    const score = p.score ?? 0;
    const cls = score >= 0.85 ? 'ok' : (score >= 0.6 ? 'mid' : 'no');
    const bw = [];
    try {
      const w = JSON.parse(p.buyer_wants || '{}');
      if (w.beds_min) bw.push(w.beds_min + '+ bd');
      if (w.location) bw.push(w.location);
      if (w.locations && w.locations.length) bw.push(w.locations.join('/'));
      if (w.price_max) bw.push('&le;' + fmtMoney(w.price_max));
    } catch (e) {}
    const when = String(p.generated_at || '').slice(5, 16).replace('T', ' ');
    return `<tr>
      <td class="mono" style="color:var(--dim)">${esc(when)}</td>
      <td>${esc(p.rejected_property)}</td>
      <td>${esc(p.rejected_agent)}</td>
      <td>${bw.map(x => `<span class="pill mid">${esc(x)}</span>`).join(' ')}</td>
      <td><b>${esc(p.best_address)}</b>${p.best_city ? `, ${esc(p.best_city)}` : ''}
          ${p.best_price ? `<div class="mono" style="color:var(--dim)">${fmtMoney(p.best_price)}
                            ${p.best_beds ? '&middot; ' + p.best_beds + 'bd' : ''}</div>` : ''}</td>
      <td>${esc(p.best_agent)}
          ${p.best_phone ? `<div class="mono" style="color:var(--dim)">${esc(p.best_phone)}</div>` : ''}</td>
      <td><span class="pill ${cls}">${Number(score).toFixed(2)}</span></td>
    </tr>`;
  }).join('');
}

document.getElementById('q').addEventListener('input', renderRows);
document.querySelectorAll('th').forEach(th => th.addEventListener('click', () => {
  const k = th.dataset.k;
  DATA.pairings.sort((a,b) => String(a[k]||'').localeCompare(String(b[k]||'')));
  renderRows();
}));
load().catch(e => console.error(e));
setInterval(load, 20000);
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/data"):
            try:
                data = read_results()
                self._send(200, json.dumps(data).encode())
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}).encode())
        elif self.path.startswith("/api/refresh"):
            self._send(200, b'{"ok":true}')
        else:
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")


def read_results():
    """Pull pairings + run stats from results.db."""
    if not os.path.exists(RESULTS_DB):
        return {"pairings": [], "runs": [], "stats": {}}
    db = sqlite3.connect(f"file:{os.path.abspath(RESULTS_DB).replace(os.sep, '/')}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    pairings = [dict(r) for r in db.execute(
        "SELECT * FROM pairings ORDER BY generated_at DESC")]
    runs = [dict(r) for r in db.execute(
        "SELECT * FROM runs ORDER BY id DESC LIMIT 50")]
    latest = runs[0] if runs else {}
    n_phone = sum(1 for p in pairings if p["best_phone"])
    stats = {
        "total_pairings": len(pairings),
        "with_phone": n_phone,
        "campaigns": latest.get("campaigns"),
        "replies": latest.get("replies"),
        "cycles": len(runs),
        "last_run": (latest.get("ts") or "")[11:19] or None,
    }
    db.close()
    runs.reverse()
    return {"pairings": pairings, "runs": runs, "stats": stats}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()
    server = HTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Dashboard: http://127.0.0.1:{args.port}  (results.db: {RESULTS_DB})")
    print("Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()