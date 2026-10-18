#!/usr/bin/env python3
"""
PEGE energy sim — local form server. Open source.
Copyright (c) 2026 Roland Mösl founder@pege.org
Licensed under the GNU General Public License v3.0. See LICENSE.

The default browser is the GUI. The form posts back here; this process
runs the simulation and streams the log.

https://github.com/pegefounder/energy-sim
https://www.pege.org/energy-sim/

Local server, same idea as a Perl server.pl:

  python pvgis_server.py

starts a localhost HTTP server and opens the system default browser.
Works on Linux and Windows. The form posts every field back here.
The server runs pvgis_multi_pv.build_report() and streams the log.

Stop with Ctrl+C in the terminal.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import traceback
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
try:
    os.chdir(HERE)
except Exception:
    pass



INVEST_HTML = """
<h2>Investment model</h2>
<nav style="margin-bottom:14px">
  <a href="/investment">General</a> |
  <a href="/investment/baseline">Baseline</a> |
  <a href="/investment/a">A</a> |
  <a href="/investment/b">B</a> |
  <a href="/investment/c">C</a> |
  <a href="/investment/d">D</a> |
  <a href="/investment/e">E</a> |
  <a href="/investment/run">Run</a>
</nav>
<form method="post" action="/investment">
  <h3>Money and time</h3>
  <div style="display:flex;gap:12px;flex-wrap:wrap">
    <div><label>Bank interest %</label><br><input name="bank_int" value="0.5" style="width:90px"></div>
    <div><label>Credit interest %</label><br><input name="credit_int" value="4" style="width:90px"></div>
    <div><label>General inflation %</label><br><input name="inf_gen" value="2.5" style="width:90px"></div>
    <div><label>Electricity inflation %</label><br><input name="inf_el" value="3" style="width:90px"></div>
    <div><label>Fossil inflation %</label><br><input name="inf_fos" value="3.5" style="width:90px"></div>
  </div>
  <h3>Credit run time and depreciation</h3>
  <table>
    <tr><th></th><th>PV</th><th>Battery</th><th>Heat pump</th><th>House</th><th>Ground</th></tr>
    <tr><td>Credit years</td>
      <td><input name="pv_y" value="25" style="width:70px"></td>
      <td><input name="bat_y" value="15" style="width:70px"></td>
      <td><input name="hp_y" value="15" style="width:70px"></td>
      <td><input name="house_y" value="30" style="width:70px"></td>
      <td><input name="ground_y" value="30" style="width:70px"></td>
    </tr>
    <tr><td>Depreciation % / year</td>
      <td><input name="pv_p" value="3" style="width:70px"></td>
      <td><input name="bat_p" value="4" style="width:70px"></td>
      <td><input name="hp_p" value="4" style="width:70px"></td>
      <td><input name="house_p" value="1" style="width:70px"></td>
      <td><input name="ground_p" value="0" style="width:70px"></td>
    </tr>
  </table>
  <div class="field wide"><label>Description</label><textarea name="gen_desc" rows="3">Typically in Austria in the last 25 years</textarea></div>
  <h3>Project</h3>
  <div class="field"><label>Project name</label><input name="proj_name" value="5 variants for my house improvement"></div>
  <div class="field wide"><label>Project description</label><textarea name="proj_desc" rows="3">Scenario 5 with all the photovoltaic looks great but now to find out about money</textarea></div>
  <button type="submit">Save general</button>
</form>
"""

BASELINE_HTML = """
<h2>Investment model</h2>
<nav style="margin-bottom:14px">
  <a href="/investment">General</a> |
  <a href="/investment/baseline">Baseline</a> |
  <a href="/investment/a">A</a> |
  <a href="/investment/b">B</a> |
  <a href="/investment/c">C</a> |
  <a href="/investment/d">D</a> |
  <a href="/investment/e">E</a> |
  <a href="/investment/run">Run</a>
</nav>
<form method="post" action="/investment/baseline">
  <h3>Baseline</h3>
  <p class="hint">Monthly electricity and fossil can be entered only when "no stored scenario" is selected. Otherwise the energy is handled by the selected scenario.</p>
  <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:12px">
    <div><label>Money on the bank</label><br><input name="base_money" value="0" style="width:120px"></div>
    <div><label>Monthly electricity</label><br><input name="base_el" id="baseEl" value="150" style="width:120px"></div>
    <div><label>Monthly fossil</label><br><input name="base_fos" id="baseFos" value="250" style="width:120px"></div>
  </div>
  <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:12px">
    <div style="flex:1"><label>Starting scenario — object</label><br>
      <select name="base_object" id="baseObj" style="width:100%"><option value="">— no stored scenario —</option></select>
    </div>
    <div style="flex:1"><label>Starting scenario — simulation</label><br>
      <select name="base_file" id="baseFile" style="width:100%"><option value="">— simulation —</option></select>
    </div>
  </div>
  <div class="field wide"><label>Description</label><textarea name="base_desc" rows="3">My old house burning fuel oil like crazy</textarea></div>
  <button type="submit">Save baseline</button>
</form>
<script>
function outdirQ() { return encodeURIComponent(location.search.includes("outdir=") ? new URLSearchParams(location.search).get("outdir") || "" : ""); }
function fillBaseFile(obj) {
  const sel = document.getElementById("baseFile");
  sel.innerHTML = "<option value=''>— simulation —</option>";
  if (!obj) return;
  fetch("/api/object-files?object=" + encodeURIComponent(obj)).then(r => r.json()).then(data => {
    (data.files || []).forEach(f => {
      const opt = document.createElement("option");
      opt.value = f.path; opt.textContent = f.name;
      sel.appendChild(opt);
    });
  }).catch(() => {});
}
fetch("/api/workspace?outdir=" + outdirQ()).then(r => r.json()).then(data => {
  const sel = document.getElementById("baseObj");
  (data.objects || []).forEach(n => {
    const opt = document.createElement("option");
    opt.value = n; opt.textContent = n;
    sel.appendChild(opt);
  });
}).catch(() => {});
document.getElementById("baseObj").addEventListener("change", function () {
  const has = !!this.value;
  document.getElementById("baseEl").disabled = has;
  document.getElementById("baseFos").disabled = has;
  if (has) {
    document.getElementById("baseEl").value = "0";
    document.getElementById("baseFos").value = "0";
    fillBaseFile(this.value);
  } else {
    document.getElementById("baseEl").value = "150";
    document.getElementById("baseFos").value = "250";
    fillBaseFile("");
  }
});
</script>
"""

def _missing_demand(*_a, **_k):
    return {}


def _missing_demand_html(_v=None):
    return (
        "<p class='hint' style='color:#a00'>"
        "pege_energy_sim_demand.py is missing. Put it in the same folder as pege_energy_sim_server_uses_default_browser_as_gui.py."
        "</p>"
    )


try:
    from pege_energy_sim_demand import demand_form_html, demand_to_form_values, parse_demand_fields  # noqa: E402
except Exception as _exc:
    print("WARNING: demand_model.py not loaded:", _exc)
    demand_form_html = _missing_demand_html
    demand_to_form_values = _missing_demand
    parse_demand_fields = _missing_demand

try:
    from pege_energy_sim_report import (  # noqa: E402
        CONFIG,
        build_report,
        list_objects,
        location_remark,
        location_stem,
        object_definition_path,
        object_folder,
        objects_root,
        sanitize_name,
    )
except Exception as _exc:
    print("ERROR: cannot import pege_energy_sim_report.py:", _exc)
    print("Put pege_energy_sim_report.py next to pege_energy_sim_server_uses_default_browser_as_gui.py.")
    print("Also needed: pip install matplotlib numpy pandas")
    if __name__ == "__main__":
        traceback.print_exc()
        try:
            input("\nPress Enter to close...")
        except Exception:
            pass
    raise

def _need_packages() -> None:
    missing = []
    for name in ("numpy", "pandas", "matplotlib", "requests"):
        try:
            __import__(name)
        except Exception:
            missing.append(name)
    if not missing:
        return
    text = (
        "PEGE energy sim cannot start.\n\n"
        "These Python packages are missing:\n"
        + "\n".join(missing)
        + "\n\nInstall them, then start again:\n"
        "pip install numpy pandas matplotlib requests\n\n"
        "On Linux, if pip is missing: sudo apt install python3-pip"
    )
    print("\n" + text + "\n")
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showerror("PEGE energy sim — missing packages", text)
        root.destroy()
    except Exception:
        pass
    raise SystemExit(1)


_need_packages()

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
# Folder the process was started in. Objects, locations and compares live here.
START_DIR = Path.cwd().resolve()


def data_root() -> Path:
    """Always the folder where pege_energy_sim_server_uses_default_browser_as_gui.py was started."""
    return START_DIR
N_ROWS = 25
JOB_LOCK = threading.Lock()
STOP = {"flag": False}
ARTIFACTS: dict[str, Path] = {}
LAST_FORM: dict[str, str] = {}
BASE_URL = f"http://{HOST}:{DEFAULT_PORT}"
DEFAULT_OBJECT = "GEMINI-60-house-with-garage"


def workspace_roots() -> list[Path]:
    roots: list[Path] = []
    for raw in (HERE, Path.cwd()):
        try:
            roots.append(raw.expanduser().resolve())
        except Exception:
            pass
    if LAST_FORM.get("outdir"):
        try:
            roots.append(Path(LAST_FORM["outdir"]).expanduser().resolve())
        except Exception:
            pass
    out, seen = [], set()
    for r in roots:
        if r in seen:
            continue
        seen.add(r)
        out.append(r)
    return out


def find_object_json(object_name: str) -> Path | None:
    name = sanitize_name(object_name or DEFAULT_OBJECT)
    if not name:
        return None
    for root in workspace_roots():
        for path in (
            root / "objects" / name / f"{name}.json",
            root / "objects" / f"{name}.json",
            root / f"{name}.json",
        ):
            if path.is_file():
                return path
    return None


def values_from_object_file(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    vals = data.get("form_values") or sidecar_to_form_values(data) or {}
    if data.get("demand"):
        vals.update(demand_to_form_values(data["demand"]))
    vals = {str(k): "" if v is None else str(v) for k, v in vals.items()}
    vals["object"] = data.get("object") or path.stem
    # Output root is the folder where this server was started, not a saved path.
    vals.pop("outdir", None)
    return vals


def startup_form_values() -> dict[str, str]:
    path = find_object_json(DEFAULT_OBJECT)
    if path is None:
        names = []
        for root in workspace_roots():
            names.extend(list_objects(root))
        if names:
            path = find_object_json(names[0])
    if path is None:
        return {"object": DEFAULT_OBJECT}
    try:
        print(f"{datetime.now():%Y-%m-%d %H:%M}  startup object {path}")
        return values_from_object_file(path)
    except Exception as exc:
        print(f"Could not read {path}: {exc}")
        return {"object": DEFAULT_OBJECT}


def find_chromium() -> str:
    env = os.environ.get("CHROMIUM") or os.environ.get("BROWSER_CHROMIUM")
    if env and Path(env).exists():
        return env
    names = (
        "chromium",
        "chromium-browser",
        "chromium-browser-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/snap/bin/chromium",
        "/usr/lib/chromium/chromium",
        "google-chrome",
        "google-chrome-stable",
        "/usr/bin/google-chrome",
    )
    for name in names:
        found = shutil.which(name) if "/" not in name else (name if Path(name).exists() else None)
        if found:
            return found
    raise FileNotFoundError(
        "Chromium not found. Install chromium, or set CHROMIUM=/path/to/chromium"
    )


def open_browser(url: str):
    """Default OS browser (Windows / Linux / macOS). Chromium only if forced."""
    import webbrowser

    if os.environ.get("BROWSER_CHROMIUM"):
        binary = find_chromium()
        print(f"{datetime.now():%Y-%m-%d %H:%M}  opening {binary}")
        return subprocess.Popen(
            [binary, "--new-window", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    print(f"{datetime.now():%Y-%m-%d %H:%M}  opening default browser {url}")
    webbrowser.open(url)
    return None


def page_shell(title: str, body: str) -> bytes:
    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<link rel="icon" href="/favicon.ico" sizes="any">
<link rel="icon" type="image/png" href="/pege-logo.png">
<style>
  :root {{
    --eu: #003399;
    --eu2: #1a4dad;
    --line: #d5dbe6;
    --bg: #f3f5f8;
  }}
  html, body {{ margin: 0; padding: 0; background: var(--bg);
    font: 14px/1.4 "Segoe UI", DejaVu Sans, sans-serif; color: #1a1a1a; }}
  header {{ background: var(--eu); color: #fff; position: relative; padding: 12px 20px; }}
  header h1 {{ margin: 0; font-size: 17px; font-weight: 650; letter-spacing: .02em; }}
  header p {{ margin: 4px 0 0; font-size: 12px; opacity: .85; }}
  main {{ max-width: 1180px; margin: 16px auto 40px; background: #fff;
    border: 1px solid var(--line); padding: 16px 18px 22px; }}
  label {{ display: block; font-size: 12px; color: #445; margin-bottom: 3px; }}
  input, select {{ font: inherit; padding: 5px 7px; border: 1px solid #b8c0cc;
    border-radius: 3px; background: #fff; }}
  input:focus, select:focus {{ outline: 2px solid #9bb6ff; border-color: var(--eu2); }}
  .row {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }}
  .field {{ display: flex; flex-direction: column; }}
  .field.name {{ flex: 2; min-width: 180px; }}
  .field.coord {{ width: 140px; }}
  .field.small {{ width: 148px; }}
  .field.wide {{ flex: 1; min-width: 220px; }}
  .field.tiny {{ width: 64px; }}
  fieldset {{ border: 1px solid var(--line); margin: 14px 0; padding: 10px 12px 6px; }}
  fieldset legend {{ font-weight: 650; color: var(--eu); padding: 0 6px; }}
  table.inline-fields {{ width: 100%; table-layout: fixed; margin: 4px 0 8px; }}
  table.inline-fields th {{ white-space: nowrap; font-weight: 600; }}
  table.inline-fields td {{ padding: 2px 4px; }}
  table.inline-fields input {{ width: 100%; box-sizing: border-box; }}
  main {{ max-width: 1180px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 8px 0 14px; }}
  th {{ text-align: left; font-size: 12px; color: #334; padding: 4px 6px;
    border-bottom: 2px solid var(--eu); }}
  td {{ padding: 3px 6px; border-bottom: 1px solid #eef1f5; }}
  td.n {{ width: 36px; color: #667; text-align: right; }}
  td.pvname {{ width: 24%; }}
  td.bi {{ width: 56px; text-align: center; }}
  td.bi input {{ width: auto; }}
  td input {{ width: 100%; box-sizing: border-box; }}
  .hint {{ font-size: 12px; color: #556; margin: 0 0 10px; }}
  button {{ background: var(--eu); color: #fff; border: 0; padding: 8px 18px;
    font: 600 14px inherit; border-radius: 3px; cursor: pointer; }}
  button:hover {{ background: var(--eu2); }}
  button.secondary {{ background: #fff; color: var(--eu); border: 1px solid var(--eu); }}
  #browser {{ margin: 12px 0 16px; border: 1px solid var(--line); padding: 10px 12px; background: #fafbfd; }}
  #browser[hidden] {{ display: none; }}
  #browser h2 {{ margin: 0 0 8px; font-size: 14px; }}
  #browser .folder {{ margin: 8px 0; }}
  #browser .folder > .fname {{ font-weight: 650; }}
  #browser ul {{ margin: 4px 0 0 18px; padding: 0; }}
  #browser li {{ list-style: disc; margin: 2px 0; font-size: 13px; }}
  #browser a.pdf {{ font-weight: 650; }}
  #browser .other {{ color: #667; }}
  .actions {{ display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }}
  a {{ color: var(--eu); text-decoration: none; }}
  a:hover {{ background: #eef3fb; }}
  pre.log, div.log {{ background: #0f1a2b; color: #d6e2f0; padding: 12px 14px;
    font: 12px/1.45 ui-monospace, Consolas, monospace; white-space: pre-wrap;
    max-height: 70vh; overflow: auto; }}
  .ok {{ color: #b6f3c4; }}
  .err {{ color: #ffb3b3; }}
  .nav {{ margin: 12px 0; }}
  nav.menu {{ display: flex; flex-wrap: wrap; gap: 8px 14px; margin-top: 8px; }}
  nav.menu a {{ color: #fff; text-decoration: none; font-size: 14px; }}
  nav.menu a:hover {{ background: rgba(255,255,255,0.18); border-radius: 3px; }}
  button.mv {{ width: 18px; height: 16px; padding: 0; margin: 0 1px; font-size: 10px; line-height: 14px; }}
  td.n {{ white-space: nowrap; width: 48px; }}
</style>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>


</script>
<script>
(function () {{
  function pin() {{
    var box = document.getElementById("log");
    if (!box) return;
    var near = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    if (near) box.scrollTop = box.scrollHeight;
  }}
  setInterval(pin, 200);
  document.addEventListener("DOMContentLoaded", pin);
}})();
</script>
</head>
<body>
<header>
  <h1>PEGE energy sim</h1>
  <p>Local server — form posts back here — Default browser used as GUI</p>
  <a href="/version-test" style="position:absolute;right:0;top:0;width:80px;height:100%;"></a>
  <div id="verbox" style="display:none;position:absolute;right:12px;top:8px;background:#fff8d6;color:#6a5200;padding:8px 12px;border-radius:4px;max-width:360px;font-size:13px">
    A newer version is listed.
    <div style="margin-top:6px"><a href="https://github.com/pegefounder/energy-sim" target="_blank">GitHub code only</a></div>
    <div><a href="https://pege.org/energy-sim/" target="_blank">PEGE.org code with examples</a></div>
  </div>
  <nav class="menu">
    <a href="/files">Files</a>
    <a href="/form">Object house</a>
    <a href="/compare-houses">Compare houses</a>
    <a href="/investment">Investment model</a>
    <a href="/objects">Other objects</a>
    <a href="/community">Community</a>
    <a href="/compare-communities">Compare communities</a>
    <a href="/about">About</a>
  </nav>
</header>
<main>
{body}
</main>
</body>
</html>
"""
    return doc.encode("utf-8")



