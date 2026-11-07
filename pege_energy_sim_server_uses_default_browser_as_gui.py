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
ARTIFACTS = {}
FORM_STATE = {}
LAST_LOADED = None
BUILD_VERSION = ""

from pege_energy_sim_common import (
    LOCAL_VERSION,
    parse_form, data_root, first,
    HOST, DEFAULT_PORT, N_ROWS, LAST_FORM, CONFIG, BASE_URL,
    startup_form_values, _pdf_lang, open_browser,
    set_language, _t, _fmt,
)
from pege_energy_sim_common import demand_form_html, parse_demand_fields, demand_to_form_values

STOP = {"flag": False}
JOB_LOCK = threading.Lock()
from pege_energy_sim_common import sanitize_name, list_objects, location_remark, object_folder, object_definition_path, build_report

from pege_energy_sim_project_finance import page_shell
from pege_energy_sim_object_house import (
    form_html, parse_job, job_to_form_values, form_query, form_values_from_qs,
    allowed_roots, is_allowed_path, parse_location_folder_name, list_location_folders,
    sidecar_to_form_values, query_from_reopen_url, extract_form_from_pdf,
)
from pege_energy_sim_project_finance import (
    INVEST_HTML, BASELINE_HTML, VARIANT_HTML, SIMULATE_HTML,
    PF_STORE, pf_safe, pf_folder, pf_default_path, pf_ensure_default,
    pf_ensure_loaded, pf_load, pf_save_default, pf_save_simulation, pf_save,
    pf_complete_selects, pf_inject_selects, pf_fill_html,
)

HERE = Path(__file__).resolve().parent
SETTINGS_PATH = HERE / "settings.json"