FILES_HTML = """
<h2 id="filesTitle">Objects</h2>
<nav style="margin:8px 0 14px">
  <a href="/files" style="margin-right:16px">Objects</a>
  <a href="/files?tab=compares" style="margin-right:16px">Compares</a>
  <a href="/files?tab=locations" style="margin-right:16px">Locations</a>
  <a href="/files?tab=investment" style="margin-right:16px">Investment models</a>
  <a href="/files?tab=other">Other</a>
  <button type="button" id="rerunAll" style="margin-left:16px;display:none">rerun all marked object reports</button>
</nav>
<p class="hint" id="filesStatus">Reading…</p>
<div id="filesList"></div>
<p><a href="/form">Back to main</a></p>
<script>
(function () {
  const tab = new URLSearchParams(location.search).get("tab") || "objects";
  const titles = {objects: "Objects", compares: "Compares", locations: "Locations", investment: "Investment models", other: "Other"};
  document.getElementById("filesTitle").textContent = titles[tab] || "Objects";
  const list = document.getElementById("filesList");
  const status = document.getElementById("filesStatus");
  fetch("/api/files-index").then(function (r) { return r.json(); }).then(function (data) {
    const groups = data[tab] || [];
    status.textContent = groups.length ? "" : "Nothing here.";
    if (tab === "investment") {
      groups.forEach(function (g) {
        const table = document.createElement("table");
        table.innerHTML = "<thead><tr><th>open</th><th>edit</th><th>" + g.name + "</th></tr></thead>";
        const body = document.createElement("tbody");
        (g.files || []).forEach(function (f) {
          const tr = document.createElement("tr");
          const open = document.createElement("td");
          if (f.href && f.name.toLowerCase().endsWith(".pdf") === false && f.href.includes(".pdf")) {
            open.innerHTML = '<a href="' + f.href + '" target="_blank">open</a>';
          } else if (f.href && f.href.includes(".pdf")) {
            open.innerHTML = '<a href="' + f.href + '" target="_blank">open</a>';
          } else {
            open.textContent = "";
          }
          const edit = document.createElement("td");
          if (f.json) {
            edit.innerHTML = '<a href="/investment?json=' + encodeURIComponent(f.json) + '">edit</a>';
          }
          const name = document.createElement("td");
          name.textContent = f.name;
          tr.appendChild(open); tr.appendChild(edit); tr.appendChild(name);
          body.appendChild(tr);
        });
        table.appendChild(body);
        list.appendChild(table);
      });
      return;
    }
    if (tab === "objects") {
      const folder = new URLSearchParams(location.search).get("folder");
      if (!folder) {
        const marks = JSON.parse(localStorage.getItem("rerunMarks") || "{}");
        groups.forEach(function (g) {
          const div = document.createElement("div");
          div.className = "folder";
          div.style.display = "flex";
          div.style.alignItems = "center";
          div.style.gap = "12px";
          const a = document.createElement("a");
          a.href = "/files?tab=objects&folder=" + encodeURIComponent(g.name);
          a.textContent = g.name;
          div.appendChild(a);
          const files = (g.files || []).filter(function (f) { return !f.name.endsWith("  (object)"); });
          const N = files.length;
          let n = 0;
          files.forEach(function (f) {
            if (marks[g.name + "/" + f.name]) n++;
          });
          const count = document.createElement("span");
          count.textContent = n + "/" + N;
          count.style.marginLeft = "auto";
          div.appendChild(count);
          const cb = document.createElement("input");
          cb.type = "checkbox";
          cb.checked = N > 0 && n === N;
          cb.onchange = function () {
            const m = JSON.parse(localStorage.getItem("rerunMarks") || "{}");
            files.forEach(function (f) {
              const key = g.name + "/" + f.name;
              if (cb.checked) m[key] = true; else delete m[key];
            });
            localStorage.setItem("rerunMarks", JSON.stringify(m));
            location.reload();
          };
          div.appendChild(cb);
          list.appendChild(div);
        });
        return;
      }
      const g = groups.filter(function (x) { return x.name === folder; })[0];
      if (!g) { status.textContent = "No such object."; return; }
      const back = document.createElement("p");
      back.innerHTML = "<a href='/files'>All objects</a>";
      list.appendChild(back);
      const table = document.createElement("table");
      table.innerHTML = "<thead><tr><th>open</th><th>edit</th><th>" + folder + "</th><th>by</th><th>rerun</th></tr></thead>";
      const body = document.createElement("tbody");
      const marks = JSON.parse(localStorage.getItem("rerunMarks") || "{}");
      function updateBtn() {
        const any = Object.values(JSON.parse(localStorage.getItem("rerunMarks") || "{}")).some(Boolean);
        const btn = document.getElementById("rerunAll");
        if (btn) btn.style.display = any ? "inline" : "none";
      }
      const menuBtn = document.getElementById("rerunAll");
      if (menuBtn) menuBtn.onclick = function () {
        const selected = Object.keys(JSON.parse(localStorage.getItem("rerunMarks") || "{}")).filter(function (k) {
          return JSON.parse(localStorage.getItem("rerunMarks") || "{}")[k];
        });
        if (!selected.length) return;
        window.location = "/rerun?files=" + encodeURIComponent(selected.join("|"));
      };
      (g.files || []).forEach(function (f) {
        const tr = document.createElement("tr");
        const open = document.createElement("td");
        const oa = document.createElement("a");
        if (f.href) { oa.href = f.href; oa.textContent = "open"; oa.target = "_blank"; open.appendChild(oa); }
        const edit = document.createElement("td");
        const ea = document.createElement("a");
        ea.href = f.edit || "#"; ea.textContent = "edit";
        edit.appendChild(ea);
        const name = document.createElement("td");
        name.textContent = f.name;
        const by = document.createElement("td");
        by.textContent = f.version || "0.0";
        const rerun = document.createElement("td");
        if (!f.name.endsWith("  (object)")) {
          const cb = document.createElement("input");
          cb.type = "checkbox";
          const key = folder + "/" + f.name;
          cb.checked = !!marks[key];
          cb.onchange = function () {
            const m = JSON.parse(localStorage.getItem("rerunMarks") || "{}");
            if (cb.checked) m[key] = true; else delete m[key];
            localStorage.setItem("rerunMarks", JSON.stringify(m));
            updateBtn();
          };
          rerun.appendChild(cb);
        }
        tr.appendChild(open); tr.appendChild(edit); tr.appendChild(name); tr.appendChild(by); tr.appendChild(rerun);
        body.appendChild(tr);
      });
      table.appendChild(body);
      list.appendChild(table);
      updateBtn();
      return;
    }
    groups.forEach(function (g) {
      const div = document.createElement("div");
      div.className = "folder";
      const title = document.createElement("div");
      title.className = "fname";
      title.textContent = g.name;
      div.appendChild(title);
      const ul = document.createElement("ul");
      (g.files || []).forEach(function (f) {
        const li = document.createElement("li");
        const a = document.createElement("a");
        a.href = f.href;
        a.textContent = f.name;
        if (f.download) a.setAttribute("download", "");
        li.appendChild(a);
        ul.appendChild(li);
      });
      div.appendChild(ul);
      list.appendChild(div);
    });
  }).catch(function (err) { status.textContent = String(err); });
})();
</script>
"""

COMPARE_HTML = """
<h2>Compare houses</h2>
<p class="hint">Difference is B − A. Green means B is better. More export is better, more import is worse.</p>
<div class="row">
  <div class="field name"><label>Object A</label><select id="cmpObj1"><option value="">— object —</option></select></div>
  <div class="field wide"><label>Simulation A</label><select id="cmpFile1"><option value="">— file —</option></select></div>
</div>
<div class="row">
  <div class="field name"><label>Object B</label><select id="cmpObj2"><option value="">— object —</option></select></div>
  <div class="field wide"><label>Simulation B</label><select id="cmpFile2"><option value="">— file —</option></select></div>
</div>
<p class="hint" id="cmpStatus"></p>
<div class="actions"><button type="button" id="cmpBtn">Compare — write PDF</button></div>
<div id="log" class="log" style="display:none"></div>
<p><a href="/form">Back to main</a></p>
<script>
(function () {
  function fill(sel, obj) {
    sel.innerHTML = "<option value=''>— file —</option>";
    if (!obj) return;
    fetch("/api/object-files?outdir=&object=" + encodeURIComponent(obj)).then(function (r) { return r.json(); }).then(function (data) {
      (data.files || []).forEach(function (f) {
        const opt = document.createElement("option");
        opt.value = f.path; opt.textContent = f.name; sel.appendChild(opt);
      });
    });
  }
  fetch("/api/workspace?outdir=").then(function (r) { return r.json(); }).then(function (data) {
    ["cmpObj1", "cmpObj2"].forEach(function (id) {
      const sel = document.getElementById(id);
      (data.objects || []).forEach(function (n) {
        const opt = document.createElement("option");
        opt.value = n; opt.textContent = n; sel.appendChild(opt);
      });
    });
  });
  document.getElementById("cmpObj1").addEventListener("change", function () { fill(document.getElementById("cmpFile1"), this.value); });
  document.getElementById("cmpObj2").addEventListener("change", function () { fill(document.getElementById("cmpFile2"), this.value); });
  document.getElementById("cmpBtn").addEventListener("click", function () {
    const a = document.getElementById("cmpFile1").value;
    const b = document.getElementById("cmpFile2").value;
    if (!a || !b) { document.getElementById("cmpStatus").textContent = "Select two simulations."; return; }
    const log = document.getElementById("log");
    log.style.display = "block";
    log.textContent = "Comparing…";
    window.location = "/compare?a=" + encodeURIComponent(a) + "&b=" + encodeURIComponent(b);
  });
})();
</script>
"""