def load_state():
    global FORM_STATE
    if SETTINGS_PATH.is_file():
        try:
            FORM_STATE.update(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass

def save_state():
    try:
        SETTINGS_PATH.write_text(json.dumps(FORM_STATE, indent=2), encoding="utf-8")
    except Exception:
        pass

load_state()
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
try:
    os.chdir(HERE)
except Exception:
    pass



FILES_HTML = """
<h2 id="filesTitle">Objects</h2>
<nav style="margin:8px 0 14px">
  <a href="/files" style="margin-right:16px">Objects</a>
  <a href="/files?tab=compares" style="margin-right:16px">Compares</a>
  <a href="/files?tab=locations" style="margin-right:16px">Locations</a>
  <a href="/files?tab=investment" style="margin-right:16px">Project finances</a>
  <a href="/files?tab=other">Other</a>
  <button type="button" id="rerunAll" style="margin-left:16px;display:none">rerun all marked object reports</button>
  <button type="button" id="deleteMarked" style="margin-left:8px;display:none">delete marked</button>
</nav>
<p class="hint" id="filesStatus">Reading…</p>
<div id="filesList"></div>
<p><a href="/form">Back to main</a></p>
<script>
(function () {
  const tab = new URLSearchParams(location.search).get("tab") || "objects";
  const titles = {objects: "Objects", compares: "Compares", locations: "Locations", investment: "Project finances", other: "Other"};
  document.getElementById("filesTitle").textContent = titles[tab] || "Objects";
  const list = document.getElementById("filesList");
  const status = document.getElementById("filesStatus");
  fetch("/api/files-index").then(function (r) { return r.json(); }).then(function (data) {
    const groups = data[tab] || [];
    status.textContent = groups.length ? "" : "Nothing here.";
    if (tab === "investment") {
      const folder = new URLSearchParams(location.search).get("folder");
      if (!folder) {
        groups.forEach(function (g) {
          const div = document.createElement("div");
          div.className = "folder";
          div.style.margin = "6px 0";
          const a = document.createElement("a");
          a.href = "/files?tab=investment&folder=" + encodeURIComponent(g.name);
          a.textContent = g.name + " (" + (g.files || []).length + ")";
          div.appendChild(a);
          list.appendChild(div);
        });
        return;
      }
      const g = groups.find(function (x) { return x.name === folder; });
      if (!g) { status.textContent = "Folder not found."; return; }
      const back = document.createElement("p");
      back.innerHTML = '<a href="/files?tab=investment">← all projects</a>';
      list.appendChild(back);
      const table = document.createElement("table");
      table.style.cssText = "width:100%;border-collapse:collapse";
      table.innerHTML = "<thead><tr><th style='width:50px'>open</th><th style='width:40px'>edit</th><th style='width:40px'>mark</th><th>" + folder + "</th></tr></thead>";
      const body = document.createElement("tbody");
      const marks = JSON.parse(localStorage.getItem("pfMarks") || "{}");
      function updateDel() {
        const m = JSON.parse(localStorage.getItem("pfMarks") || "{}");
        const present = {};
        (g.files || []).forEach(function (f) { present[folder + "/" + f.name] = true; });
        const sel = Object.keys(m).filter(function (k) { return m[k] && present[k]; });
        const del = document.getElementById("deleteMarked");
        if (del) {
          del.style.display = "inline";
          del.textContent = "Delete all marked in this folder (" + sel.length + ")";
          del.onclick = function () {
            if (!sel.length) return;
            const names = sel.map(function (k) { return k.split("/").slice(1).join("/"); });
            const box = document.createElement("div");
            box.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.4);display:flex;align-items:center;justify-content:center;z-index:99";
            const card = document.createElement("div");
            card.style.cssText = "background:#fff;padding:16px 20px;border-radius:6px;max-width:480px;max-height:70vh;overflow:auto";
            card.innerHTML = "<p><b>Delete these " + names.length + " files?</b></p><ul style='margin:8px 0;padding-left:18px'>" + names.map(function (n) { return "<li>" + n + "</li>"; }).join("") + "</ul>";
            const cancel = document.createElement("button");
            cancel.textContent = "Cancel";
            cancel.onclick = function () { box.remove(); };
            const ok = document.createElement("button");
            ok.textContent = "Delete";
            ok.style.marginLeft = "8px";
            ok.onclick = function () {
              window.location = "/delete-files?files=" + encodeURIComponent(sel.join("|")) + "&back=" + encodeURIComponent(location.pathname + location.search);
            };
            card.appendChild(cancel); card.appendChild(ok);
            box.appendChild(card);
            document.body.appendChild(box);
          };
        }
      }
      (g.files || []).forEach(function (f) {
        const tr = document.createElement("tr");
        const open = document.createElement("td");
        if (f.href) open.innerHTML = '<a href="' + f.href + '" target="_blank">open</a>';
        const edit = document.createElement("td");
        if (f.edit) edit.innerHTML = '<a href="' + f.edit + '">edit</a>';
        const mark = document.createElement("td");
        const cb = document.createElement("input");
        cb.type = "checkbox";
        const key = folder + "/" + f.name;
        cb.checked = !!marks[key];
        cb.onchange = function () {
          const m = JSON.parse(localStorage.getItem("pfMarks") || "{}");
          m[key] = cb.checked;
          localStorage.setItem("pfMarks", JSON.stringify(m));
          updateDel();
        };
        mark.appendChild(cb);
        const name = document.createElement("td");
        name.textContent = f.name;
        tr.appendChild(open); tr.appendChild(edit); tr.appendChild(mark); tr.appendChild(name);
        body.appendChild(tr);
      });
      table.appendChild(body);
      list.appendChild(table);
      updateDel();
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
        const marksNow = JSON.parse(localStorage.getItem("rerunMarks") || "{}");
        const valid = {};
        (data.objects || []).forEach(function (g) {
          (g.files || []).forEach(function (f) {
            const key = g.name + "/" + f.name;
            if (marksNow[key]) valid[key] = true;
          });
        });
        localStorage.setItem("rerunMarks", JSON.stringify(valid));
        const selectedKeys = Object.keys(valid);
        const btn = document.getElementById("rerunAll");
        const del = document.getElementById("deleteMarked");
        if (btn) {
          btn.style.display = "inline";
          btn.textContent = "rerun all marked object reports (" + selectedKeys.length + ")";
          btn.onclick = function () {
            if (!selectedKeys.length) return;
            window.location = "/rerun?files=" + encodeURIComponent(selectedKeys.join("|"));
          };
        }
        if (del) {
          const inFolder = new URLSearchParams(location.search).get("folder");
          del.style.display = inFolder ? "inline" : "none";
          del.textContent = "Delete all marked objects in this folder (" + selectedKeys.length + ")";
          del.onclick = function () {
            if (!selectedKeys.length) return;
            const names = selectedKeys.map(function (k) { return k.split("/").slice(1).join("/"); });
            const box = document.createElement("div");
            box.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.4);display:flex;align-items:center;justify-content:center;z-index:99";
            const card = document.createElement("div");
            card.style.cssText = "background:#fff;padding:16px 20px;border-radius:6px;max-width:520px;max-height:70vh;overflow:auto";
            card.innerHTML = "<p><b>Delete these " + names.length + " files?</b></p><ul style='margin:8px 0;padding-left:18px'>" + names.map(function (n) { return "<li>" + n + "</li>"; }).join("") + "</ul>";
            const cancel = document.createElement("button");
            cancel.textContent = "Cancel";
            cancel.onclick = function () { box.remove(); };
            const ok = document.createElement("button");
            ok.textContent = "Delete";
            ok.style.marginLeft = "8px";
            ok.onclick = function () {
              window.location = "/delete-files?files=" + encodeURIComponent(selectedKeys.join("|"));
            };
            card.appendChild(cancel); card.appendChild(ok);
            box.appendChild(card);
            document.body.appendChild(box);
          };
        }
        return;
      }
      const g = groups.filter(function (x) { return x.name === folder; })[0];
      if (!g) { status.textContent = "No such object."; return; }
      const back = document.createElement("p");
      back.innerHTML = "<a href='/files'>All objects</a>";
      list.appendChild(back);
      const table = document.createElement("table");
      table.style.cssText = "width:100%;border-collapse:collapse";
      table.innerHTML = "<thead><tr><th style='width:50px'>open</th><th style='width:40px'>edit</th><th>" + folder + "</th><th style='width:40px'>by</th><th style='width:40px'>mark</th><th style='width:36px'></th></tr></thead>";
      const body = document.createElement("tbody");
      const marks = JSON.parse(localStorage.getItem("rerunMarks") || "{}");
      function updateBtn() {
        const marks = JSON.parse(localStorage.getItem("rerunMarks") || "{}");
        const selected = Object.keys(marks).filter(function (k) { return marks[k] && k.startsWith(folder + "/"); });
        const btn = document.getElementById("rerunAll");
        const del = document.getElementById("deleteMarked");
        if (btn) { btn.style.display = "inline"; btn.textContent = "rerun all marked object reports (" + selected.length + ")"; }
        if (del) {
          del.style.display = "inline";
          del.textContent = "Delete all marked objects in this folder (" + selected.length + ")";
          del.onclick = function () {
            if (!selected.length) return;
            const names = selected.map(function (k) { return k.split("/").slice(1).join("/"); });
            const box = document.createElement("div");
            box.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.4);display:flex;align-items:center;justify-content:center;z-index:99";
            const card = document.createElement("div");
            card.style.cssText = "background:#fff;padding:16px 20px;border-radius:6px;max-width:520px;max-height:70vh;overflow:auto";
            card.innerHTML = "<p><b>Delete these " + names.length + " files?</b></p><ul style='margin:8px 0;padding-left:18px'>" + names.map(function (n) { return "<li>" + n + "</li>"; }).join("") + "</ul>";
            const cancel = document.createElement("button");
            cancel.textContent = "Cancel";
            cancel.onclick = function () { box.remove(); };
            const ok = document.createElement("button");
            ok.textContent = "Delete";
            ok.style.marginLeft = "8px";
            ok.onclick = function () {
              window.location = "/delete-files?files=" + encodeURIComponent(selected.join("|"));
            };
            card.appendChild(cancel); card.appendChild(ok);
            box.appendChild(card);
            document.body.appendChild(box);
          };
        }
      }
      const menuBtn = document.getElementById("rerunAll");
      if (menuBtn) menuBtn.onclick = function () {
        const folder = new URLSearchParams(location.search).get("folder") || "";
        const all = JSON.parse(localStorage.getItem("rerunMarks") || "{}");
        const selected = Object.keys(all).filter(function (k) {
          if (!all[k]) return false;
          if (folder) return k.startsWith(folder + "/");
          return true;
        });
        if (!selected.length) return;
        const names = selected.map(function (k) { return k.split("/").slice(1).join("/"); });
        const box = document.createElement("div");
        box.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.4);display:flex;align-items:center;justify-content:center;z-index:99";
        const card = document.createElement("div");
        card.style.cssText = "background:#fff;padding:16px 20px;border-radius:6px;max-width:560px;max-height:70vh;overflow:auto";
        card.innerHTML = "<p><b>Rerun these " + names.length + " reports?</b></p><ul style='margin:8px 0;padding-left:18px;max-height:40vh;overflow:auto'>" + names.map(function (n) { return "<li>" + n + "</li>"; }).join("") + "</ul>";
        const cancel = document.createElement("button");
        cancel.textContent = "Cancel";
        cancel.onclick = function () { box.remove(); };
        const ok = document.createElement("button");
        ok.textContent = "Rerun";
        ok.style.marginLeft = "8px";
        ok.onclick = function () {
          window.location = "/rerun?files=" + encodeURIComponent(selected.join("|"));
        };
        card.appendChild(cancel); card.appendChild(ok);
        box.appendChild(card);
        document.body.appendChild(box);
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
        name.style.cssText = "white-space:nowrap";
        name.title = f.name;
        const by = document.createElement("td");
        by.textContent = f.version || "0.2";
        const rerun = document.createElement("td");
        const flagTd = document.createElement("td");
        flagTd.style.cssText = "padding:2px 4px";
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
          const lang = f.pdf_language || f.language || "english";
          const pos = {english:[362,2],german:[322,2],spanish:[2,32],portugese:[322,32],french:[42,32],italian:[122,32],polish:[282,32],czech:[482,2],slovak:[442,32],hungarian:[82,32],romanian:[362,32],slovenian:[482,32],croatian:[562,64]};
          const xy = pos[lang] || pos.english;
          const outer = document.createElement("div");
          outer.style.cssText = "width:36px;height:24px;overflow:hidden;position:relative";
          const img = document.createElement("img");
          img.src = "https://www.pege.org/pic/2026-11-05-logo.png";
          img.style.cssText = "position:absolute;left:" + (-xy[0]) + "px;top:" + (-xy[1]) + "px;border:0;cursor:pointer";
          img.title = lang;
          img.onclick = function () { location.href = "/pdf-output?lang=" + encodeURIComponent(lang); };
          img.onmouseover = function () { outer.style.outline = "2px solid #3584e4"; };
          img.onmouseout = function () { outer.style.outline = ""; };
          outer.appendChild(img);
          flagTd.appendChild(outer);
        }
        tr.appendChild(open); tr.appendChild(edit); tr.appendChild(name); tr.appendChild(by); tr.appendChild(rerun); tr.appendChild(flagTd);
        body.appendChild(tr);
      });
      table.appendChild(body);
      list.appendChild(table);
      updateBtn();
      return;
    }
    groups.forEach(function (g) {
      const div = document.createElement("div");
    if (tab === "compares") {
      const del = document.createElement("button");
      del.id = "deleteMarked";
      del.style.display = "none";
      del.style.marginBottom = "8px";
      list.appendChild(del);
      groups.forEach(function (g) {
        const marks = JSON.parse(localStorage.getItem("cmpMarks") || "{}");
        const table = document.createElement("table");
        table.style.cssText = "width:100%;border-collapse:collapse";
        table.innerHTML = "<thead><tr><th style='width:50px'>open</th><th style='width:40px'>mark</th><th>" + g.name + "</th></tr></thead>";
        const body = document.createElement("tbody");
        function updateDel() {
          const m = JSON.parse(localStorage.getItem("cmpMarks") || "{}");
          const sel = Object.keys(m).filter(function (k) { return m[k]; });
          del.style.display = "inline";
          del.textContent = "Delete all marked (" + sel.length + ")";
          del.onclick = function () {
            if (!sel.length) return;
            const names = sel.map(function (k) { return k.split("/").slice(1).join("/"); });
            const box = document.createElement("div");
            box.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.4);display:flex;align-items:center;justify-content:center;z-index:99";
            const card = document.createElement("div");
            card.style.cssText = "background:#fff;padding:16px 20px;border-radius:6px;max-width:480px;max-height:70vh;overflow:auto";
            card.innerHTML = "<p><b>Delete these " + names.length + " files?</b></p><ul style='margin:8px 0;padding-left:18px'>" + names.map(function (n) { return "<li>" + n + "</li>"; }).join("") + "</ul>";
            const cancel = document.createElement("button");
            cancel.textContent = "Cancel";
            cancel.onclick = function () { box.remove(); };
            const ok = document.createElement("button");
            ok.textContent = "Delete";
            ok.style.marginLeft = "8px";
            ok.onclick = function () {
              window.location = "/delete-files?files=" + encodeURIComponent(sel.join("|")) + "&back=" + encodeURIComponent(location.pathname + location.search);
            };
            card.appendChild(cancel); card.appendChild(ok);
            box.appendChild(card);
            document.body.appendChild(box);
          };
        }
        (g.files || []).forEach(function (f) {
          const tr = document.createElement("tr");
          const open = document.createElement("td");
          if (f.href) open.innerHTML = '<a href="' + f.href + '" target="_blank">open</a>';
          const mark = document.createElement("td");
          const cb = document.createElement("input");
          cb.type = "checkbox";
          const key = g.name + "/" + f.name;
          cb.checked = !!marks[key];
          cb.onchange = function () {
            const m = JSON.parse(localStorage.getItem("cmpMarks") || "{}");
            m[key] = cb.checked;
            localStorage.setItem("cmpMarks", JSON.stringify(m));
            updateDel();
          };
          mark.appendChild(cb);
          const name = document.createElement("td");
          name.textContent = f.name;
          name.style.cssText = "white-space:nowrap";
          name.title = f.name;
          tr.appendChild(open); tr.appendChild(mark); tr.appendChild(name);
          body.appendChild(tr);
        });
        table.appendChild(body);
        list.appendChild(table);
        updateDel();
      });
      return;
    }
    });
  }).catch(function (err) { status.textContent = String(err); });
})();
</script>
"""


OBJECTS_HTML = """
<h2>Other objects</h2>
<p class="hint">Yield and demand model shared with house. Select or define an other object (garage, settlement part, etc.).</p>
<div class="row">
  <div class="field name"><label>Object</label><select id="objPick"><option value="">— select —</option></select></div>
  <div class="field wide"><label>Simulation</label><select id="simPick"><option value="">— select —</option></select></div>
</div>
<p class="hint">Full form for other object types will reuse the house yield/demand engine.</p>
<div class="actions">
  <button type="button" onclick="location.href='/form'">Open as house form</button>
</div>
<p><a href="/form">Back to main</a></p>
<script>
fetch("/api/workspace").then(r => r.json()).then(d => {
  const sel = document.getElementById("objPick");
  (d.objects || []).forEach(n => {
    const o = document.createElement("option"); o.value = n; o.textContent = n; sel.appendChild(o);
  });
});
document.getElementById("objPick").addEventListener("change", function() {
  const sSel = document.getElementById("simPick");
  sSel.innerHTML = "<option value=''>— select —</option>";
  if (!this.value) return;
  fetch("/api/object-files?object=" + encodeURIComponent(this.value)).then(r => r.json()).then(data => {
    (data.files || []).forEach(f => {
      const o = document.createElement("option"); o.value = f.path; o.textContent = f.name; sSel.appendChild(o);
    });
  });
});
</script>
"""

COMMUNITY_HTML = """
<h2>Community</h2>
<p class="hint">Community runs (DC bus between houses, shared storage, power-to-X) — version 1.0 feature. Yield and demand shared with house.</p>
<div class="row">
  <div class="field name"><label>Community object</label><select id="commObj"><option value="">— select —</option></select></div>
</div>
<p class="hint">Select houses that belong to the community. Full simulation engine will be added.</p>
<div class="actions">
  <button type="button" disabled>Run community simulation (soon)</button>
</div>
<p><a href="/form">Back to main</a></p>
<script>
fetch("/api/workspace").then(r => r.json()).then(d => {
  const sel = document.getElementById("commObj");
  (d.objects || []).forEach(n => {
    const o = document.createElement("option"); o.value = n; o.textContent = n; sel.appendChild(o);
  });
});
</script>
"""

COMPARE_COMMUNITIES_HTML = """
<h2>Compare communities</h2>
<p class="hint">Difference is B − A. Same colour rules as house compare.</p>
<div class="row">
  <div class="field name"><label>Community A</label><select id="cA"><option value="">— select —</option></select></div>
  <div class="field wide"><label>Simulation A</label><select id="sA"><option value="">— file —</option></select></div>
</div>
<div class="row">
  <div class="field name"><label>Community B</label><select id="cB"><option value="">— select —</option></select></div>
  <div class="field wide"><label>Simulation B</label><select id="sB"><option value="">— file —</option></select></div>
</div>
<p class="hint" id="cStatus"></p>
<div class="actions"><button type="button" id="cBtn" disabled>Compare — write PDF (soon)</button></div>
<p><a href="/form">Back to main</a></p>
<script>
function fill(sel, obj) {
  sel.innerHTML = "<option value=''>— file —</option>";
  if (!obj) return;
  fetch("/api/object-files?object=" + encodeURIComponent(obj)).then(r => r.json()).then(data => {
    (data.files || []).forEach(f => {
      const o = document.createElement("option"); o.value = f.path; o.textContent = f.name; sel.appendChild(o);
    });
  });
}
fetch("/api/workspace").then(r => r.json()).then(d => {
  ["cA","cB"].forEach(id => {
    const sel = document.getElementById(id);
    (d.objects || []).forEach(n => {
      const o = document.createElement("option"); o.value = n; o.textContent = n; sel.appendChild(o);
    });
  });
});
document.getElementById("cA").addEventListener("change", function() { fill(document.getElementById("sA"), this.value); });
document.getElementById("cB").addEventListener("change", function() { fill(document.getElementById("sB"), this.value); });
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


def remote_version(timeout: float = 5.0) -> str | None:
    """Read README.md from GitHub, find the highest ## YYYY-MM-DD Version N line.
    Returns the highest version number as a string, or None if the fetch fails.
    A short timeout keeps the program usable without internet."""
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://raw.githubusercontent.com/pegefounder/energy-sim/main/README.md",
            headers={"User-Agent": "PEGE-energy-sim"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None
    best = None
    best_num = -1.0
    for line in text.splitlines():
        s = line.strip()
        # ## YYYY-MM-DD Version VERSIONNUMBER
        # "##", two "-", and "Version" qualify the line
        if not s.startswith("##"):
            continue
        if s.count("-") < 2 or "Version" not in s:
            continue
        # extract the number after Version
        parts = s.split("Version", 1)
        if len(parts) < 2:
            continue
        num_s = parts[1].strip().split()[0] if parts[1].strip() else ""
        try:
            num = float(num_s)
        except ValueError:
            continue
        if num > best_num:
            best_num = num
            best = num_s
    return best



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
    if cache.is_file() and cache.stat().st_size > 32:
        return cache.read_bytes()
    try:
        data = _fetch_bytes(FAVICON_URL)
        if data and len(data) > 32:
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
        # Store field update from query string if present
        qs = parse_qs(parsed.query)
        if qs.get("field") and qs.get("page"):
            page = qs["page"][0]
            field = qs["field"][0]
            value = (qs.get("value") or [""])[0]
            project = (qs.get("project") or [PF_STORE.get("project") or ""])[0]
            if project:
                PF_STORE["project"] = project
            if field:
                if page == "baseline":
                    PF_STORE.setdefault("variants", {}).setdefault("BASELINE", {})[field] = value
                elif page in "abcde":
                    PF_STORE.setdefault("variants", {}).setdefault(page.upper(), {})[field] = value
                else:
                    PF_STORE.setdefault("data", {})[field] = value
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
        if parsed.path == "/objects":
            self._send(200, page_shell("Other objects", OBJECTS_HTML))
            return
        if parsed.path == "/community":
            self._send(200, page_shell("Community", COMMUNITY_HTML))
            return
        if parsed.path == "/compare-communities":
            self._send(200, page_shell("Compare communities", COMPARE_COMMUNITIES_HTML))
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
                    if js and not js.is_file():
                        js = None
                    definition = name == folder.name and not pdf
                    version = "0.2"
                    lang = "english"
                    if js and js.is_file():
                        try:
                            # Read only the first 2 KB; version and language are near the top
                            head = js.read_text(encoding="utf-8")[:2048]
                            import re as _re
                            m = _re.search(r'"software_version"\s*:\s*"([^"]*)"', head)
                            if m:
                                version = m.group(1) or "0.0"
                            m = _re.search(r'"pdf_language"\s*:\s*"([^"]*)"', head)
                            if m:
                                lang = m.group(1) or "english"
                        except Exception:
                            pass
                    out.append({
                        "name": name + ("  (object)" if definition else ""),
                        "href": "/file?path=" + quote(str(pdf)) if pdf else "",
                        "edit": "/load?json=" + quote(str(js)) if js else "",
                        "version": version,
                        "pdf_language": lang,
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
            idir = root / "project-finance"
            if idir.is_dir():
                for folder in sorted(p for p in idir.iterdir() if p.is_dir()):
                    files = []
                    for f in sorted(folder.glob("*.json")):
                        pdf = f.with_suffix(".pdf")
                        files.append({
                            "name": f.stem,
                            "href": "/file?path=" + quote(str(pdf)) if pdf.is_file() else "",
                            "edit": "/project-finance?json=" + quote(str(f)),
                            "json": str(f),
                        })
                    investment.append({"name": folder.name, "files": files})
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
        if parsed.path == "/project-finance":
            pf_ensure_loaded()
            qs = parse_qs(parsed.query)
            if not qs and not parsed.query and PF_STORE.get("last_page") and PF_STORE["last_page"] != "general":
                self.send_response(302)
                self.send_header("Location", "/project-finance/" + PF_STORE["last_page"])
                self.end_headers()
                return
            # handle project or sim selection from dropdown
            if qs.get("project"):
                PF_STORE["project"] = qs["project"][0]
                PF_STORE["data"] = pf_load(qs["project"][0])
            if qs.get("sim"):
                PF_STORE["selected_sim"] = qs["sim"][0]
                data = pf_load(PF_STORE.get("project") or "", qs["sim"][0])
                if data:
                    PF_STORE["data"] = data
            if qs.get("field") and qs.get("page"):
                page = qs["page"][0]
                field = qs["field"][0]
                value = (qs.get("value") or [""])[0]
                if page == "baseline":
                    PF_STORE.setdefault("variants", {}).setdefault("BASELINE", {})[field] = value
                elif page in "abcde":
                    PF_STORE.setdefault("variants", {}).setdefault(page.upper(), {})[field] = value
                else:
                    PF_STORE.setdefault("data", {})[field] = value
            # ensure model_name and sim_name are in data
            if PF_STORE.get("project") and "model_name" not in (PF_STORE.get("data") or {}):
                PF_STORE.setdefault("data", {})["model_name"] = PF_STORE["project"]
            sel = PF_STORE.get("selected_sim") or ""
            if sel and "sim_name" not in (PF_STORE.get("data") or {}):
                import re
                m = re.match(r"\d{4}-\d{2}-\d{2} \d{4} (.+)", sel)
                PF_STORE.setdefault("data", {})["sim_name"] = m.group(1) if m else sel
            PF_STORE["last_page"] = "general"
            page = pf_fill_html(INVEST_HTML)
            sel = PF_STORE.get("selected_sim") or ""
            page += "<script>window.PF_SELECTED_SIM = " + json.dumps(sel) + ";</script>"
            self._send(200, page_shell("Project finance", page))
            return
        if parsed.path == "/project-finance/baseline":
            PF_STORE["last_page"] = "baseline"
            pf_ensure_loaded()
            qs = parse_qs(parsed.query)
            if qs.get("field"):
                page = (qs.get("page") or ["baseline"])[0]
                field = qs["field"][0]
                value = (qs.get("value") or [""])[0]
                if page == "baseline":
                    PF_STORE.setdefault("variants", {}).setdefault("BASELINE", {})[field] = value
                elif page in "abcde":
                    PF_STORE.setdefault("variants", {}).setdefault(page.upper(), {})[field] = value
                else:
                    PF_STORE.setdefault("data", {})[field] = value
            base = (PF_STORE.get("variants") or {}).get("BASELINE") or {}
            page = pf_fill_html(BASELINE_HTML, base)
            page = pf_complete_selects(page, base)
            stored = {k: v for k, v in base.items() if k.startswith("obj_") or k.startswith("sim_")}
            page += "<script>window.PF_STORED = " + json.dumps(stored) + ";</script>"
            self._send(200, page_shell("Project finance", page))
            return
        if parsed.path.startswith("/project-finance/") and parsed.path.split("/")[-1] in list("abcde"):
            PF_STORE["last_page"] = parsed.path.split("/")[-1]
            letter = parsed.path[-1].upper()
            pf_ensure_loaded()
            qs = parse_qs(parsed.query)
            if qs:
                src_letter = (qs.get("letter") or [letter])[0].upper()
                if src_letter in "ABCDE" or src_letter == "BASELINE":
                    store = PF_STORE.setdefault("variants", {}).setdefault(src_letter, {})
                else:
                    store = PF_STORE.setdefault("data", {})
                for k, v in qs.items():
                    if k not in ("project", "sim", "letter"):
                        store[k] = v[0] if isinstance(v, list) else v
            variant_data = (PF_STORE.get("variants") or {}).get(letter) or {}
            raw = VARIANT_HTML.replace("{letter}", letter).replace("{letter_lower}", letter.lower()).replace("{{", "{").replace("}}", "}")
            page = pf_fill_html(raw, variant_data)
            page = pf_complete_selects(page, variant_data)
            stored = {k: v for k, v in variant_data.items() if k.startswith("obj_") or k.startswith("sim_")}
            page += "<script>window.PF_STORED = " + json.dumps(stored) + ";</script>"
            self._send(200, page_shell("Project finance", page))
            return
        if parsed.path == "/api/pf-simulate":
            from pege_energy_sim_project_finance import pf_simulate
            qs = parse_qs(parsed.query)
            variant = (qs.get("v") or ["baseline"])[0]
            stress = (qs.get("s") or ["normal"])[0]
            result = pf_simulate(variant, stress)
            self._send_json(200, result)
            return
        if parsed.path == "/project-finance/pdf":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            def emit(chunk):
                self.wfile.write(chunk.encode("utf-8"))
                try:
                    self.wfile.flush()
                except BrokenPipeError:
                    pass
            head = page_shell("PDF creation", "<h3>Creating PDF</h3><div class='log' id='log' style='background:#0f1a2b;color:#d6e2f0;padding:12px 14px;max-height:70vh;overflow:auto;font:12px/1.45 ui-monospace,Consolas,monospace;white-space:pre-wrap'>LOG_STREAM").decode("utf-8")
            emit(head.split("LOG_STREAM", 1)[0])
            def log(msg):
                emit(html.escape(msg) + "<br><script>(function(){var b=document.getElementById('log');if(b)b.scrollTop=b.scrollHeight;})();</script>")
            try:
                from pege_energy_sim_project_finance import pf_build_pdf
                path = pf_build_pdf(progress=log)
            except Exception:
                import traceback
                log(traceback.format_exc())
                path = None
            if path:
                PF_STORE["last_pdf"] = path
                import shutil
                if shutil.which("7z"):
                    log("Creating 7z…")
                    try:
                        from pege_energy_sim_project_finance import pf_build_7z
                        seven = pf_build_7z()
                        if seven:
                            log(f"7z: {Path(seven).name}")
                            PF_STORE["last_7z"] = seven
                    except Exception as exc:
                        log(f"7z failed: {exc}")
                else:
                    log("7z is not available. Creating ZIP instead.")
            emit("</div>")
            if path:
                emit("<p style='margin-top:12px'><a href='/project-finance/pdf-view' target='_blank' style='padding:8px 16px;background:#003399;color:#fff;text-decoration:none;border-radius:4px'>View PDF</a> ")
                emit("<a href='/project-finance/email' style='padding:8px 16px;background:#1565c0;color:#fff;text-decoration:none;border-radius:4px'>Email 7z</a> ")
                emit("<a href='/project-finance/zip' style='padding:8px 16px;background:#2e7d32;color:#fff;text-decoration:none;border-radius:4px'>Create ZIP</a></p>")
            emit("</main></body></html>")
            return
        if parsed.path == "/project-finance/pdf-status":
            from pege_energy_sim_project_finance import PDF_PROGRESS
            done = any(x == "Done" for x in PDF_PROGRESS)
            buttons = ""
            if done:
                buttons = '<a href="/project-finance/pdf-view" target="_blank" style="padding:8px 16px;background:#003399;color:#fff;text-decoration:none;border-radius:4px">View PDF</a> &nbsp; <a href="/project-finance/zip" style="padding:8px 16px;background:#2e7d32;color:#fff;text-decoration:none;border-radius:4px">Create ZIP</a> &nbsp; <a href="/project-finance/7z" style="padding:8px 16px;background:#1565c0;color:#fff;text-decoration:none;border-radius:4px">Create 7z</a>'
            self._send_json(200, {"lines": list(PDF_PROGRESS), "done": done, "buttons": buttons})
            return
        if parsed.path == "/project-finance/pdf-view":
            from pege_energy_sim_project_finance import pf_folder
            path = Path(PF_STORE.get("last_pdf") or "")
            if not path.is_file():
                project = PF_STORE.get("project") or ""
                selected = PF_STORE.get("selected_sim") or ""
                if project and selected:
                    path = pf_folder(project) / f"{selected}.pdf"
            if path.is_file():
                    data = path.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/pdf")
                    self.send_header("Content-Disposition", f'inline; filename="{path.name}"')
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
            self._send(404, page_shell("PDF", "<p>Not found.</p>"))
            return
        if parsed.path == "/project-finance/pdf-run":
            from pege_energy_sim_project_finance import pf_build_pdf
            log_lines = []
            def prog(msg):
                log_lines.append(msg)
                print(f"PDF: {msg}", flush=True)
            try:
                path = pf_build_pdf(progress=prog)
            except Exception:
                import traceback
                tb = traceback.format_exc()
                print(tb, flush=True)
                body = f"<h3>PDF creation failed</h3><pre>{html.escape(tb)}</pre><p><a href='/project-finance'>Back</a></p>"
                self._send(500, page_shell("PDF error", body))
                return
            if not path:
                self._send(400, page_shell("PDF", "<p>No project selected.</p>"))
                return
            log_html = "<br>".join(html.escape(x) for x in log_lines)
            body = f"""<h3>PDF created</h3>
<p>{log_html}</p>
<p><a href="/file?path={quote(str(path))}" target="_blank" style="padding:8px 16px;background:#003399;color:#fff;text-decoration:none;border-radius:4px">View PDF</a>
&nbsp;
<a href="/project-finance/zip" style="padding:8px 16px;background:#2e7d32;color:#fff;text-decoration:none;border-radius:4px">Create ZIP</a>
&nbsp;
<a href="/project-finance/7z" style="padding:8px 16px;background:#1565c0;color:#fff;text-decoration:none;border-radius:4px">Create 7z</a></p>
<p><a href="/project-finance">Back to project finance</a></p>"""
            self._send(200, page_shell("PDF creation", body))
            return
        if parsed.path == "/project-finance/zip":
            from pege_energy_sim_project_finance import pf_build_zip
            path = pf_build_zip()
            if not path:
                self._send(400, page_shell("ZIP", "<p>No project selected.</p>"))
                return
            data = Path(path).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{Path(path).name}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path == "/project-finance/email":
            import subprocess, shutil
            seven = PF_STORE.get("last_7z") or ""
            if not seven or not Path(seven).is_file():
                self._send(400, page_shell("Email", "<p>No 7z file. Create the PDF first.</p>"))
                return
            if shutil.which("xdg-email"):
                subprocess.Popen(["xdg-email", "--attach", seven, "mailto:?subject=Project finance"])
                self._send(200, page_shell("Email", "<p>Opening the email program with the 7z attached.</p><p><a href='/project-finance'>Back</a></p>"))
            else:
                self._send(200, page_shell("Email", f"<p>xdg-email is not available. The 7z is at:<br>{html.escape(seven)}</p>"))
            return
        if parsed.path == "/project-finance/7z":
            from pege_energy_sim_project_finance import pf_folder
            import subprocess, shutil, tempfile, os
            pf_ensure_loaded()
            project = PF_STORE.get("project") or ""
            if not project:
                self._send(400, page_shell("7z", "<p>No project selected.</p>"))
                return
            selected = PF_STORE.get("selected_sim") or ""
            if not selected:
                last = Path(PF_STORE.get("last_pdf") or "")
                selected = last.stem if last.stem else ""
            folder = pf_folder(project)
            variants = PF_STORE.get("variants") or {}
            # Collect files to include
            files = []
            for ext in (".json", ".pdf"):
                f = folder / f"{selected}{ext}"
                if f.is_file():
                    files.append((f, f"project-finance/{project}/{f.name}"))
            for key in ["BASELINE", "A", "B", "C", "D", "E"]:
                v = variants.get(key) or {}
                for sfx in ("n", "e", "p"):
                    on = str(v.get(f"obj_{sfx}") or "").strip()
                    sn = str(v.get(f"sim_{sfx}") or "").strip()
                    if not on or not sn:
                        continue
                    obj_folder = data_root() / "objects" / on
                    if not obj_folder.is_dir():
                        continue
                    for ext in (".json", ".pdf"):
                        for f in obj_folder.glob(f"*{sn}*{ext}"):
                            files.append((f, f"objects/{on}/{f.name}"))
            if not files:
                self._send(400, page_shell("7z", "<p>No files.</p>"))
                return
            out_name = f"{selected}.7z" if selected else "export.7z"
            if shutil.which("7z"):
                with tempfile.TemporaryDirectory() as td:
                    for src, arc in files:
                        dest = Path(td) / arc
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(src.read_bytes())
                    out = Path(td) / out_name
                    subprocess.run(["7z", "a", "-t7z", str(out), "."], cwd=td, check=False, capture_output=True)
                    data = out.read_bytes() if out.is_file() else b""
            else:
                # no 7z: deliver a zip with the same structure
                import zipfile, io
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                    for src, arc in files:
                        z.write(src, arc)
                data = buf.getvalue()
                out_name = out_name.replace(".7z", ".zip")
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="{out_name}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path == "/project-finance/simulate":
            PF_STORE["last_page"] = "simulate"
            pf_ensure_loaded()
            data = PF_STORE.get("data") or {}
            sim = (PF_STORE.get("selected_sim") or data.get("sim_name") or "").strip()
            project = PF_STORE.get("project") or ""
            page = pf_fill_html(SIMULATE_HTML)
            if sim:
                page += f"<p>Simulation: {html.escape(project)} — {html.escape(sim)}</p>"
            else:
                page += "<p>No simulation selected.</p>"
            self._send(200, page_shell("Project finance", page))
            return
        if parsed.path.startswith("/project-finance/"):
            page = pf_fill_html(INVEST_HTML)
            self._send(200, page_shell("Project finance", page))
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
            print(f"LOAD START: {path}")
            # if PDF, look for the JSON sidecar
            if path.suffix.lower() == ".pdf":
                path = path.with_suffix(".json")
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                self._send(404, page_shell("Load", "<p>That simulation could not be read.</p><p><a href='/form'>Back to main</a></p>"))
                return
            vals = sidecar_to_form_values(data) or {}
            # Prefer top-level sidecar location (reliable) over form_values
            # which may contain stale data from earlier state bugs.
            if data.get("name"):
                vals["name"] = str(data["name"])
            if data.get("lat") is not None:
                vals["lat"] = str(data["lat"])
            if data.get("lon") is not None:
                vals["lon"] = str(data["lon"])
            print(f"LOAD vals: name={vals.get('name')}, lat={vals.get('lat')}")
            if data.get("demand"):
                vals.update(demand_to_form_values(data["demand"]))
            vals = {str(k): "" if v is None else str(v) for k, v in vals.items()}
            FORM_STATE.update(vals)
            FORM_STATE["_last_loaded"] = str(path)
            save_state()
            self._send(200, form_html(vals))
            return
        if parsed.path == "/version-test":
            self._send(200, page_shell("Version", "<script>document.getElementById('verbox').style.display='block';</script>"))
            return
        if parsed.path == "/set-default":
            self._send(200, page_shell("Default", "<p>Use the button on the form.</p>"))
            return
        if parsed.path == "/api/state":
            self._send_json(200, FORM_STATE)
            return
        if parsed.path == "/pdf-output":
            flags = [
                # Option B: English, German, French, Spanish, Italian, then A-Z
                ("english", 362, 2), ("german", 322, 2), ("french", 42, 32),
                ("spanish", 2, 32), ("italian", 122, 32),
                ("bulgarian", 402, 2), ("chinese", 442, 2), ("croatian", 562, 64),
                ("czech", 482, 2), ("danish", 522, 2), ("greek", 562, 2),
                ("hungarian", 82, 32), ("japanese", 162, 32), ("korean", 202, 32),
                ("norwegian", 242, 32), ("polish", 282, 32), ("portugese", 322, 32),
                ("romanian", 362, 32), ("russian", 402, 32), ("slovak", 442, 32),
                ("slovenian", 482, 32), ("swedish", 522, 32), ("turkish", 562, 32),
            ]
            settings_path = HERE / "settings.json"
            current = "english"
            if settings_path.is_file():
                try:
                    current = json.loads(settings_path.read_text(encoding="utf-8")).get("pdf_language", "english")
                except Exception:
                    pass
            qs = parse_qs(parsed.query)
            if "lang" in qs:
                current = qs["lang"][0]
                try:
                    data = {}
                    if settings_path.is_file():
                        data = json.loads(settings_path.read_text(encoding="utf-8"))
                    data["pdf_language"] = current
                    settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
                except Exception:
                    pass
            items = []
            for i, (name, x, y) in enumerate(flags):
                border = "3px solid #1a5fb4" if name == current else "3px solid transparent"
                items.append(
                    f'<a href="/pdf-output?lang={name}" style="display:inline-block;margin:6px 8px;text-align:center;text-decoration:none;color:inherit;width:56px">'
                    f'<div style="width:36px;height:24px;margin:0 auto;background:url(https://www.pege.org/pic/2026-11-05-logo.png) no-repeat {-x}px {-y}px;border:{border};border-radius:2px" '
                    f'onmouseover="this.style.outline=\'2px solid #3584e4\'" onmouseout="this.style.outline=\'\'"></div>'
                    f'<div style="font-size:11px;margin-top:2px">{name}</div></a>'
                )
                if i == 11:
                    items.append("<br>")
            body = f"<h2>PDF output</h2><p>Current language: <b>{html.escape(current)}</b></p>" + "".join(items)
            body += '<p style="margin-top:16px"><a href="/form">Back to main</a></p>'
            self._send(200, page_shell("PDF output", body))
            return
        if parsed.path == "/about":
            logo = '<p><a href="https://www.pege.org/" target="_blank"><img src="/pege-logo.png" alt="PEGE" width="420" height="120" style="width:420px;height:120px;background:#fff"></a></p>'
            body = logo
            body += f"<p><b>Current version</b> {html.escape(LOCAL_VERSION)} &nbsp; Built {html.escape(BUILD_VERSION)}</p>"
            remote = remote_version(5.0)
            newer = False
            if remote:
                try:
                    newer = float(remote) > float(LOCAL_VERSION)
                except ValueError:
                    newer = False
            view = (parse_qs(parsed.query).get("view") or ["local"])[0]
            active = "remote" if view == "remote" else "local"
            body += '<p style="margin:12px 0">'
            body += f'<a href="/about?view=local" style="padding:6px 12px;margin-right:8px;border-radius:4px;text-decoration:none;background:{"#1a5fb4" if active=="local" else "#e0e0e0"};color:{"#fff" if active=="local" else "#333"}">this computer</a>'
            body += f'<a href="/about?view=remote" style="padding:6px 12px;border-radius:4px;text-decoration:none;background:{"#1a5fb4" if active=="remote" else "#e0e0e0"};color:{"#fff" if active=="remote" else "#333"}">Github</a>'
            body += '</p>'
            if view == "remote":
                local_readme = ""
                remote_readme = ""
                try:
                    import urllib.request
                    req = urllib.request.Request("https://raw.githubusercontent.com/pegefounder/energy-sim/main/README.md", headers={"User-Agent": "PEGE-energy-sim"})
                    with urllib.request.urlopen(req, timeout=8) as resp:
                        remote_readme = resp.read().decode("utf-8", errors="replace")
                except Exception:
                    remote_readme = ""
                local_path = HERE / "README.md"
                if local_path.is_file():
                    local_readme = local_path.read_text(encoding="utf-8")
                same = (local_readme.strip() == remote_readme.strip()) if (local_readme and remote_readme) else False
                status = "same" if same else "different"
                color = "#2e7d32" if same else "#c62828"
                body += f'<p style="margin:4px 0 12px;color:{color};font-weight:bold">{status}</p>'
            body += "<h2>README</h2>"
            readme_text = ""
            if view == "remote" or not (HERE / "README.md").is_file():
                try:
                    import urllib.request
                    req = urllib.request.Request("https://raw.githubusercontent.com/pegefounder/energy-sim/main/README.md", headers={"User-Agent": "PEGE-energy-sim"})
                    with urllib.request.urlopen(req, timeout=8) as resp:
                        readme_text = resp.read().decode("utf-8", errors="replace")
                except Exception:
                    readme_text = ""
            if not readme_text:
                local = HERE / "README.md"
                if local.is_file():
                    readme_text = local.read_text(encoding="utf-8")
            if readme_text:
                lines = readme_text.splitlines()
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
<p style="margin-top:1em"><a href="https://pege.org/energy-sim/" target="_blank">https://pege.org/energy-sim/</a></p>
<div style="text-align:right;margin-top:1.2em">
<form action="https://www.paypal.com/cgi-bin/webscr" method="post" target="_blank" style="display:inline">
<input type="hidden" name="cmd" value="_s-xclick">
<input type="hidden" name="hosted_button_id" value="XNMPYU3R396Z8">
<button type="submit" name="submit" style="width:120px;height:47px;background:url(https://www.pege.org/pic/2026-11-05-logo.png) no-repeat -210px -60px;border:none;cursor:pointer;text-indent:-9999px;">Donate</button>
</form>
</div>
<p><a href="/">Back to main</a></p>
"""
            self._send(200, page_shell("About", body))
            return
        if parsed.path in ("/", "/form"):
            qs = parse_qs(parsed.query, keep_blank_values=True)
            vals = FORM_STATE.copy()
            if parsed.query:
                vals.update(form_values_from_qs(qs))
            elif LAST_FORM:
                vals.update(LAST_FORM)
            self._send(200, form_html(vals))
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

        if parsed.path == "/api/project-finance":
            qs = parse_qs(parsed.query)
            project = (qs.get("project") or [""])[0]
            sim = (qs.get("sim") or [""])[0]
            if not project:
                self._send_json(200, {"data": {}})
                return
            # do not overwrite the store if it already holds this project
            if PF_STORE.get("project") == project and PF_STORE.get("data"):
                data = PF_STORE["data"]
            else:
                data = pf_load(project, sim if sim else None)
                if data:
                    PF_STORE["data"] = data
            if sim:
                PF_STORE["selected_sim"] = sim
            self._send_json(200, {"project": project, "sim": sim, "data": data})
            return
        if parsed.path == "/api/projects":
            root = data_root() / "project-finance"
            projects = []
            if root.exists():
                for d in sorted(root.iterdir()):
                    if d.is_dir():
                        import re as _re
                        def _sim_key(stem):
                            m = _re.match(r"\d{4}-\d{2}-\d{2} \d{4} (.+)", stem)
                            return (m.group(1).lower() if m else stem.lower(), stem)
                        sims = sorted((f.stem for f in d.glob("*.json") if f.stem != d.name), key=_sim_key)
                        projects.append({"name": d.name, "sims": sims})
            self._send_json(200, {"projects": projects})
            return
        if parsed.path == "/api/workspace":
            qs = parse_qs(parsed.query)
            outdir = (qs.get("outdir") or [""])[0]
            parent = Path(outdir) if outdir else data_root()
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
                info = {"name": stem, "path": str(chosen)}
                js = pair.get(".json")
                if js and js.is_file():
                    try:
                        side = json.loads(js.read_text(encoding="utf-8"))
                        info["lat"] = side.get("lat")
                        info["lon"] = side.get("lon")
                        info["locname"] = side.get("name") or ""
                        info["simname"] = side.get("simname") or ""
                    except Exception:
                        pass
                files.append(info)
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
            from pege_energy_sim_common import compares_root
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
            stamp = datetime.now().strftime("%Y-%m-%d %H%M")
            built = f"{obj1} {stamp} - {stem1} vs {side(obj2, stem2, obj1, stem1)}.pdf"
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
                from pege_energy_sim_compare_houses import compare_reports
                set_language(_pdf_lang())
                compare_reports(path_a, path_b, out, label_a=f"{obj1} {stem1}", label_b=f"{obj2} {stem2}", log=log)
            except Exception as exc:
                import traceback
                emit(f"<span class='err'>{html.escape(str(exc))}</span><br>")
                emit(f"<pre>{html.escape(traceback.format_exc())}</pre></div>")
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
        if parsed.path == "/delete-files":
            qs = parse_qs(parsed.query)
            raw = (qs.get("files") or [""])[0]
            names = [n for n in raw.split("|") if n]
            roots = [data_root() / "objects", data_root() / "project-finance"]
            deleted = 0
            for name in names:
                for root in roots:
                    base = root / name
                    for ext in (".pdf", ".json"):
                        pair = Path(str(base) + ext)
                        if pair.is_file():
                            pair.unlink()
                            deleted += 1
            back = (qs.get("back") or ["/files"])[0]
            self.send_response(302)
            self.send_header("Location", back)
            self.end_headers()
            return
        if parsed.path == "/rerun":
            self._handle_rerun()
            return
        # Serve object and project-finance files linked from PDFs
        if parsed.path.startswith("/objects/") or parsed.path.startswith("/project-finance/"):
            rel = unquote(parsed.path.lstrip("/"))
            path = data_root() / rel
            if path.is_file():
                data = path.read_bytes()
                ctype = "application/pdf" if path.suffix == ".pdf" else "application/octet-stream"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
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
            from pege_energy_sim_common import build_report
            try:
                lang = fv.get("pdf_language") or ""
                if not lang:
                    settings = Path(__file__).with_name("settings.json")
                    if settings.is_file():
                        try:
                            lang = json.loads(settings.read_text(encoding="utf-8")).get("pdf_language") or ""
                        except Exception:
                            pass
                lang = lang or _pdf_lang()
                set_language(lang)
                # Parse PV arrays from form values
                pv_list = []
                for i in range(1, 26):
                    sl = fv.get(f"slope{i}")
                    az = fv.get(f"az{i}")
                    wp = fv.get(f"wp{i}")
                    label = fv.get(f"pvname{i}")
                    if not sl or not az or not wp:
                        continue
                    try:
                        slope = float(str(sl).replace(",", "."))
                        azimuth = float(str(az).replace(",", "."))
                        watts = float(str(wp).replace(",", "."))
                    except ValueError:
                        continue
                    if watts <= 0:
                        continue
                    dist_s = fv.get(f"dist{i}")
                    height_s = fv.get(f"height{i}")
                    wall_d = wall_h = None
                    if dist_s and height_s:
                        try:
                            wall_d = int(float(str(dist_s).replace(",", ".")))
                            wall_h = int(float(str(height_s).replace(",", ".")))
                        except ValueError:
                            pass
                    bifacial = str(fv.get(f"bifacial{i}", "")).lower() in {"1", "on", "yes", "true"}
                    bipct_s = fv.get(f"bifaciality{i}")
                    bifaciality = None
                    if bipct_s:
                        try:
                            bifaciality = float(str(bipct_s).replace(",", "."))
                            if bifaciality > 1.5:
                                bifaciality = bifaciality / 100.0
                        except ValueError:
                            pass
                    elif bifacial:
                        bifaciality = 0.80
                    pv_list.append((label or f"PV {i}", slope, azimuth, watts, wall_d, wall_h, bifacial, bifaciality))
                # Prefer top-level sidecar fields (reliable) over form_values
                # (which may contain stale data from earlier state bugs).
                name = old_data.get("name") or fv.get("name") or ""
                lat = float(old_data.get("lat") or fv.get("lat") or 0)
                lon = float(old_data.get("lon") or fv.get("lon") or 0)
                log(f"  location: {name}  {lat}  {lon}")
                new_pdf = build_report(
                    lang=lang,
                    name=name,
                    lat=lat,
                    lon=lon,
                    pv_list=pv_list,
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
                    demand=parse_demand_fields(fv) if fv else old_data.get("demand"),
                )
            except Exception as exc:
                log(f"  run failed: {exc}")
                continue
            results.append((js, new_pdf, old_data))
            log(f"  wrote {new_pdf.name}")
        # Write the difference report
        if results:
            from pege_energy_sim_common import compares_root
            stamp = datetime.now().strftime("%Y-%m-%d %H%M")
            out = compares_root(data_root()) / f"{stamp} - difference report from rerun.pdf"
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
        if parsed.path == "/api/state":
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            if raw:
                new_data = json.loads(raw)
                print(f"STATE: slope1={new_data.get('slope1')}")
                FORM_STATE.update(new_data)
                save_state()
            self._send_json(200, {"ok": True})
            return
        # Field blur / change report — store in memory immediately
        if parsed.path == "/project-finance/field":
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            data = parse_form(raw, self.headers.get("Content-Type", ""))
            page = (data.get("page") or data.get("letter") or "general").lower()
            field = data.get("field") or data.get("name") or ""
            value = data.get("value") or ""
            project = data.get("project") or PF_STORE.get("project") or ""
            if project:
                PF_STORE["project"] = project
            if field:
                if page == "baseline":
                    PF_STORE.setdefault("variants", {}).setdefault("BASELINE", {})[field] = value
                elif page in "abcde":
                    PF_STORE.setdefault("variants", {}).setdefault(page.upper(), {})[field] = value
                else:
                    PF_STORE.setdefault("data", {})[field] = value
            self._send_json(200, {"ok": True})
            return
        if parsed.path == "/project-finance/save":
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            data = parse_form(raw, self.headers.get("Content-Type", ""))
            project = data.get("project") or PF_STORE.get("project") or ""
            sim = data.get("sim") or ""
            mode = data.get("mode") or "new"
            if not project or not sim:
                self._send(400, b"Project and simulation name required")
                return
            PF_STORE["project"] = project
            PF_STORE["sim"] = sim
            folder = pf_folder(project)
            if mode == "keep":
                # Overwrite the currently selected simulation (keeps its timestamp)
                sel = PF_STORE.get("selected_sim") or ""
                if sel:
                    path = folder / f"{sel}.json"
                    if not path.is_file():
                        path = folder / f"{datetime.now().strftime('%Y-%m-%d %H%M')} - {pf_safe(sim)}.json"
                else:
                    candidates = sorted(folder.glob(f"* - {pf_safe(sim)}.json"), key=lambda p: p.stat().st_mtime)
                    path = candidates[-1] if candidates else folder / f"{datetime.now().strftime('%Y-%m-%d %H%M')} - {pf_safe(sim)}.json"
            else:
                stamp = datetime.now().strftime("%Y-%m-%d %H%M")
                path = folder / f"{stamp} - {pf_safe(sim)}.json"
            fv = dict(PF_STORE.get("data") or {})
            variants = dict(PF_STORE.get("variants") or {})
            base = dict(variants.get("BASELINE") or {})
            for k in ("obj_n","sim_n","obj_e","sim_e","obj_p","sim_p","base_desc","baseline_description","inc_inc"):
                if base.get(k) and not fv.get(k):
                    fv[k] = base[k]
            # Do not let an empty form value wipe a stored variant value
            for k in list(base.keys()):
                if not base.get(k) and fv.get(k):
                    base[k] = fv[k]
            variants["BASELINE"] = base
            payload = {"model": project, "simulation": sim, "form_values": fv, "variants": variants}
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            PF_STORE["selected_sim"] = path.stem
            # simple changes list against previous file if any
            changes = []
            prev = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime)
            prev = [p for p in prev if p != path]
            if prev:
                try:
                    old = json.loads(prev[-1].read_text(encoding="utf-8"))
                    old_fv = old.get("form_values") or {}
                    for k, v in sorted((payload.get("form_values") or {}).items()):
                        ov = str(old_fv.get(k, ""))
                        nv = str(v)
                        if ov != nv and (ov or nv):
                            changes.append((k, ov, nv))
                    old_var = old.get("variants") or {}
                    new_var = payload.get("variants") or {}
                    for page in sorted(set(old_var) | set(new_var)):
                        ovp = old_var.get(page) or {}
                        nvp = new_var.get(page) or {}
                        for k in sorted(set(ovp) | set(nvp)):
                            ov = str(ovp.get(k, ""))
                            nv = str(nvp.get(k, ""))
                            if ov != nv and (ov or nv):
                                changes.append((f"{page}.{k}", ov, nv))
                except Exception:
                    pass
            rows = "".join(f"<tr><td>{html.escape(k)}</td><td>{html.escape(o)}</td><td>{html.escape(n)}</td></tr>" for k, o, n in changes)
            body = f"""<h2>Saved: {html.escape(path.name)}</h2>
<p>{len(changes)} change(s)</p>
<table style="border-collapse:collapse;width:100%">
<tr style="background:#f0f0f0"><th style="text-align:left;padding:6px;border:1px solid #ddd">Field</th><th style="text-align:left;padding:6px;border:1px solid #ddd">Old</th><th style="text-align:left;padding:6px;border:1px solid #ddd">New</th></tr>
{rows}
</table>
<p><a href="/project-finance?view=general">Back to project finance</a></p>"""
            self._send(200, page_shell("Saved", body))
            return
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            data = parse_form(raw, self.headers.get("Content-Type", ""))
            page = data.get("page") or "general"
            field = data.get("field") or ""
            value = data.get("value") or ""
            project = data.get("project") or PF_STORE.get("project") or ""
            if project:
                PF_STORE["project"] = project
            if field:
                if page == "baseline":
                    PF_STORE.setdefault("variants", {}).setdefault("BASELINE", {})[field] = value
                elif page in "abcde":
                    PF_STORE.setdefault("variants", {}).setdefault(page.upper(), {})[field] = value
                else:
                    PF_STORE.setdefault("data", {})[field] = value
            self._send_json(200, {"ok": True})
            return
        # Object house form flush — keep LAST_FORM when the user leaves the page
        if parsed.path == "/form-flush":
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            fields = parse_form(raw, self.headers.get("Content-Type", ""))
            if fields:
                LAST_FORM.clear()
                LAST_FORM.update({str(k): v[0] if isinstance(v, list) else str(v) for k, v in fields.items()})
            self._send_json(200, {"ok": True})
            return
        # Save configuration
        if parsed.path == "/save-config":
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            fields = parse_form(raw, self.headers.get("Content-Type", ""))
            job = parse_job(fields)
            mode = fields.get("mode", [""])[0] if isinstance(fields.get("mode"), list) else fields.get("mode", "")
            ofolder = object_folder(job["outdir"], job["object"])
            ofolder.mkdir(parents=True, exist_ok=True)
            simname = job.get("simname") or "config"
            loc = job.get("name") or ""
            lat = job.get("lat") or ""
            lon = job.get("lon") or ""
            locpart = f" - {loc} - {lat} - {lon}" if loc else ""
            if mode == "new":
                stamp = datetime.now().strftime("%Y-%m-%d %H%M")
                json_path = ofolder / f"{stamp} - {simname}{locpart}.json"
            else:
                candidates = list(ofolder.glob(f"* {simname}*.json"))
                json_path = candidates[0] if candidates else ofolder / f"{datetime.now().strftime('%Y-%m-%d %H%M')} - {simname}{locpart}.json"
            vals = job_to_form_values(job)
            remark_text = first(fields, "locremark")[:2000]
            if remark_text:
                location_remark(job["lat"], job["lon"], cache_dir=data_root(), text=remark_text)
                vals["locremark"] = remark_text
            print(f"SAVE: slope1={vals.get('slope1')}")
            old_vals = {}
            last_path = FORM_STATE.get("_last_loaded")
            if not last_path:
                loc = job.get("name") or ""
                pattern = f"* {simname}*{loc}*.json" if loc else f"* {simname}*.json"
                existing = sorted(ofolder.glob(pattern), key=lambda p: p.stat().st_mtime)
                if existing:
                    last_path = str(existing[-1])
            print(f"COMPARE: last_path={last_path}")
            compare_path = Path(last_path) if last_path else json_path
            print(f"COMPARE: compare_path={compare_path}, exists={compare_path.exists()}")
            if compare_path.exists():
                try:
                    old_data = json.loads(compare_path.read_text(encoding="utf-8"))
                    print(f"COMPARE: keys={list(old_data.keys())[:5]}")
                    old_vals = old_data.get("form_values", {})
                    print(f"COMPARE: old_vals count={len(old_vals)}")
                    print(f"COMPARE: old slope1={old_vals.get('slope1')}")
                except Exception as e:
                    print(f"COMPARE: error={e}")
                    pass
            changes = []
            for k, v in sorted(vals.items()):
                old = str(old_vals.get(k, ""))
                new = str(v)
                if old != new and (old or new):
                    changes.append((k, old, new))
            sidecar = {
                "object": job["object"],
                "software_version": LOCAL_VERSION,
                "name": job.get("name") or "",
                "lat": job.get("lat") or "",
                "lon": job.get("lon") or "",
                "form_values": vals,
            }
            json_path.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False), encoding="utf-8")
            FORM_STATE.update(vals)
            FORM_STATE["_last_loaded"] = str(json_path)
            LAST_FORM.clear()
            LAST_FORM.update(vals)
            save_state()
            rows = "".join(f"<tr><td>{html.escape(k)}</td><td>{html.escape(o)}</td><td>{html.escape(n)}</td></tr>" for k, o, n in changes)
            body = f"""<h2>Saved: {html.escape(json_path.name)}</h2>
<p>{len(changes)} change(s)</p>
<table style="border-collapse:collapse;width:100%">
<tr style="background:#f0f0f0"><th style="text-align:left;padding:6px;border:1px solid #ddd">Field</th><th style="text-align:left;padding:6px;border:1px solid #ddd">Old</th><th style="text-align:left;padding:6px;border:1px solid #ddd">New</th></tr>
{rows}
</table>
<p><a href="/form">Back to form</a></p>"""
            self._send(200, page_shell("Saved", body))
            return
        # Flush on leaving page or main menu change — write default JSON
        if parsed.path == "/project-finance/flush":
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            data = parse_form(raw, self.headers.get("Content-Type", ""))
            project = data.get("project") or PF_STORE.get("project") or ""
            letter = (data.get("letter") or "").upper()
            if project:
                PF_STORE["project"] = project
                if letter in "ABCDE" or letter == "BASELINE":
                    store = PF_STORE.setdefault("variants", {}).setdefault(letter, {})
                else:
                    store = PF_STORE.setdefault("data", {})
                for k, v in data.items():
                    if k in ("project", "sim", "letter"):
                        continue
                    if v == "" and store.get(k):
                        continue
                    store[k] = v
                pf_save_default()
            self._send_json(200, {"ok": True})
            return
        if parsed.path == "/project-finance/baseline":
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            data = parse_form(raw, self.headers.get("Content-Type", ""))
            # drop garbage
            data = {k: v for k, v in data.items() if "\r" not in k and not k.startswith("------")}
            project = data.get("model_name") or PF_STORE.get("project") or ""
            if project:
                PF_STORE["project"] = project
                PF_STORE.setdefault("data", {}).update(data)
                pf_save_default()
            self._send(200, page_shell("Project finance", pf_fill_html(BASELINE_HTML)))
            return
        if parsed.path == "/project-finance/set-default":
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            ct = self.headers.get("Content-Type", "")
            data = parse_form(raw, ct)
            model = (data.get("model_name") or PF_STORE.get("project") or "").strip()
            if not model:
                self._send(200, page_shell("Project finance", INVEST_HTML + "<p>No project name. Enter a project name first.</p>"))
                return
            PF_STORE["project"] = model
            PF_STORE["data"] = data
            pf_save_default()
            msg = '<p>Default configuration saved for ' + html.escape(model) + '.</p><p><a href="/project-finance?view=general">Back to Project finance</a></p>'
            self._send(200, page_shell("Project finance", INVEST_HTML + msg))
            return

        parsed = urlparse(self.path)
        if parsed.path == "/project-finance":
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            fields = parse_qs(raw, keep_blank_values=True)
            data = {str(k): v[0] if isinstance(v, list) else str(v) for k, v in fields.items()}
            model = (data.get("model_name") or PF_STORE.get("project") or "investment").strip()
            sim = (data.get("sim_name") or PF_STORE.get("sim") or "simulation").strip()
            PF_STORE["project"] = model
            PF_STORE["sim"] = sim
            PF_STORE["data"] = data
            pf_save_default()
            stem = pf_save_simulation(sim) if sim and sim != "simulation" else None
            msg = f"<p>Saved default for {html.escape(model)}</p>"
            if stem:
                msg += f"<p>simulation {html.escape(stem)}.json</p>"
            self._send(200, page_shell("Project finance", INVEST_HTML + msg))
            return
        if urlparse(self.path).path == "/set-default":
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            fields = parse_qs(raw, keep_blank_values=True)
            vals = {str(k): v[0] if isinstance(v, list) else str(v) for k, v in fields.items()}
            obj_name = (vals.get("object") or "").strip()
            if obj_name:
                ofolder = object_folder(data_root(), obj_name)
                # bare {object}.json is abandoned
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
        # bare {object}.json is abandoned; do not write it
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
                # bare {object}.json is abandoned
                log(f"Results folder: {ofolder}")
                result_folder = ofolder
                log(f"Results folder: {ofolder}")
            set_language(_pdf_lang())
            pdf_path = build_report(
                lang=_pdf_lang(),
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


def ensure_base_folders() -> None:
    root = data_root()
    for name in ("objects", "compares", "locations", "project-finance"):
        path = root / name
        path.mkdir(parents=True, exist_ok=True)

def main(argv: list[str] | None = None) -> int:
    ensure_base_folders()
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
            print("Press Enter to close this window.")
            print("Press any other key to retry.")
            print("=" * 62 + "\n")
            try:
                import termios, tty
                fd = sys.stdin.fileno()
                old = termios.tcgetattr(fd)
                try:
                    tty.setraw(fd)
                    key = sys.stdin.read(1)
                finally:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old)
                if key not in ("\r", "\n", "\x03"):
                    print()
                    return main()
            except (KeyboardInterrupt, EOFError, Exception):
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