def form_html(values: dict | None = None) -> bytes:
    v = values or {}
    name = html.escape(str(v.get("name", CONFIG["name"])))
    try:
        _lat = float(str(v.get("lat", CONFIG["lat"])).replace(",", "."))
        _lon = float(str(v.get("lon", CONFIG["lon"])).replace(",", "."))
        _stored = location_remark(_lat, _lon, cache_dir=data_root())
    except Exception:
        _stored = ""
    locremark = html.escape(str(v.get("locremark") or _stored))
    lat = html.escape(str(v.get("lat", CONFIG["lat"])))
    lon = html.escape(str(v.get("lon", CONFIG["lon"])))
    obj = html.escape(str(v.get("object") or "GEMINI-60-house-with-garage"))
    startyear = html.escape(str(v.get("startyear", CONFIG["startyear"])))
    endyear = html.escape(str(v.get("endyear", CONFIG["endyear"])))
    loss = html.escape(str(v.get("loss", CONFIG["loss"])))
    outdir = html.escape(str(v.get("outdir", "")))
    db = str(v.get("raddatabase", CONFIG["raddatabase"]))
    horizon_on = v.get("usehorizon", "1" if CONFIG["usehorizon"] else "0") != "0"

    db_opts = []
    for opt in ("auto", "PVGIS-SARAH3", "PVGIS-NSRDB", "PVGIS-ERA5"):
        sel = " selected" if opt == db else ""
        db_opts.append(f'<option value="{opt}"{sel}>{opt}</option>')

    # Defaults only on a fresh form. A selected object keeps its own rows, including none.
    preset = [] if str(v.get("object") or "").strip() else list(CONFIG.get("pv") or [])
    rows = []
    for i in range(1, N_ROWS + 1):
        pvn = sl = az = wp = ""
        if i <= len(preset):
            item = preset[i - 1]
            if len(item) == 4:
                pvn, sl, az, wp = item
            else:
                sl, az, wp = item
                pvn = f"PV {i}"
        if f"slope{i}" in v:
            sl = v.get(f"slope{i}", sl)
            az = v.get(f"az{i}", az)
            wp = v.get(f"wp{i}", wp)
            pvn = v.get(f"pvname{i}", pvn)
        dist = v.get(f"dist{i}", "")
        height = v.get(f"height{i}", "")
        bi = str(v.get(f"bifacial{i}", "")).lower() in {"1", "on", "yes", "true"}
        bi_chk = " checked" if bi else ""
        bipct = v.get(f"bifaciality{i}", "80" if bi else "")
        use_on = str(v.get(f"use{i}", "1")).lower() not in {"0", "off", "no", "false"}
        use_chk = " checked" if use_on else ""
        rows.append(
            f"<tr><td class='n'><button type='button' class='mv' data-dir='-1'>↑</button><button type='button' class='mv' data-dir='1'>↓</button></td>"
            f"<td class='bi'><input type='checkbox' name='use{i}' value='1'{use_chk}></td>"
            f"<td class='pvname'><input name='pvname{i}' value='{html.escape(str(pvn))}' placeholder='e.g. Garden fence SW' data-clearrow='1'></td>"
            f"<td><input name='slope{i}' value='{html.escape(str(sl))}' inputmode='decimal'></td>"
            f"<td><input name='az{i}' value='{html.escape(str(az))}' inputmode='decimal'></td>"
            f"<td><input name='wp{i}' value='{html.escape(str(wp))}' inputmode='decimal'></td>"
            f"<td><input name='dist{i}' value='{html.escape(str(dist))}' inputmode='numeric' placeholder='m'></td>"
            f"<td><input name='height{i}' value='{html.escape(str(height))}' inputmode='numeric' placeholder='m'></td>"
            f"<td class='bi'><input type='checkbox' name='bifacial{i}' value='1' data-row='{i}'{bi_chk}></td>"
            f"<td class='bipct'><input name='bifaciality{i}' value='{html.escape(str(bipct))}' inputmode='decimal' placeholder='80'></td></tr>"
        )

    hz = " checked" if horizon_on else ""
    demand_block = demand_form_html(v)
    body = f"""
<div class="actions" style="margin-bottom:14px">
  <button type="submit" form="pvform">Run — download missing files and write PDF</button>
  <button type="submit" form="pvform" formaction="/set-default" formmethod="post" style="margin-left:12px">Make this configuration default for this object</button>
</div>
<form id="pvform" method="post" action="/run">
  <div class="row">
    <div class="field name"><label>Object (house / plant)</label>
      <input name="object" id="objectName" value="{obj}" placeholder="GEMINI-60-house-with-garage"></div>
    <div class="field name"><label>Existing objects</label>
      <select id="objectPick"><option value="">— select —</option></select></div>
    <div class="field name"><label>Existing locations</label>
      <select id="locationPick"><option value="">— select —</option></select></div>
  </div>
  <p class="hint">
    An <strong>object</strong> is the house (PV layout + demand). A <strong>location</strong> is climate
    (folder <code>Country - place - lat - lon</code>). Pick both: same house near Berlin or in Krems.
    Results go to <code>objects/&lt;object&gt;/Country - place - lat - lon.pdf</code>.
    Weather CSV files stay in the location folder.
  </p>
  <div class="row">
    <div class="field name"><label>Location name (country name - place name)</label>
      <input name="name" value="{name}" required placeholder="Austria - Krems" autocomplete="off"></div>
    <div class="field name"><label>Simulation name</label>
      <input name="simname" value="{html.escape(str(v.get('simname','')))}" placeholder="Test without garden fence"></div>
    <div class="field coord"><label>Latitude</label>
      <input name="lat" value="{lat}" required inputmode="decimal"></div>
    <div class="field coord"><label>Longitude</label>
      <input name="lon" value="{lon}" required inputmode="decimal"></div>
  </div>
  <div id="locmap" style="height:280px;margin:0 0 14px;border:1px solid #d5dbe6"></div>
  <p class="hint">Click the map or type coordinates. The marker is the selected location.
  Price bands use UTC from longitude (15° per hour), not the political time zone.
  <span id="utcNote"></span></p>
  <div style="display:flex;align-items:center;gap:8px;margin:8px 0">
    <span>Search place</span>
    <input id="placeSearch" placeholder="Corfu, Aalborg, Cairo…" style="flex:1;max-width:400px">
    <button type="button" id="placeGo">Find</button>
    <span id="placeHit" class="hint"></span>
  </div>
  <div class="field" style="max-width:720px">
    <label>Location remark</label>
    <textarea name="locremark" maxlength="2000" rows="4" style="width:100%;min-height:5.5em">{locremark}</textarea>
    <p class="hint">Up to 2000 characters. Stored in locations/.pvgis_country_cache.json. Cleared when the pin or the coordinates change.</p>
  </div>
  <div class="row">
    <div class="field small"><label>Start year</label>
      <input name="startyear" value="{startyear}"></div>
    <div class="field small"><label>End year</label>
      <input name="endyear" value="{endyear}"></div>
    <div class="field small"><label>System loss %</label>
      <input name="loss" value="{loss}"></div>
    <div class="field"><label>Radiation database</label>
      <select name="raddatabase">{''.join(db_opts)}</select></div>
    <div class="field" style="justify-content:flex-end;padding-bottom:4px;">
      <label><input type="checkbox" name="usehorizon" value="1"{hz}> terrain horizon</label>
    </div>
  </div>
  <div class="row">
    <div class="field wide"><label>Output parent folder (empty = current directory)</label>
      <input name="outdir" value="{outdir}" placeholder="{html.escape(str(Path.cwd()))}"></div>
  </div>

  <p class="hint">
    Up to 25 arrays. Empty rows are ignored.
    Azimuth: 0 = south, 90 = west, −90 = east, 180 = north. Southwest = 225, opposite face = 45.
    Wall: integer metres, empty = no extra shade. Width is 10× distance.
    Cache suffix is <code>_s80</code> = 100 × height/distance (d5h4 and d10h8 are the same file).
    Bifacial: fills the next row as face 2 (azimuth + 180°, junction-box / rear side).
    Face 2 Wp = front × bifaciality % (default 80). Both yields are added; nameplate Wp is counted once.
    Put the front (no junction box) toward the open field; face 2 toward the house.
  </p>
  <p class="hint">Use ↑ ↓ to change the order. Clearing a name and leaving the field clears that row.</p>
  <table id="pvtable">
    <thead><tr><th>#</th><th>Use</th><th>Name</th><th>Slope °</th><th>Azimuth °</th><th>Wp</th><th>Wall dist. m</th><th>Wall height m</th><th>Bifacial</th><th>Bi %</th></tr></thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
<script>
(function () {{
  const table = document.getElementById("pvtable");
  if (!table) return;
  function fields(row) {{
    return Array.prototype.slice.call(row.querySelectorAll("input"));
  }}
  function swap(a, b) {{
    const fa = fields(a), fb = fields(b);
    fa.forEach(function (ia, k) {{
      const ib = fb[k];
      if (!ib) return;
      if (ia.type === "checkbox") {{
        const c = ia.checked; ia.checked = ib.checked; ib.checked = c;
      }} else {{
        const v = ia.value; ia.value = ib.value; ib.value = v;
      }}
    }});
  }}
  table.addEventListener("click", function (e) {{
    const btn = e.target.closest("button.mv");
    if (!btn) return;
    const row = btn.closest("tr");
    const dir = Number(btn.getAttribute("data-dir"));
    const other = dir < 0 ? row.previousElementSibling : row.nextElementSibling;
    if (!other) return;
    swap(row, other);
  }});
  table.querySelectorAll("input[data-clearrow]").forEach(function (inp) {{
    inp.addEventListener("change", function () {{
      if (inp.value.trim() !== "") return;
      const row = inp.closest("tr");
      row.querySelectorAll("input").forEach(function (other) {{
        if (other === inp) return;
        if (other.type === "checkbox") other.checked = false;
        else other.value = "";
      }});
    }});
  }});
}})();
</script>
  {demand_block}
  <div class="actions">
    <button type="submit">Run — download missing files and write PDF</button>
  </div>
</form>
<script>
(function () {{
  const form = document.getElementById("pvform");
  if (form) form.addEventListener("submit", function (ev) {{
    const name = (document.querySelector("[name=name]").value || "").trim();
    if (!name) {{
      ev.preventDefault();
      alert("Enter the location name first (country name - place name).");
    }}
  }});
  window.loadRemark = async function () {{
    const lat = document.querySelector("[name=lat]").value;
    const lon = document.querySelector("[name=lon]").value;
    const box = document.querySelector("[name=locremark]");
    if (!box) return;
    try {{
      const r = await fetch("/api/location-remark?lat=" + encodeURIComponent(lat) + "&lon=" + encodeURIComponent(lon));
      const data = await r.json();
      if (data.remark) box.value = data.remark;
    }} catch (err) {{}}
  }};
  function oppositeAz(az) {{
    let a = parseFloat(String(az).replace(",", ".")) + 180;
    if (isNaN(a)) return "";
    while (a > 180) a -= 360;
    while (a <= -180) a += 360;
    return Number.isInteger(a) ? String(a) : String(Math.round(a * 1000) / 1000);
  }}
  function fillFace2(i) {{
    const n = i + 1;
    const box = document.querySelector("[name=bifacial" + i + "]");
    if (!box || !box.checked) return;
    const nameEl = document.querySelector("[name=pvname" + i + "]");
    const slEl = document.querySelector("[name=slope" + i + "]");
    const azEl = document.querySelector("[name=az" + i + "]");
    const wpEl = document.querySelector("[name=wp" + i + "]");
    const pctEl = document.querySelector("[name=bifaciality" + i + "]");
    const nName = document.querySelector("[name=pvname" + n + "]");
    const nSl = document.querySelector("[name=slope" + n + "]");
    const nAz = document.querySelector("[name=az" + n + "]");
    const nWp = document.querySelector("[name=wp" + n + "]");
    const nBi = document.querySelector("[name=bifacial" + n + "]");
    if (!nName || !slEl || !azEl || !wpEl) return;
    let pct = parseFloat(String((pctEl && pctEl.value) || "80").replace(",", "."));
    if (isNaN(pct) || pct <= 0) pct = 80;
    if (pctEl && !pctEl.value) pctEl.value = "80";
    const wp = parseFloat(String(wpEl.value).replace(",", "."));
    const base = (nameEl.value || "").trim() || ("PV " + i);
    nName.value = / face2$/i.test(base) ? base : base + " face2";
    nSl.value = slEl.value;
    nAz.value = oppositeAz(azEl.value);
    if (!isNaN(wp)) nWp.value = String(Math.round(wp * pct / 100));
    if (nBi) nBi.checked = false;
  }}
  document.querySelectorAll("input[name^='bifacial']").forEach(function (box) {{
    box.addEventListener("change", function () {{
      const i = parseInt(box.getAttribute("data-row"), 10);
      if (box.checked) fillFace2(i);
    }});
  }});
  document.querySelectorAll("input[name^='wp'], input[name^='bifaciality'], input[name^='az'], input[name^='slope']").forEach(function (el) {{
    el.addEventListener("change", function () {{
      const m = el.name.match(/(\\d+)$/);
      if (m) fillFace2(parseInt(m[1], 10));
    }});
  }});
  function outdirQ() {{
    return encodeURIComponent(document.querySelector("[name=outdir]").value || "");
  }}
  async function refreshPicks() {{
    try {{
      const r = await fetch("/api/workspace?outdir=" + outdirQ());
      const data = await r.json();
      const oSel = document.getElementById("objectPick");
      const lSel = document.getElementById("locationPick");
      if (oSel) {{
        const cur = document.getElementById("objectName").value;
        oSel.innerHTML = "<option value=''>— select —</option>";
        (data.objects || []).forEach(function (n) {{
          const opt = document.createElement("option");
          opt.value = n; opt.textContent = n;
          if (n === cur) opt.selected = true;
          oSel.appendChild(opt);
        }});
      }}
      if (lSel) {{
        lSel.innerHTML = "<option value=''>— select —</option>";
        (data.locations || []).forEach(function (loc) {{
          const opt = document.createElement("option");
          opt.value = JSON.stringify(loc);
          opt.textContent = loc.folder;
          lSel.appendChild(opt);
        }});
      }}
    }} catch (err) {{}}
  }}
  const oPick = document.getElementById("objectPick");
  if (oPick) oPick.addEventListener("change", function () {{
    if (!oPick.value) return;
    document.getElementById("objectName").value = oPick.value;
    window.location = "/load-object?name=" + encodeURIComponent(oPick.value) + "&outdir=" + outdirQ();
  }});
  const lPick = document.getElementById("locationPick");
  if (lPick) lPick.addEventListener("change", function () {{
    if (!lPick.value) return;
    try {{
      const loc = JSON.parse(lPick.value);
      if (loc.fullname) document.querySelector("[name=name]").value = loc.fullname;
      else if (loc.name) document.querySelector("[name=name]").value = loc.name;
      if (loc.lat != null) document.querySelector("[name=lat]").value = loc.lat;
      if (loc.lon != null) document.querySelector("[name=lon]").value = loc.lon;
      if (window._moveMap) window._moveMap(loc.lat, loc.lon);
      loadRemark();
    }} catch (err) {{}}
  }});
  refreshPicks();
  (function () {{
    var box = document.getElementById("locmap");
    if (!box || typeof L === "undefined") return;
    var latEl = document.querySelector("[name=lat]");
    var lonEl = document.querySelector("[name=lon]");
    var lat = parseFloat(String(latEl.value).replace(",", ".")) || 48.4;
    var lon = parseFloat(String(lonEl.value).replace(",", ".")) || 15.989;
    var map = L.map(box).setView([lat, lon], 8);
    L.tileLayer("https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
      maxZoom: 18, attribution: "&copy; OpenStreetMap"
    }}).addTo(map);
    var marker = L.marker([lat, lon], {{draggable: true}}).addTo(map);
    function clearPlace() {{
      var nameEl = document.querySelector("[name=name]");
      var remarkEl = document.querySelector("[name=locremark]");
      if (nameEl) nameEl.value = "";
      if (remarkEl) remarkEl.value = "";
      setTimeout(function () {{
        if (nameEl) nameEl.value = "";
        if (remarkEl) remarkEl.value = "";
      }}, 50);
    }}
    function setFrom(lat, lon) {{
      latEl.value = String(Math.round(lat * 1000) / 1000);
      lonEl.value = String(Math.round(lon * 1000) / 1000);
      marker.setLatLng([lat, lon]);
      clearPlace();
      var utc = Math.round(lon / 15);
      var note = document.getElementById("utcNote");
      if (note) note.textContent = "  Using UTC" + (utc >= 0 ? "+" : "") + utc + " from longitude.";
    }}
    map.on("click", function (e) {{ setFrom(e.latlng.lat, e.latlng.lng); }});
    marker.on("dragend", function () {{
      var p = marker.getLatLng();
      setFrom(p.lat, p.lng);
    }});
    function readFields() {{
      var a = parseFloat(String(latEl.value).replace(",", "."));
      var b = parseFloat(String(lonEl.value).replace(",", "."));
      if (isNaN(a) || isNaN(b)) return;
      marker.setLatLng([a, b]);
      map.panTo([a, b]);
      clearPlace();
    }}
    latEl.addEventListener("change", readFields);
    lonEl.addEventListener("change", readFields);
    var searchEl = document.getElementById("placeSearch");
    var goBtn = document.getElementById("placeGo");
    var hitEl = document.getElementById("placeHit");
    async function findPlace() {{
      var q = (searchEl.value || "").trim();
      if (!q) return;
      hitEl.textContent = "Searching…";
      try {{
        var r = await fetch("https://nominatim.openstreetmap.org/search?format=json&accept-language=en&q=" + encodeURIComponent(q));
        var data = await r.json();
        if (!data.length) {{ hitEl.textContent = "Not found."; return; }}
        var hit = data[0];
        setFrom(parseFloat(hit.lat), parseFloat(hit.lon));
        map.setView([hit.lat, hit.lon], 10);
        hitEl.textContent = hit.display_name;
      }} catch (err) {{
        hitEl.textContent = "Search failed.";
      }}
    }}
    if (goBtn) goBtn.addEventListener("click", findPlace);
    if (searchEl) searchEl.addEventListener("keydown", function (e) {{ if (e.key === "Enter") {{ e.preventDefault(); findPlace(); }} }});
    window._moveMap = function (lat, lon) {{
      var a = parseFloat(lat);
      var b = parseFloat(lon);
      if (isNaN(a) || isNaN(b)) return;
      marker.setLatLng([a, b]);
      map.setView([a, b], 8);
    }};
    latEl.addEventListener("input", clearPlace);
    lonEl.addEventListener("input", clearPlace);
  }})();
  const box = document.getElementById("browser");
  const list = document.getElementById("browserList");
  const status = document.getElementById("browserStatus");
  const openIndex = async function () {{
    box.hidden = false;
    status.textContent = "Reading folders…";
    list.innerHTML = "";
    const outdir = encodeURIComponent(document.querySelector("[name=outdir]").value || "");
    try {{
      const r = await fetch("/api/folders?outdir=" + outdir);
      const data = await r.json();
      if (data.error) {{ status.textContent = data.error; return; }}
      status.textContent = "Parent: " + data.parent + "  —  click a PDF to load its parameters into the form.";
      if (!data.folders.length) {{
        list.innerHTML = "<p class='hint'>No location folders with files found.</p>";
        return;
      }}
      data.folders.forEach(function (f) {{
        const div = document.createElement("div");
        div.className = "folder";
        const title = document.createElement("div");
        title.className = "fname";
        title.textContent = f.name;
        div.appendChild(title);
        const ul = document.createElement("ul");
        (f.files || []).forEach(function (file) {{
          const li = document.createElement("li");
          if (file.load) {{
            const a = document.createElement("a");
            a.className = "pdf";
            a.href = file.load;
            a.textContent = file.name;
            li.appendChild(a);
            li.appendChild(document.createTextNode("  load parameters"));
          }} else {{
            const span = document.createElement("span");
            span.className = "other";
            span.textContent = file.name;
            li.appendChild(span);
          }}
          ul.appendChild(li);
        }});
        div.appendChild(ul);
        list.appendChild(div);
      }});
    }} catch (err) {{
      status.textContent = String(err);
    }}
  }};
  document.querySelectorAll(".browseBtn").forEach(function (b) {{
    b.addEventListener("click", openIndex);
  }});
  async function fillCmp(sel, obj) {{
    sel.innerHTML = "<option value=''>— file —</option>";
    const note = document.getElementById("cmpStatus");
    if (!obj) {{ if (note) note.textContent = ""; return; }}
    try {{
      const r = await fetch("/api/object-files?outdir=" + outdirQ() + "&object=" + encodeURIComponent(obj));
      const data = await r.json();
      if (data.error) {{ if (note) note.textContent = data.error; return; }}
      (data.files || []).forEach(function (f) {{
        const opt = document.createElement("option");
        opt.value = f.path;
        opt.textContent = f.name;
        sel.appendChild(opt);
      }});
      if (note) note.textContent = (data.files || []).length
        ? ""
        : "No PDF/JSON pair in objects/" + obj + ". Run a simulation first.";
    }} catch (err) {{
      if (note) note.textContent = "Could not list simulations: " + err;
    }}
  }}
  function fillObjects(ids) {{
    fetch("/api/workspace?outdir=" + outdirQ()).then(function (r) {{ return r.json(); }}).then(function (data) {{
      ids.forEach(function (id) {{
        const sel = document.getElementById(id);
        if (!sel) return;
        const cur = sel.value;
        sel.innerHTML = "<option value=''>— object —</option>";
        (data.objects || []).forEach(function (n) {{
          const opt = document.createElement("option");
          opt.value = n; opt.textContent = n;
          if (n === cur) opt.selected = true;
          sel.appendChild(opt);
        }});
      }});
    }}).catch(function () {{}});
  }}
  fillObjects(["cmpObj1", "cmpObj2"]);
  const c1 = document.getElementById("cmpObj1");
  const c2 = document.getElementById("cmpObj2");
  if (c1) c1.addEventListener("change", function () {{ fillCmp(document.getElementById("cmpFile1"), c1.value); }});
  if (c2) c2.addEventListener("change", function () {{ fillCmp(document.getElementById("cmpFile2"), c2.value); }});
  if (c1 && c1.value) fillCmp(document.getElementById("cmpFile1"), c1.value);
  if (c2 && c2.value) fillCmp(document.getElementById("cmpFile2"), c2.value);
  const cmpBtn = document.getElementById("cmpBtn");
  if (cmpBtn) cmpBtn.addEventListener("click", function () {{
    const a = document.getElementById("cmpFile1").value;
    const b = document.getElementById("cmpFile2").value;
    if (!a || !b) {{ alert("Select two simulation files."); return; }}
    window.location = "/compare?a=" + encodeURIComponent(a) + "&b=" + encodeURIComponent(b) + "&outdir=" + outdirQ();
  }});
}})();
</script>
"""
    return page_shell("PEGE energy sim", body)


def first(fields: dict[str, list[str]], key: str, default: str = "") -> str:
    vals = fields.get(key)
    return vals[0].strip() if vals else default


def parse_job(fields: dict[str, list[str]]) -> dict:
    name = first(fields, "name")
    if not name:
        raise ValueError("Enter the location name first (country name - place name).")
    try:
        lat = float(first(fields, "lat").replace(",", "."))
        lon = float(first(fields, "lon").replace(",", "."))
    except ValueError as exc:
        raise ValueError("Latitude and longitude must be numbers") from exc
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Latitude must be −90…90 and longitude −180…180")

    pv = []
    for i in range(1, N_ROWS + 1):
        sl = first(fields, f"slope{i}")
        az = first(fields, f"az{i}")
        wp = first(fields, f"wp{i}")
        label = first(fields, f"pvname{i}")
        if not sl and not az and not wp and not label:
            continue
        if not sl or not az or not wp:
            raise ValueError(
                f"{label or f'PV {i}'}: fill slope, azimuth and Wp, or leave the whole row empty"
            )
        try:
            slope = float(sl.replace(",", "."))
            azimuth = float(az.replace(",", "."))
            watts = float(wp.replace(",", "."))
        except ValueError as exc:
            raise ValueError(f"{label or f'PV {i}'}: slope, azimuth and Wp must be numbers") from exc
        if watts <= 0:
            raise ValueError(f"{label or f'PV {i}'}: Wp must be positive")
        dist_s = first(fields, f"dist{i}")
        height_s = first(fields, f"height{i}")
        wall_d = wall_h = None
        if dist_s or height_s:
            if not dist_s or not height_s:
                raise ValueError(f"{label or f'PV {i}'}: fill both wall distance and height, or leave both empty")
            try:
                wall_d = int(float(dist_s.replace(",", ".")))
                wall_h = int(float(height_s.replace(",", ".")))
            except ValueError as exc:
                raise ValueError(f"{label or f'PV {i}'}: wall distance and height must be integers") from exc
            if wall_d != float(dist_s.replace(",", ".")) or wall_h != float(height_s.replace(",", ".")):
                raise ValueError(f"{label or f'PV {i}'}: wall parameters are integers only")
            if wall_d <= 0 or wall_h < 0:
                raise ValueError(f"{label or f'PV {i}'}: wall distance > 0 and height ≥ 0")
        bifacial = first(fields, f"bifacial{i}").lower() in {"1", "on", "yes", "true"}
        bipct_s = first(fields, f"bifaciality{i}")
        bifaciality = None
        if bipct_s:
            try:
                bifaciality = float(bipct_s.replace(",", "."))
            except ValueError as exc:
                raise ValueError(f"{label or f'PV {i}'}: bifaciality % must be a number") from exc
            if bifaciality > 1.5:
                bifaciality = bifaciality / 100.0
            if not (0.05 <= bifaciality <= 1.0):
                raise ValueError(f"{label or f'PV {i}'}: bifaciality must be 5–100 %")
        elif bifacial:
            bifaciality = 0.80
        enabled = first(fields, f"use{i}").lower() in {"1", "on", "yes", "true"}
        pv.append((label or f"PV {i}", slope, azimuth, watts, wall_d, wall_h, bifacial, bifaciality, enabled))
    if not pv:
        log_note = "No PV arrays. Apartment or grid-only run."

    startyear = int(first(fields, "startyear", str(CONFIG["startyear"])) or CONFIG["startyear"])
    endyear = int(first(fields, "endyear", str(CONFIG["endyear"])) or CONFIG["endyear"])
    if startyear > endyear:
        raise ValueError("Start year must be ≤ end year")
    loss = float(first(fields, "loss", str(CONFIG["loss"])) or CONFIG["loss"])
    raddatabase = first(fields, "raddatabase", "auto") or "auto"
    usehorizon = first(fields, "usehorizon", "") in {"1", "on", "yes"}
    export0915 = False
    outdir_s = first(fields, "outdir")
    outdir = Path(outdir_s).expanduser() if outdir_s else Path.cwd()
    outdir.mkdir(parents=True, exist_ok=True)
    object_name = sanitize_name(first(fields, "object") or "GEMINI-60-house-with-garage")
    demand = parse_demand_fields(fields)
    demand["export_0915"] = export0915
    pv = [item for item in pv if item[-1]]
    return {
        "name": name,
        "simname": first(fields, "simname"),
        "lat": lat,
        "lon": lon,
        "object": object_name,
        "pv": pv,
        "startyear": startyear,
        "endyear": endyear,
        "loss": loss,
        "raddatabase": raddatabase,
        "usehorizon": usehorizon,
        "export0915": export0915,
        "outdir": outdir,
        "demand": demand,
    }


def job_to_form_values(job: dict) -> dict[str, str]:
    vals = {
        "name": str(job["name"]),
        "simname": str(job.get("simname") or ""),
        "lat": str(job["lat"]),
        "lon": str(job["lon"]),
        "startyear": str(job["startyear"]),
        "endyear": str(job["endyear"]),
        "loss": str(job["loss"]),
        "raddatabase": str(job["raddatabase"]),
        "usehorizon": "1" if job["usehorizon"] else "0",
        "export0915": "1" if job.get("export0915") else "0",
        "outdir": str(job["outdir"]),
        "object": str(job.get("object") or ""),
    }
    for i, item in enumerate(job["pv"], 1):
        bifacial = False
        bifaciality = None
        enabled = True
        if len(item) >= 9:
            label, sl, az, wp, dist, height, bifacial, bifaciality, enabled = item[:9]
        elif len(item) == 8:
            label, sl, az, wp, dist, height, bifacial, bifaciality = item
        elif len(item) == 7:
            label, sl, az, wp, dist, height, bifacial = item
        elif len(item) == 6:
            label, sl, az, wp, dist, height = item
        elif len(item) == 4:
            label, sl, az, wp = item
            dist = height = None
        else:
            sl, az, wp = item[:3]
            label, dist, height = f"PV {i}", None, None
        vals[f"pvname{i}"] = str(label)
        vals[f"slope{i}"] = str(sl)
        vals[f"az{i}"] = str(az)
        vals[f"wp{i}"] = str(wp)
        vals[f"dist{i}"] = "" if dist is None else str(int(dist))
        vals[f"height{i}"] = "" if height is None else str(int(height))
        vals[f"bifacial{i}"] = "1" if bifacial else "0"
        vals[f"use{i}"] = "1" if enabled else "0"
        if bifaciality is not None:
            pct = bifaciality * 100 if float(bifaciality) <= 1.5 else float(bifaciality)
            vals[f"bifaciality{i}"] = str(int(pct) if abs(pct - round(pct)) < 1e-9 else pct)
    vals.update(demand_to_form_values(job.get("demand") or {}))
    return vals


def form_query(job: dict) -> str:
    return urlencode(job_to_form_values(job), doseq=False)


def form_values_from_qs(qs: dict[str, list[str]]) -> dict[str, str]:
    return {k: (v[0] if v else "") for k, v in qs.items()}


def allowed_roots(extra: Path | None = None) -> list[Path]:
    roots = [Path.cwd().resolve()]
    if LAST_FORM.get("outdir"):
        try:
            roots.append(Path(LAST_FORM["outdir"]).expanduser().resolve())
        except Exception:
            pass
    if extra:
        try:
            roots.append(extra.expanduser().resolve())
        except Exception:
            pass
    out = []
    seen = set()
    for r in roots:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def is_allowed_path(path: Path, extra: Path | None = None) -> bool:
    try:
        path = path.resolve()
    except Exception:
        return False
    for root in allowed_roots(extra):
        if path == root or root in path.parents:
            return True
    return False


def parse_location_folder_name(folder_name: str) -> dict | None:
    parts = [p.strip() for p in folder_name.split(" - ")]
    if len(parts) < 3:
        return None
    try:
        lat = float(parts[-2].replace(",", "."))
        lon = float(parts[-1].replace(",", "."))
    except ValueError:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    full = " - ".join(parts[:-2]) if len(parts) >= 4 else parts[0]
    if len(parts) >= 4:
        name = " - ".join(parts[1:-2])
    else:
        name = parts[0]
    return {"folder": folder_name, "name": name, "fullname": full, "lat": lat, "lon": lon}


def list_location_folders(parent: Path) -> list[dict]:
    parent = parent.expanduser().resolve()
    folders = []
    candidates = []
    if parent.is_dir():
        candidates.append(parent)
        extra = [parent]
        loc = parent / "locations"
        if loc.is_dir():
            extra.append(loc)
        for root in extra:
            for child in sorted(root.iterdir()):
                if child.is_dir() and child.name.lower() not in {"objects", "compares", "locations"}:
                    candidates.append(child)
    for folder in candidates:
        files = []
        try:
            entries = sorted(folder.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for p in entries:
            if not p.is_file():
                continue
            rec = {"name": p.name}
            if p.suffix.lower() in {".pdf", ".json"}:
                rec["load"] = "/load-pdf?path=" + quote(str(p))
            files.append(rec)
        if folder.name.lower() in {"objects", "compares", "locations"}:
            continue
        if files and folder != parent:
            folders.append({"name": folder.name, "files": files})
        elif files and folder == parent:
            folders.insert(0, {"name": folder.name + "  (parent)", "files": files})
    return folders


def sidecar_to_form_values(data: dict) -> dict[str, str] | None:
    if data.get("form_values"):
        return {str(k): "" if v is None else str(v) for k, v in data["form_values"].items()}
    if data.get("arrays") and data.get("name"):
        job = {
            "name": data["name"],
            "lat": data.get("lat", ""),
            "lon": data.get("lon", ""),
            "startyear": data.get("startyear", CONFIG["startyear"]),
            "endyear": data.get("endyear", CONFIG["endyear"]),
            "loss": data.get("loss", CONFIG["loss"]),
            "raddatabase": data.get("raddatabase", "auto"),
            "usehorizon": data.get("usehorizon", True),
            "outdir": data.get("outdir", ""),
            "pv": [],
        }
        for a in data["arrays"]:
            job["pv"].append((
                a.get("label", ""),
                a.get("slope"),
                a.get("azimuth"),
                a.get("wp"),
                a.get("wall_d"),
                a.get("wall_h"),
            ))
        if job["outdir"] == "":
            job["outdir"] = Path.cwd()
        return job_to_form_values(job)
    return None


def query_from_reopen_url(url: str) -> dict[str, str] | None:
    if not url or "/form?" not in url:
        return None
    q = url.split("/form?", 1)[1].strip()
    q = q.split()[0].rstrip(".,;)")
    return form_values_from_qs(parse_qs(q, keep_blank_values=True))


def _vals_from_json_file(path: Path) -> dict[str, str] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    vals = sidecar_to_form_values(data)
    if vals:
        return vals
    if data.get("reopen_url"):
        return query_from_reopen_url(str(data["reopen_url"]))
    return None


def _vals_from_pdf_bytes(pdf_path: Path) -> dict[str, str] | None:
    raw = pdf_path.read_bytes()
    # URLs are often stored as literal ASCII in the PDF stream / annotations.
    text = raw.decode("latin-1", errors="ignore")
    for pattern in (
        r"https?://[^\s<>()\\]+/form\?[^\s<>()\\]+",
        r"/form\?[A-Za-z0-9_%.=&+\-]+",
    ):
        for blob in re.findall(pattern, text):
            blob = blob.replace("\\", "")
            if "/form?" not in blob:
                continue
            vals = query_from_reopen_url("http://local" + blob[blob.index("/form?"):])
            if vals and ("name" in vals or "lat" in vals):
                return vals
    return None


def extract_form_from_pdf(pdf_path: Path) -> dict[str, str]:
    if pdf_path.suffix.lower() == ".json":
        vals = _vals_from_json_file(pdf_path)
        if vals:
            return vals
        raise ValueError("That JSON file has no form parameters.")

    sidecar = pdf_path.with_suffix(".json")
    if sidecar.is_file():
        vals = _vals_from_json_file(sidecar)
        if vals:
            return vals

    vals = _vals_from_pdf_bytes(pdf_path)
    if vals:
        return vals

    try:
        from pypdf import PdfReader  # optional
    except ImportError:
        PdfReader = None
    if PdfReader is not None:
        try:
            reader = PdfReader(str(pdf_path))
            meta = reader.metadata
            for attr in ("subject", "keywords"):
                val = getattr(meta, attr, None) if meta else None
                if isinstance(val, str):
                    found = query_from_reopen_url(val)
                    if found:
                        return found
            text = ""
            for page in reader.pages[:2]:
                text += page.extract_text() or ""
            match = re.search(r"/form\?[^\s]+", text)
            if match:
                found = query_from_reopen_url("http://local" + match.group(0)[match.group(0).index("/form?"):])
                if found:
                    return found
        except Exception:
            pass

    raise ValueError(
        "No parameters in this PDF. Use a report from the current program "
        "(it writes a matching .json next to the PDF), or click that .json file."
    )



LOGO_URL = "https://pege.org/pic/pege-trans.png"
FAVICON_URL = "https://www.pege.org/favicon.ico"


def _fetch_bytes(url: str, timeout: float = 8.0) -> bytes:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "PEGE-energy-sim"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def load_logo() -> tuple[bytes, str]:
    """Logo is taken from pege.org, then kept beside the server as a fallback."""
    cache = HERE / "pege-logo.png"
    try:
        data = _fetch_bytes(LOGO_URL)
        if data[:8] == b"\x89PNG\r\n\x1a\n" or data[:4] == b"\x89PNG":
            cache.write_bytes(data)
            return data, "image/png"
        if data:
            cache.write_bytes(data)
            return data, "image/png"
    except Exception:
        pass
    if cache.is_file():
        return cache.read_bytes(), "image/png"
    return b"", "image/png"


def load_favicon() -> bytes:
    cache = HERE / "favicon.ico"
    try:
        data = _fetch_bytes(FAVICON_URL)
        if data and (data[:4] == b"\x00\x00\x01\x00" or data[:8] == b"\x89PNG\r\n\x1a\n" or len(data) > 32):
            cache.write_bytes(data)
            return data
    except Exception:
        pass
    if cache.is_file():
        return cache.read_bytes()
    return b""


class Handler(BaseHTTPRequestHandler):
    server_version = "PVGISServer/1.0"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(f"{datetime.now():%Y-%m-%d %H:%M}  {self.address_string()}  {fmt % args}\n")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/pege-logo.png", "/favicon.png"):
            data, ctype = load_logo()
            if not data:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path == "/favicon.ico":
            data = load_favicon()
            if not data:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/x-icon")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path in ("/objects", "/community", "/compare-communities", "/investment"):
            self._send(200, page_shell("Soon", "<p>Will be implemented soon.</p><p><a href='/form'>Back to main</a></p>"))
            return
        if parsed.path == "/files":
            self._send(200, page_shell("Files", FILES_HTML))
            return
        if parsed.path == "/api/files-index":
            root = data_root()
            def pairs(folder):
                pdfs = {p.stem: p for p in folder.glob("*.pdf")}
                jsons = {p.stem: p for p in folder.glob("*.json")}
                names = sorted(set(pdfs) | set(jsons))
                out = []
                for name in names:
                    pdf = pdfs.get(name)
                    js = jsons.get(name)
                    definition = name == folder.name and not pdf
                    if not definition and not (pdf and js):
                        continue
                    version = "0.0"
                    if js and js.is_file():
                        try:
                            meta = json.loads(js.read_text(encoding="utf-8"))
                            version = str(meta.get("software_version") or "0.0")
                        except Exception:
                            pass
                    out.append({
                        "name": name + ("  (object)" if definition else ""),
                        "href": "" if definition else "/file?path=" + quote(str(pdf or js)),
                        "edit": "/load?json=" + quote(str(js)) if js else "",
                        "version": version,
                        "json": str(js) if js else "",
                    })
                return out
            objects = []
            odir = root / "objects"
            if odir.is_dir():
                for folder in sorted(p for p in odir.iterdir() if p.is_dir()):
                    objects.append({"name": folder.name, "files": pairs(folder)})
            compares = []
            cdir = root / "compares"
            if cdir.is_dir():
                compares.append({"name": "compares", "files": [
                    {"name": p.stem, "href": "/file?path=" + quote(str(p))}
                    for p in sorted(cdir.glob("*.pdf"))
                ]})
            locations = []
            ldir = root / "locations"
            if ldir.is_dir():
                for folder in sorted(p for p in ldir.iterdir() if p.is_dir()):
                    locations.append({"name": folder.name, "files": [
                        {"name": p.name, "href": "/file?path=" + quote(str(p))}
                        for p in sorted(folder.iterdir()) if p.is_file()
                    ]})
            investment = []
            idir = root / "investment-models"
            if idir.is_dir():
                investment.append({"name": "investment-models", "files": pairs(idir)})
            known = {odir.resolve(), cdir.resolve(), ldir.resolve(), idir.resolve()}
            other = []
            if root.is_dir():
                root_files = []
                for item in sorted(root.iterdir()):
                    if item.resolve() in known or item.name.startswith("."):
                        continue
                    if item.is_dir():
                        other.append({"name": item.name, "files": [
                            {"name": p.name, "href": "/file?path=" + quote(str(p))}
                            for p in sorted(item.rglob("*")) if p.is_file()
                        ]})
                    else:
                        root_files.append({"name": item.name, "href": "/file?path=" + quote(str(item))})
                if root_files:
                    other.insert(0, {"name": "parent folder", "files": root_files})
            self._send_json(200, {
                "objects": objects, "compares": compares,
                "locations": locations, "investment": investment, "other": other,
            })
            return
        if parsed.path == "/file":
            raw = (parse_qs(parsed.query).get("path") or [""])[0]
            path = Path(unquote(raw)).expanduser().resolve()
            if not path.is_file() or not is_allowed_path(path, path.parent):
                self._send(404, page_shell("File", "<p>Not found.</p>"))
                return
            data = path.read_bytes()
            ctype = "application/pdf" if path.suffix == ".pdf" else "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path == "/investment/baseline":
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            fields = parse_qs(raw, keep_blank_values=True)
            base = {str(k): v[0] if isinstance(v, list) else str(v) for k, v in fields.items()}
            folder = data_root() / "investment-models"
            folder.mkdir(parents=True, exist_ok=True)
            # Prefer the newest project JSON that is not named baseline
            candidates = [p for p in folder.glob("*.json") if p.name != "baseline.json"]
            candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            if candidates:
                path = candidates[0]
                existing = {}
                try:
                    existing = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    existing = {}
            else:
                stamp = datetime.now().strftime("%Y-%m-%d %H%M")
                path = folder / f"{stamp} investment.json"
                existing = {}
            existing.update(base)
            path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
            path.touch()
            self._send(200, page_shell("Investment model", BASELINE_HTML + f"<p>Saved into {path}<br>money={existing.get('base_money','')}</p>"))
            return
        if parsed.path == "/investment":
            self._send(200, page_shell("Investment model", INVEST_HTML))
            return
        if parsed.path == "/investment/baseline":
            self._send(200, page_shell("Investment model", BASELINE_HTML))
            return
        if parsed.path.startswith("/investment/"):
            self._send(200, page_shell("Investment model", INVEST_HTML))
            return
        if parsed.path == "/compare-houses":
            self._send(200, page_shell("Compare houses", COMPARE_HTML))
            return
        if parsed.path == "/open":
            import base64, zlib
            raw = (parse_qs(parsed.query).get("d") or [""])[0]
            try:
                packed = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
                text = zlib.decompress(packed).decode("utf-8")
                vals = form_values_from_qs(parse_qs(text, keep_blank_values=True))
            except Exception:
                self._send(400, page_shell("Open", "<p>That link could not be read.</p><p><a href='/form'>Back to main</a></p>"))
                return
            self._send(200, form_html(vals))
            return
        if parsed.path == "/load":
            raw = (parse_qs(parsed.query).get("json") or [""])[0]
            path = Path(unquote(raw)).expanduser()
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                self._send(404, page_shell("Load", "<p>That simulation could not be read.</p><p><a href='/form'>Back to main</a></p>"))
                return
            vals = sidecar_to_form_values(data) or {}
            if data.get("demand"):
                vals.update(demand_to_form_values(data["demand"]))
            vals = {str(k): "" if v is None else str(v) for k, v in vals.items()}
            self._send(200, form_html(vals))
            return
        if parsed.path == "/version-test":
            page = page_shell("Version", form_html(LAST_FORM) if False else "")
            # show the form and reveal the box
            self._send(200, page_shell("Version", "<script>document.getElementById('verbox').style.display='block';</script>"))
            return
        if parsed.path == "/set-default":
            self._send(200, page_shell("Default", "<p>Use the button on the form.</p>"))
            return
        if parsed.path == "/about":
            logo = '<p><a href="https://www.pege.org/" target="_blank"><img src="/pege-logo.png" alt="PEGE" width="420" height="120" style="width:420px;height:120px;background:#fff"></a></p>'
            readme = HERE / "README.md"
            body = logo
            if readme.is_file():
                lines = readme.read_text(encoding="utf-8").splitlines()
                html_lines = []
                for line in lines:
                    s = line.strip()
                    if s.startswith("##"):
                        html_lines.append(f"<h2 style='margin:0.4em 0 0.15em;font-size:1.15em'>{html.escape(s[2:].strip())}</h2>")
                    elif s.startswith("<a href="):
                        if 'target=' not in s:
                            s = s.replace("<a ", '<a target="_blank" ')
                        html_lines.append(f"<p style='margin:0.2em 0'>{s}</p>")
                    elif s.startswith("https://"):
                        html_lines.append(f'<p style="margin:0.2em 0"><a href="{html.escape(s)}" target="_blank">{html.escape(s)}</a></p>')
                    elif s:
                        html_lines.append(f"<p style='margin:0.2em 0'>{html.escape(s)}</p>")
                    else:
                        html_lines.append("<br>")
                body += "\n".join(html_lines)
            else:
                body += "<p>README.md not found in the program folder.</p>"
            body += """
<div style="text-align:right;margin-top:1.2em">
<form action="https://www.paypal.com/cgi-bin/webscr" method="post" target="_blank" style="display:inline">
<input type="hidden" name="cmd" value="_s-xclick">
<input type="hidden" name="hosted_button_id" value="XNMPYU3R396Z8">
<button type="submit" name="submit" style="width:120px;height:47px;background:url(https://www.pege.org/pic/2024-01-18-logo.png) no-repeat -210px -60px;border:none;cursor:pointer;text-indent:-9999px;">Donate</button>
</form>
</div>
<p><a href="/">Back to main</a></p>
"""
            self._send(200, page_shell("About", body))
            return
        if parsed.path in ("/", "/form"):
            qs = parse_qs(parsed.query, keep_blank_values=True)
            if parsed.query:
                self._send(200, form_html(form_values_from_qs(qs)))
            elif LAST_FORM:
                self._send(200, form_html(LAST_FORM))
            else:
                self._send(200, form_html(startup_form_values()))
            return
        if parsed.path == "/new":
            self._send(200, form_html())
            return
        if parsed.path == "/api/folders":
            qs = parse_qs(parsed.query)
            raw = (qs.get("outdir") or [""])[0].strip()
            parent = Path(raw).expanduser() if raw else Path.cwd()
            try:
                parent = parent.resolve()
            except Exception:
                self._send_json(400, {"error": "Invalid folder"})
                return
            if not is_allowed_path(parent, extra=parent if parent.is_dir() else None):
                # allow the typed outdir itself if it exists
                if not parent.is_dir():
                    self._send_json(400, {"error": f"Not a folder: {parent}"})
                    return
            if not parent.is_dir():
                self._send_json(400, {"error": f"Not a folder: {parent}"})
                return
            payload = {"parent": str(parent), "folders": list_location_folders(parent)}
            self._send_json(200, payload)
            return
        if parsed.path == "/api/workspace":
            parent = data_root()
            locs = []
            for rec in list_location_folders(parent):
                parsed_loc = parse_location_folder_name(rec["name"].replace("  (parent)", "").strip())
                if parsed_loc:
                    locs.append(parsed_loc)
            self._send_json(200, {
                "parent": str(parent),
                "objects": list_objects(parent),
                "locations": locs,
            })
            return
        if parsed.path == "/api/location-remark":
            qs = parse_qs(parsed.query)
            try:
                lat = float((qs.get("lat") or ["0"])[0].replace(",", "."))
                lon = float((qs.get("lon") or ["0"])[0].replace(",", "."))
            except ValueError:
                self._send_json(200, {"remark": ""})
                return
            self._send_json(200, {"remark": location_remark(lat, lon, cache_dir=data_root())})
            return
        if parsed.path == "/api/object-files":
            qs = parse_qs(parsed.query)
            parent = data_root()
            name = sanitize_name((qs.get("object") or [""])[0])
            folder = (parent / "objects" / name) if name else None
            files = []
            if not name:
                self._send_json(200, {"object": "", "files": [], "error": "No object selected."})
                return
            if not folder or not folder.is_dir():
                self._send_json(200, {"object": name, "files": [], "error": f"No folder {folder}. Start folder is {parent}."})
                return
            stems = {}
            for pth in sorted(folder.iterdir(), key=lambda x: x.name.lower()):
                if pth.suffix.lower() not in {".pdf", ".json"}:
                    continue
                if pth.name.startswith("compare ") or " vs " in pth.name:
                    continue
                # object definition, not a simulation run
                if pth.suffix.lower() == ".json" and pth.stem == folder.name:
                    continue
                stems.setdefault(pth.stem, {})[pth.suffix.lower()] = pth
            for stem, pair in stems.items():
                chosen = pair.get(".pdf") or pair.get(".json")
                files.append({"name": stem, "path": str(chosen)})
            self._send_json(200, {"object": name, "files": files, "folder": str(folder)})
            return
        if parsed.path == "/stop":
            STOP["flag"] = True
            self._send(200, page_shell("Stop", "<p>Stop requested. The run will end at the next year.</p><p><a href='/form'>Back to main</a></p>"))
            return
        if parsed.path == "/compare":
            qs = parse_qs(parsed.query)
            raw_a = (qs.get("a") or [""])[0]
            raw_b = (qs.get("b") or [""])[0]
            path_a = Path(unquote(raw_a)).expanduser()
            path_b = Path(unquote(raw_b)).expanduser()
            try:
                path_a = path_a.resolve()
                path_b = path_b.resolve()
            except Exception:
                self._send(400, page_shell("Compare", "<p>Invalid path.</p>"))
                return
            if not path_a.is_file() or not path_b.is_file() or not is_allowed_path(path_a, path_a.parent) or not is_allowed_path(path_b, path_b.parent):
                self._send(403, page_shell("Compare", "<p>Those files are not readable from this server.</p>"))
                return
            parent = data_root()
            obj1 = path_a.parent.name
            obj2 = path_b.parent.name
            stem1 = path_a.stem
            stem2 = path_b.stem
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            def emit(chunk: str) -> None:
                self.wfile.write(chunk.encode("utf-8"))
                try:
                    self.wfile.flush()
                except BrokenPipeError:
                    pass

            head = page_shell(
                "Compare",
                "<p class='nav'><a href='/form'>Back to main</a></p>\n<div class='log' id='log'>\nLOG_STREAM",
            ).decode("utf-8")
            emit(head.split("LOG_STREAM", 1)[0])

            def log(msg: str) -> None:
                stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
                emit(html.escape(f"{stamp}  {msg}") + "<br><script>(function(){var b=document.getElementById('log');if(!b)return;b.scrollTop=b.scrollHeight;})();</script>")

            log("Comparing A and B…")
            log(f"A  {obj1} {stem1}")
            log(f"B  {obj2} {stem2}")
            from pege_energy_sim_report import compares_root
            def parts(stem: str):
                # date time - simulation name - location
                bits = stem.split(" - ", 2)
                date = bits[0] if bits else stem
                sim = bits[1] if len(bits) > 1 else ""
                loc = bits[2] if len(bits) > 2 else ""
                return date, sim, loc
            def side(obj, stem, other_obj, other_stem):
                date, sim, loc = parts(stem)
                od, osim, oloc = parts(other_stem)
                out = []
                if obj != other_obj:
                    out.append(obj)
                out.append(date)
                if sim and sim != osim:
                    out.append(sim)
                if loc and loc != oloc:
                    out.append(loc)
                return " - ".join(out)
            built = f"{obj1} {stem1} vs {side(obj2, stem2, obj1, stem1)}.pdf"
            name = (qs.get("name") or [""])[0].strip() or built
            if not name.lower().endswith(".pdf"):
                name += ".pdf"
            name = name.replace("/", " ").replace("\\", " ")
            if len(name.encode()) > 240:
                emit(
                    "<span class='err'>File name too long. "
                    f"{len(name.encode())} bytes, the limit is 255.</span><br>"
                    "<form method='get' action='/compare'>"
                    f"<input type='hidden' name='a' value='{html.escape(raw_a)}'>"
                    f"<input type='hidden' name='b' value='{html.escape(raw_b)}'>"
                    "<p>Edit the file name, then write the PDF.</p>"
                    f"<input name='name' value='{html.escape(name)}' style='width:100%'>"
                    "<p><button type='submit'>Write PDF</button></p></form></div>"
                    "<p class='nav'><a href='/form'>Back to main</a></p></main></body></html>"
                )
                return
            out = compares_root(parent) / name
            try:
                from pege_energy_sim_report import compare_reports
                compare_reports(path_a, path_b, out, label_a=f"{obj1} {stem1}", label_b=f"{obj2} {stem2}", log=log)
            except Exception as exc:
                emit(f"<span class='err'>{html.escape(str(exc))}</span><br></div>")
                emit("<p class='nav'><a href='/form'>Back to main</a></p></main></body></html>")
                return
            token = uuid.uuid4().hex
            ARTIFACTS[token] = out
            href = "/pdf/" + quote(token)
            emit("<p style='color:#146c2e;font-weight:600'>PDF ready.</p>")
            emit("</div>")
            emit(f"<p class='ok'>PDF ready.</p><p><a href='{href}' target='_blank'>Open {html.escape(out.name)}</a></p>")
            emit(f"<p class='hint'>{html.escape(str(out))}</p><p><a href='/form'>Back to main</a></p></main></body></html>")
            return
        if parsed.path == "/load-object":
            qs = parse_qs(parsed.query)
            raw = (qs.get("outdir") or [""])[0].strip()
            parent = Path(raw).expanduser() if raw else Path.cwd()
            name = sanitize_name((qs.get("name") or [""])[0])
            path = object_definition_path(object_folder(parent, name))
            if not path.is_file():
                self._send(404, page_shell("Missing object", f"<p>No {html.escape(str(path))}</p><p><a href='/form'>Back</a></p>"))
                return
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                self._send(400, page_shell("Bad object", f"<p>{html.escape(str(exc))}</p>"))
                return
            vals = data.get("form_values") or sidecar_to_form_values(data) or {}
            if data.get("demand"):
                vals.update(demand_to_form_values(data["demand"]))
            vals["object"] = name
            LAST_FORM.clear()
            LAST_FORM.update({str(k): "" if v is None else str(v) for k, v in vals.items()})
            self.send_response(302)
            self.send_header("Location", "/form?" + urlencode(LAST_FORM, doseq=False))
            self.end_headers()
            return
        if parsed.path == "/load-pdf":
            qs = parse_qs(parsed.query)
            raw = (qs.get("path") or [""])[0]
            path = Path(unquote(raw)).expanduser()
            try:
                path = path.resolve()
            except Exception:
                self._send(400, page_shell("Error", "<p>Invalid path. <a href='/form'>Back</a></p>"))
                return
            if path.suffix.lower() not in {".pdf", ".json"} or not path.is_file() or not is_allowed_path(path, extra=path.parent):
                self._send(403, page_shell("Error", "<p>That file is not readable from this server. <a href='/form'>Back</a></p>"))
                return
            try:
                vals = extract_form_from_pdf(path)
            except Exception as exc:
                self._send(400, page_shell(
                    "No parameters",
                    f"<p>{html.escape(str(exc))}</p><p><a href='/form'>Back to main</a></p>",
                ))
                return
            LAST_FORM.clear()
            LAST_FORM.update(vals)
            qs_out = urlencode(vals, doseq=False)
            self.send_response(302)
            self.send_header("Location", "/form?" + qs_out)
            self.end_headers()
            return
        if parsed.path.startswith("/pdf/"):
            token = unquote(parsed.path[len("/pdf/"):])
            path = ARTIFACTS.get(token)
            if not path or not path.is_file():
                self._send(404, page_shell("Missing", "<p>PDF not found. Run the form again.</p>"))
                return
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Disposition", f'inline; filename="{path.name}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path == "/rerun":
            self._handle_rerun()
            return
        self._send(404, page_shell("Not found", "<p>Unknown URL. <a href='/'>Back to main</a></p>"))

    def _handle_rerun(self) -> None:
        qs = parse_qs(urlparse(self.path).query)
        raw = (qs.get("files") or [""])[0]
        selected = [s for s in unquote(raw).split("|") if s.strip()]
        if not selected:
            self._send(400, page_shell("Rerun", "<p>Nothing marked.</p><p><a href='/files'>Back</a></p>"))
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        def emit(chunk: str) -> None:
            self.wfile.write(chunk.encode("utf-8"))
            try:
                self.wfile.flush()
            except BrokenPipeError:
                pass
        head = page_shell(
            "Rerun",
            "<p class='nav'><a href='/files'>Back to files</a></p>\n"
            "<script>/* marks cleared after success */</script>\n"
            "<div class='log' id='log'>\nLOG_STREAM",
        ).decode("utf-8")
        emit(head.split("LOG_STREAM", 1)[0])
        current = {"i": 0, "n": 0}
        def log(msg: str) -> None:
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            prefix = f"[{current['i']}/{current['n']}]  " if current["n"] else ""
            if msg == "PROGRESS-END":
                emit("<script>(function(){var s=document.getElementById('prog');if(s)s.removeAttribute('id');})();</script><br>")
                return
            if msg.startswith("PROGRESS "):
                text = msg[len("PROGRESS "):]
                print(f"{stamp}  {prefix}{text}", end="\r", flush=True)
                emit(
                    "<script>(function(){var b=document.getElementById('log');if(!b)return;"
                    "var s=document.getElementById('prog');"
                    "if(!s){s=document.createElement('span');s.id='prog';b.appendChild(s);}"
                    "s.innerHTML=" + repr(
                        html.escape(f"{stamp}  {prefix}")
                        + "".join(
                            "<span style='color:#fff;font-weight:bold'>" + html.escape(ch) + "</span>"
                            if ch.isdigit() or ch == "#" else html.escape(ch)
                            for ch in text
                        )
                    ) + ";"
                    "if(b.scrollHeight-b.scrollTop-b.clientHeight<80)b.scrollTop=b.scrollHeight;})();</script>"
                )
                return
            line = f"{stamp}  {prefix}{msg}"
            print(line)
            emit(html.escape(line) + "<br><script>(function(){var b=document.getElementById('log');if(!b)return;b.scrollTop=b.scrollHeight;})();</script>")
        results = []
        total = len(selected)
        current["n"] = total
        for i, key in enumerate(selected, 1):
            current["i"] = i
            folder_name, _, file_name = key.partition("/")
            log(f"[{i}/{total}] {folder_name}/{file_name}")
            root = data_root()
            folder = root / "objects" / folder_name
            js = folder / (file_name.replace("  (object)", "") + ".json")
            if not js.is_file():
                log(f"  missing {js}")
                continue
            try:
                old_data = json.loads(js.read_text(encoding="utf-8"))
            except Exception as exc:
                log(f"  cannot read {exc}")
                continue
            fv = old_data.get("form_values") or {}
            # Rebuild a minimal job from the stored form values and re-run.
            from pege_energy_sim_report import build_report
            try:
                new_pdf = build_report(
                    name=fv.get("name") or old_data.get("name") or "",
                    lat=float(fv.get("lat") or old_data.get("lat") or 0),
                    lon=float(fv.get("lon") or old_data.get("lon") or 0),
                    pv_list=[
                        (a.get("label"), a.get("slope"), a.get("azimuth"), a.get("wp"),
                         a.get("wall_d"), a.get("wall_h"), a.get("bifacial"), a.get("bifaciality"))
                        for a in (old_data.get("arrays") or [])
                    ],
                    startyear=int(fv.get("startyear") or old_data.get("startyear") or 2005),
                    endyear=int(fv.get("endyear") or old_data.get("endyear") or 2023),
                    loss=float(fv.get("loss") or old_data.get("loss") or 14),
                    raddatabase=fv.get("raddatabase") or old_data.get("raddatabase") or "auto",
                    usehorizon=bool(int(fv.get("usehorizon") or 1)),
                    outdir=str(root),
                    log=log,
                    form_values=fv,
                    result_folder=folder,
                    object_name=old_data.get("object") or folder_name,
                    demand=old_data.get("demand"),
                )
            except Exception as exc:
                log(f"  run failed: {exc}")
                continue
            results.append((js, new_pdf, old_data))
            log(f"  wrote {new_pdf.name}")
        # Write the difference report
        if results:
            from pege_energy_sim_report import compares_root
            stamp = datetime.now().strftime("%Y-%m-%d %H%M")
            out = compares_root(data_root()) / f"{stamp} difference report from rerun.pdf"
            self._write_difference_pdf(results, out, log)
            log(f"Difference report: {out}")
            emit("</div>")
            emit(f"<p class='ok'><a href='/file?path={quote(str(out))}' target='_blank'>Open difference report</a></p>")
            emit(f"<p>Also under Files → Compares: {html.escape(out.name)}</p>")
        else:
            emit("</div>")
        emit("<p><a href='/files'>Back to files</a></p></main></body></html>")

    def _write_difference_pdf(self, results, out, log):
        from matplotlib.backends.backend_pdf import PdfPages
        import matplotlib.pyplot as plt
        out.parent.mkdir(parents=True, exist_ok=True)
        with PdfPages(out) as pdf:
            for old_js, new_pdf, old_data in results:
                new_js = new_pdf.with_suffix(".json")
                try:
                    new_data = json.loads(new_js.read_text(encoding="utf-8"))
                except Exception:
                    continue
                old_pages = (old_data.get("outcome") or {}).get("pages") or {}
                new_pages = (new_data.get("outcome") or {}).get("pages") or {}
                diffs = []
                seen = set()
                max_month = 0.0
                max_day = 0.0
                for title in set(old_pages) | set(new_pages):
                    for rec_o, rec_n in zip(old_pages.get(title) or [], new_pages.get(title) or []):
                        if rec_o.get("title") != rec_n.get("title"):
                            continue
                        for k in ("Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec","All"):
                            vo = rec_o.get(k)
                            vn = rec_n.get(k)
                            if not isinstance(vo, (int, float)) or not isinstance(vn, (int, float)):
                                continue
                            if abs(vn - vo) < 0.05:
                                continue
                            pct = abs(vn - vo) / max(abs(vo), 1e-9) * 100
                            if pct < 0.5:
                                continue
                            key = (title, rec_o.get("title"), k)
                            if key in seen:
                                continue
                            seen.add(key)
                            diffs.append((title, rec_o.get("title"), k, vo, vn, pct))
                            if "month" in (rec_o.get("title") or "").lower():
                                max_month = max(max_month, pct)
                            else:
                                max_day = max(max_day, pct)
                if max_month < 1 and max_day < 2:
                    color = "#c8efd4"
                elif max_month < 2 and max_day < 3:
                    color = "#fff6d8"
                else:
                    color = "#f6c9c9"
                fig, ax = plt.subplots(figsize=(11.69, 8.27))
                ax.axis("off")
                ax.set_facecolor(color)
                fig.patch.set_facecolor(color)
                old_name = old_js.stem
                new_name = new_pdf.stem
                # Header with links, then the differences below.
                fig.text(0.04, 0.95, old_js.parent.name, fontsize=11, va="top", weight="bold")
                old_link = fig.text(0.04, 0.91, f"Old: {old_name}", fontsize=8, va="top", color="#003399")
                old_link.set_url(f"file://{old_js.with_suffix('.pdf')}")
                new_link = fig.text(0.04, 0.88, f"New: {new_name}", fontsize=8, va="top", color="#003399")
                new_link.set_url(f"file://{new_pdf}")
                lines = []
                if not diffs:
                    lines.append("No differences.")
                else:
                    for chapter, row, k, vo, vn, pct in diffs[:50]:
                        lines.append(f"{chapter}  {row}  {k}:  {vo:.2f} → {vn:.2f}  ({pct:.1f}%)")
                    if len(diffs) > 50:
                        lines.append(f"... and {len(diffs)-50} more")
                fig.text(0.04, 0.84, "\n".join(lines), fontsize=7.5, va="top", family="monospace")
                pdf.savefig(fig)
                plt.close(fig)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/investment":
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            fields = parse_qs(raw, keep_blank_values=True)
            data = {str(k): v[0] if isinstance(v, list) else str(v) for k, v in fields.items()}
            folder = data_root() / "investment-models"
            folder.mkdir(parents=True, exist_ok=True)
            name = data.get("proj_name") or "investment"
            stamp = datetime.now().strftime("%Y-%m-%d %H%M")
            safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in name).strip()
            path = folder / f"{stamp} {safe}.json"
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            self._send(200, page_shell("Investment model", INVEST_HTML + f"<p>Saved {path.name}</p>"))
            return
        if urlparse(self.path).path == "/set-default":
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            fields = parse_qs(raw, keep_blank_values=True)
            vals = {str(k): v[0] if isinstance(v, list) else str(v) for k, v in fields.items()}
            obj_name = (vals.get("object") or "").strip()
            if obj_name:
                ofolder = object_folder(data_root(), obj_name)
                object_definition_path(ofolder).write_text(
                    json.dumps({"object": obj_name, "form_values": vals, "default": True}, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            LAST_FORM.clear()
            LAST_FORM.update(vals)
            page = form_html(vals)
            if isinstance(page, bytes):
                page = page.decode("utf-8")
            note = (
                "<div style='background:#e8f6ee;border:1px solid #b7e0c5;"
                "padding:10px 14px;margin:0 0 14px;border-radius:4px'>"
                "<strong>Saved as the default for this object.</strong> "
                "Until now a run updated the object JSON every time. "
                "From now a run leaves the default alone. "
                "Click this button again to replace it."
                "</div>"
            )
            page = page.replace(
                "<form id=\"pvform\"",
                note + "<form id=\"pvform\"",
                1,
            )
            self._send(200, page.encode("utf-8"))
            return
        if urlparse(self.path).path == "/rerun":
            self._handle_rerun()
            return
        if urlparse(self.path).path != "/run":
            self._send(404, page_shell("Not found", "<p><a href='/'>Back to main</a></p>"))
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        fields = parse_qs(raw, keep_blank_values=True)

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def emit(chunk: str) -> None:
            self.wfile.write(chunk.encode("utf-8"))
            try:
                self.wfile.flush()
            except BrokenPipeError:
                pass

        head = page_shell(
            "PEGE energy sim",
            "<p class='nav'><a href='/form'>Back to main</a>  "
            "<button type='button' onclick=\"fetch('/stop')\">Stop</button></p>\n"
            "<div class='log' id='log'>\nLOG_STREAM",
        ).decode("utf-8")
        emit(head.split("LOG_STREAM", 1)[0])

        def log(msg: str) -> None:
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            if msg == "PROGRESS-END":
                emit("<script>(function(){var s=document.getElementById('prog');if(s)s.removeAttribute('id');})();</script><br>")
                return
            if msg.startswith("PROGRESS "):
                text = msg[len("PROGRESS "):]
                print(f"{stamp}  {text}", end="\r", flush=True)
                emit(
                    "<script>(function(){var b=document.getElementById('log');if(!b)return;"
                    "var s=document.getElementById('prog');"
                    "if(!s){s=document.createElement('span');s.id='prog';b.appendChild(s);}"
                    "s.innerHTML=" + repr(
                        html.escape(f"{stamp}  [1/1]  ")
                        + "".join(
                            "<span style='color:#fff;font-weight:bold'>" + html.escape(ch) + "</span>"
                            if ch.isdigit() or ch == "#" else html.escape(ch)
                            for ch in text
                        )
                    ) + ";"
                    "if(b.scrollHeight-b.scrollTop-b.clientHeight<80)b.scrollTop=b.scrollHeight;})();</script>"
                )
                return
            line = f"{stamp}  [1/1]  {msg}"
            print(line)
            emit(html.escape(line) + "<br><script>(function(){var b=document.getElementById('log');if(!b)return;if(b.scrollHeight-b.scrollTop-b.clientHeight<80)b.scrollTop=b.scrollHeight;})();</script>")
            if STOP["flag"]:
                raise RuntimeError("Stopped by user.")
            # expose stop to the report
            log.stop = lambda: STOP["flag"]

        STOP["flag"] = False
        LAST_FORM.clear()
        LAST_FORM.update({str(k): v[0] if isinstance(v, list) else str(v) for k, v in fields.items()})
        for i in range(1, N_ROWS + 1):
            if f"use{i}" not in fields:
                LAST_FORM[f"use{i}"] = "0"
            if f"bifacial{i}" not in fields:
                LAST_FORM[f"bifacial{i}"] = "0"
        obj_name = first(fields, "object").strip()
        if obj_name:
            try:
                ofolder = object_folder(data_root(), obj_name)
                path = object_definition_path(ofolder)
                marked = False
                if path.is_file():
                    marked = bool(json.loads(path.read_text(encoding="utf-8")).get("default"))
                if not marked:
                    path.write_text(
                        json.dumps({"object": obj_name, "form_values": dict(LAST_FORM)}, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
            except Exception:
                pass
        remark_text = first(fields, "locremark")[:2000]
        try:
            job = parse_job(fields)
            location_remark(job["lat"], job["lon"], cache_dir=data_root(), text=remark_text)
        except ValueError as exc:
            emit(f"<span class='err'>{html.escape(str(exc))}</span><br></div>")
            emit("<p class='nav'><a href='/form'>Back to main</a></p></main></body></html>")
            return

        LAST_FORM.clear()
        LAST_FORM.update(job_to_form_values(job))
        LAST_FORM["locremark"] = remark_text
        back_href = "/form?" + form_query(job)
        reopen_url = BASE_URL.rstrip("/") + back_href

        log(
            f"{job['name']}  {job['lat']}, {job['lon']}  "
            f"{len(job['pv'])} array(s)  {job['startyear']}-{job['endyear']}"
        )
        if not JOB_LOCK.acquire(blocking=False):
            emit("<span class='err'>A job is already running. Wait and submit again.</span><br></div>")
            emit(f"<p class='nav'><a href='{html.escape(back_href)}'>Back to main</a></p></main></body></html>")
            return
        try:
            result_folder = None
            if job.get("object"):
                ofolder = object_folder(job["outdir"], job["object"])  # creates objects/<name>/
                defn = {
                    "object": job["object"],
                    "form_values": job_to_form_values(job),
                    "pv": [
                        {
                            "label": t[0], "slope": t[1], "azimuth": t[2], "wp": t[3],
                            "wall_d": t[4] if len(t) > 4 else None,
                            "wall_h": t[5] if len(t) > 5 else None,
                            "bifacial": t[6] if len(t) > 6 else False,
                            "bifaciality": t[7] if len(t) > 7 else None,
                        }
                        for t in job["pv"]
                    ],
                    "demand": job.get("demand") or {},
                }
                object_definition_path(ofolder).write_text(
                    json.dumps(defn, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                log(f"Object definition: {object_definition_path(ofolder)}")
                result_folder = ofolder
                log(f"Results folder: {ofolder}")
            pdf_path = build_report(
                name=job["name"],
                lat=job["lat"],
                lon=job["lon"],
                pv_list=job["pv"],
                startyear=job["startyear"],
                endyear=job["endyear"],
                loss=job["loss"],
                raddatabase=job["raddatabase"],
                usehorizon=job["usehorizon"],
                outdir=job["outdir"],
                log=log,
                reopen_url=reopen_url,
                form_values=job_to_form_values(job),
                result_folder=result_folder,
                object_name=job.get("object") or None,
                demand=job.get("demand"),
            )
            token = uuid.uuid4().hex
            ARTIFACTS[token] = pdf_path
            href = "/pdf/" + quote(token)
            emit(
                f"<span class='ok'>PDF ready.</span><br></div>"
                f"<p class='nav'><a href='{href}' target='_blank'>Open {html.escape(pdf_path.name)}</a> "
                f"&nbsp;|&nbsp; <a href='{html.escape(back_href)}'>Back to main</a> "
                f"&nbsp;|&nbsp; <a href='/new'>Empty form</a></p>"
                f"<p class='hint'>{html.escape(str(pdf_path))}</p>"
                f"<p class='hint'><a href='{html.escape(back_href)}'>Reopen URL</a> — "
                f"{html.escape(reopen_url)}</p>"
                f"</main></body></html>"
            )
        except Exception as exc:
            if "Stopped by user" in str(exc):
                emit("<span class='err'>Stopped. Correct the import caps and run again.</span><br></div>")
            else:
                try:
                    emit(f"<span class='err'>{html.escape(traceback.format_exc())}</span><br></div>")
                except Exception:
                    pass
            emit(f"<p class='nav'><a href='{html.escape(back_href)}'>Back to main</a></p></main></body></html>")
        finally:
            JOB_LOCK.release()

    def _send(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def big_box(title: str, message: str, error: bool = False) -> None:
    """A large dialog. Printed box if the desktop cannot open one."""
    bar = "=" * 62
    print("\n" + bar)
    print(title)
    print(bar)
    print(message)
    print(bar + "\n")
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        if error:
            messagebox.showerror(title, message)
        else:
            messagebox.showinfo(title, message)
        root.destroy()
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    port = DEFAULT_PORT
    args = list(sys.argv[1:] if argv is None else argv)
    if "--port" in args:
        i = args.index("--port")
        port = int(args[i + 1])
    no_browser = "--no-browser" in args

    global BASE_URL
    try:
        httpd = ThreadingHTTPServer((HOST, port), Handler)
    except OSError as exc:
        if getattr(exc, "errno", None) in (98, 10048):
            print("\n" + "=" * 62)
            print("PEGE energy sim is already running")
            print("=" * 62)
            print("The server was not started.")
            print("An older copy is still using this port.")
            print("Close that copy first: in its terminal press Ctrl+C.")
            print(f"Or open the form that is already running:\nhttp://{HOST}:{port}/")
            print("Do not start a second copy over the first one.")
            print("Press Ctrl+C or Enter to close this window.")
            print("=" * 62 + "\n")
            try:
                input()
            except (KeyboardInterrupt, EOFError):
                pass
            return 1
        raise
    url = f"http://{HOST}:{port}/"
    BASE_URL = url.rstrip("/")
    bar = "=" * 62
    print("\n" + bar)
    print(f"{datetime.now():%Y-%m-%d %H:%M}  PEGE energy sim listening on {url}")
    print("Look now at the default browser. That window is the GUI.")
    print("Stop with Ctrl+C")
    print(bar + "\n")

    if not no_browser:
        try:
            open_browser(url)
        except FileNotFoundError as exc:
            print(f"  {exc}")
            print(f"  Open {url} yourself in a browser")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print(f"\n{datetime.now():%Y-%m-%d %H:%M}  server stopped")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception:
        traceback.print_exc()
        try:
            input("\nError — press Enter to close...")
        except Exception:
            pass
        sys.exit(1)
