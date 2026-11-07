#!/usr/bin/env python3
"""Project finance pages and storage."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime
from pathlib import Path
from pege_energy_sim_common import data_root, parse_form

PF_STORE: dict = {"project": "", "sim": "", "data": {}, "variants": {}}

def pf_safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in " -_" else "_" for c in (name or "")).strip()

def pf_folder(project: str) -> Path:
    safe = pf_safe(project)
    folder = data_root() / "project-finance" / safe
    folder.mkdir(parents=True, exist_ok=True)
    return folder

def pf_default_path(project: str) -> Path:
    safe = pf_safe(project)
    return pf_folder(project) / f"{safe}.json"

def pf_ensure_default(project: str) -> Path:
    # Bare {project}.json is abandoned; return the folder only.
    return pf_folder(project)


def pf_ensure_loaded() -> None:
    """If the store is empty, load the most recently used project from disk."""
    if PF_STORE.get("data") and PF_STORE.get("project"):
        return
    root = data_root() / "project-finance"
    if not root.is_dir():
        return
    folders = [d for d in root.iterdir() if d.is_dir()]
    if not folders:
        return
    # most recently modified folder
    folders.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    project = folders[0].name
    PF_STORE["project"] = project
    PF_STORE["data"] = pf_load(project)

def pf_load(project: str, sim: str | None = None) -> dict:
    folder = pf_folder(project)
    path = None
    if sim:
        candidates = list(folder.glob(f"* {sim}.json")) + list(folder.glob(f"{sim}.json"))
        if candidates:
            path = candidates[0]
    if path is None:
        # Prefer the most recently modified simulation file over the abandoned bare project.json
        sims = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        path = sims[0] if sims else pf_default_path(project)
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            data = raw.get("form_values") or {}
            variants = raw.get("variants") or {}
            # general fields may have landed in the empty variant; merge them
            empty = variants.pop("", None)
            if isinstance(empty, dict):
                for k, v in empty.items():
                    if k not in data or not data.get(k):
                        data[k] = v
            # baseline is a variant; move its fields if they are still in form_values
            base_keys = [k for k in data if k[:3] in ("obj", "sim", "inc", "exp", "el_", "fos") or k in ("base_money", "years_pension", "pens_dec", "baseline_description", "inc_inc") or k.startswith("stress_") or k.endswith("_description")]
            if "BASELINE" not in variants:
                variants["BASELINE"] = {k: data[k] for k in base_keys if k in data}
            PF_STORE["variants"] = variants
            PF_STORE["data"] = data
            return data
        except Exception:
            pass
    return {}

def pf_save_default() -> None:
    project = PF_STORE.get("project") or ""
    if not project:
        return
    sim = (PF_STORE.get("sim") or "").strip()
    if not sim or sim in ("default", "simulation"):
        return
    folder = pf_folder(project)
    stamp = datetime.now().strftime("%Y-%m-%d %H%M")
    path = folder / f"{stamp} {pf_safe(sim)}.json"
    data = PF_STORE.get("data") or {}
    clean = {k: v for k, v in data.items() if "\r" not in str(k) and not str(k).startswith("------") and "WebKit" not in str(k) and k not in ("simPick", "projPick")}
    PF_STORE["data"] = clean
    variants = {k: v for k, v in (PF_STORE.get("variants") or {}).items() if k in "ABCDE" or k == "BASELINE"}
    for letter in "ABCDE":
        variants.setdefault(letter, {})
    variants.setdefault("BASELINE", {})
    payload = {"model": project, "simulation": sim, "form_values": clean, "variants": variants}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    PF_STORE["selected_sim"] = f"{stamp} {pf_safe(sim)}"

def pf_save_simulation(sim_name: str | None = None) -> str | None:
    project = PF_STORE.get("project") or ""
    if not project:
        return None
    sim = (sim_name or PF_STORE.get("sim") or "").strip() or "simulation"
    folder = pf_folder(project)
    stamp = datetime.now().strftime("%Y-%m-%d %H%M")
    stem = f"{stamp} {pf_safe(sim)}"
    path = folder / f"{stem}.json"
    data = dict(PF_STORE.get("data") or {})
    variants = dict(PF_STORE.get("variants") or {})
    empty = variants.pop("", None)
    if isinstance(empty, dict):
        for k, v in empty.items():
            if k not in data or not data.get(k):
                data[k] = v
    # always keep all five letters so the file shows the full set
    for letter in "ABCDE":
        variants.setdefault(letter, {})
    payload = {"model": project, "simulation": sim, "form_values": data, "variants": variants}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return stem

def pf_save() -> None:
    pf_save_default()



def pf_complete_selects(page: str, stored: dict) -> str:
    """Replace object and simulation selects with complete lists and the stored selection.
    The page arrives ready; the browser does not have to load anything."""
    import re
    root = data_root() / "objects"
    objects = sorted([d.name for d in root.iterdir() if d.is_dir()]) if root.is_dir() else []
    for sfx in ("n", "e", "p"):
        obj_key = f"obj_{sfx}"
        sim_key = f"sim_{sfx}"
        want_obj = str(stored.get(obj_key) or "")
        want_sim = str(stored.get(sim_key) or "")
        obj_opts = ['<option value="">— select object —</option>']
        for name in objects:
            sel = " selected" if name == want_obj else ""
            obj_opts.append(f'<option value="{html.escape(name)}"{sel}>{html.escape(name)}</option>')
        obj_html = f'<select name="{obj_key}" style="width:100%">{"".join(obj_opts)}</select>'
        page = re.sub(
            rf'<select name="{obj_key}"[^>]*>.*?</select>',
            obj_html,
            page,
            count=1,
            flags=re.DOTALL,
        )
        sims = []
        if want_obj:
            folder = root / want_obj
            if folder.is_dir():
                stems = set()
                for pth in folder.iterdir():
                    if pth.suffix.lower() in {".pdf", ".json"} and pth.stem != folder.name:
                        if not pth.name.startswith("compare ") and " vs " not in pth.name:
                            stems.add(pth.stem)
                sims = sorted(stems)
        sim_opts = ['<option value="">— simulation —</option>']
        for name in sims:
            sel = " selected" if name == want_sim else ""
            sim_opts.append(f'<option value="{html.escape(name)}"{sel}>{html.escape(name)}</option>')
        if want_sim and want_sim not in sims:
            sim_opts.append(f'<option value="{html.escape(want_sim)}" selected>{html.escape(want_sim)}</option>')
        sim_html = f'<select name="{sim_key}" style="width:100%;margin-top:4px">{"".join(sim_opts)}</select>'
        page = re.sub(
            rf'<select name="{sim_key}"[^>]*>.*?</select>',
            sim_html,
            page,
            count=1,
            flags=re.DOTALL,
        )
    return page


def pf_inject_selects(html: str, stored: dict) -> str:
    """Put stored object and simulation values into the selects as selected options."""
    for key, val in (stored or {}).items():
        if not val or not (key.startswith("obj_") or key.startswith("sim_")):
            continue
        # find the select and add a selected option
        import re
        def repl(m, k=key, v=str(val)):
            tag = m.group(0)
            if f'name="{k}"' not in tag and f"name='{k}'" not in tag:
                return tag
            # remove existing selected
            tag = re.sub(r'\s+selected', '', tag)
            # add the stored option as selected if not present
            if f'value="{v}"' not in tag:
                tag = tag.replace('</select>', f'<option value="{v}" selected>{v}</option></select>')
            else:
                tag = tag.replace(f'value="{v}"', f'value="{v}" selected')
            return tag
        html = re.sub(r'<select[^>]*>.*?</select>', repl, html, flags=re.DOTALL)
    return html

def pf_fill_html(html: str, values: dict | None = None) -> str:
    """Replace value= attributes and selected options with stored values."""
    if not values:
        values = PF_STORE.get("data") or {}
    if not values:
        return html
    def repl_input(m):
        tag = m.group(0)
        name_m = re.search(r'name=["\']([^"\']+)["\']', tag)
        if not name_m:
            return tag
        name = name_m.group(1)
        if name not in values:
            return tag
        val = str(values[name]).replace('"', '"')
        if re.search(r'value=["\'][^"\']*["\']', tag):
            return re.sub(r'value=["\'][^"\']*["\']', f'value="{val}"', tag, count=1)
        # insert value before the closing >
        return tag[:-1] + f' value="{val}">'
    html = re.sub(r'<input[^>]*>', repl_input, html)
    # textareas
    def repl_ta(m):
        tag = m.group(0)
        name_m = re.search(r'name=["\']([^"\']+)["\']', tag)
        if not name_m or name_m.group(1) not in values:
            return tag
        val = str(values[name_m.group(1)])
        return re.sub(r'>.*?</textarea>', '>' + val.replace('<','<') + '</textarea>', tag, count=1, flags=re.DOTALL)
    html = re.sub(r'<textarea[^>]*>.*?</textarea>', repl_ta, html, flags=re.DOTALL)
    # selects: mark the matching option selected
    def repl_select(m):
        tag = m.group(0)
        name_m = re.search(r'name=["\']([^"\']+)["\']', tag)
        if not name_m:
            return tag
        name = name_m.group(1)
        if name not in values:
            return tag
        want = str(values[name])
        # remove existing selected
        tag = re.sub(r'\s+selected', '', tag)
        # add selected to the matching option
        def opt(o):
            val_m = re.search(r'value=["\']([^"\']*)["\']', o.group(0))
            if val_m and val_m.group(1) == want:
                return o.group(0)[:-1] + ' selected>'
            return o.group(0)
        return re.sub(r'<option[^>]*>', opt, tag)
    html = re.sub(r'<select[^>]*>.*?</select>', repl_select, html, flags=re.DOTALL)
    # textareas
    def repl_ta(m):
        tag = m.group(0)
        name_m = re.search(r'name=["\']([^"\']+)["\']', tag)
        if not name_m or name_m.group(1) not in values:
            return tag
        val = str(values[name_m.group(1)])
        return re.sub(r'>.*?</textarea>', f'>{val}</textarea>', tag, count=1, flags=re.DOTALL)
    html = re.sub(r'<textarea[^>]*>.*?</textarea>', repl_ta, html, flags=re.DOTALL)
    return html


INVEST_HTML = """
<h2>Project finance</h2>
<nav style="margin-bottom:14px">
  <a href="/project-finance?view=general" class="nav" style="margin-right:14px">General</a><a href="/project-finance/baseline" class="nav" style="margin-right:14px">Baseline</a><a href="/project-finance/a" class="nav" style="margin-right:14px">A</a><a href="/project-finance/b" class="nav" style="margin-right:14px">B</a><a href="/project-finance/c" class="nav" style="margin-right:14px">C</a><a href="/project-finance/d" class="nav" style="margin-right:14px">D</a><a href="/project-finance/e" class="nav" style="margin-right:14px">E</a><a href="/project-finance/simulate" class="nav" style="margin-right:14px">Simulate</a><a href="/project-finance/pdf" class="nav">PDF creation</a>
</nav>
<script>
// Track focused field; update links with page/field/value; report on focus change
(function() {
  let focused = null;
  let lastValue = "";
  function pageName() {
    const path = location.pathname.split("/").pop() || "general";
    return path === "project-finance" ? "general" : path;
  }
  function updateLinks() {
    if (!focused || !focused.name) return;
    const params = new URLSearchParams();
    params.set("page", pageName());
    params.set("field", focused.name);
    params.set("value", focused.value || "");
    const qs = params.toString();
    document.querySelectorAll("nav a").forEach(a => {
      const base = a.href.split("?")[0];
      a.href = base + "?" + qs;
    });
  }
  function reportField() {
    if (!focused || !focused.name) return;
    const proj = document.getElementById("projName");
    const project = (proj && proj.value) || localStorage.getItem("pf_project") || "";
    if (!project) return;
    const body = "project=" + encodeURIComponent(project) + "&page=" + encodeURIComponent(pageName()) + "&field=" + encodeURIComponent(focused.name) + "&value=" + encodeURIComponent(focused.value || "");
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/project-finance/field", false);
    xhr.setRequestHeader("Content-Type", "application/x-www-form-urlencoded");
    xhr.send(body);
  }
  document.addEventListener("focusin", e => {});
  document.addEventListener("input", e => {});
  document.addEventListener("focusout", e => {});
  document.addEventListener("click", e => {});
})();
</script>

<style>
.nav.on { background:#003399; color:#fff; padding:4px 10px; border-radius:4px; }
</style>
<script>
document.addEventListener("DOMContentLoaded", () => {
  const path = location.pathname;
  document.querySelectorAll(".nav").forEach(a => {
    const href = a.getAttribute("href") || "";
    const base = href.split("?")[0];
    if (base === path || (path === "/project-finance" && base === "/project-finance")) a.classList.add("on");
  });
});
</script>

<form method="post" action="/project-finance" enctype="application/x-www-form-urlencoded">
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px">
    <div>
      <label>Select project</label><br>
      <select id="projPick" style="width:100%"><option value="">— select —</option></select>
    </div>
    <div>
      <label>Select simulation</label><br>
      <select id="simPick" style="width:100%"><option value="">— select —</option></select>
    </div>
    <div>
      <label>Project name</label><br>
      <input name="model_name" id="projName" value="5 variants for my house improvement" style="width:100%">
    </div>
    <div>
      <label>Simulation name</label><br>
      <input name="sim_name" id="simName" value="" style="width:100%">
    </div>

  </div>

  <fieldset style="border:1px solid #c5cdd8;border-radius:8px;padding:12px 16px;margin-bottom:16px">
    <legend style="font-weight:600;padding:0 6px">Money and time</legend>
    <div style="display:flex;gap:16px;flex-wrap:wrap">
      <div style="flex:1;min-width:120px"><label>Bank interest %</label><br><input name="bank_int" value="0.5" style="width:100%"></div>
      <div style="flex:1;min-width:120px"><label>Credit interest %</label><br><input name="credit_int" value="4" style="width:100%"></div>
      <div style="flex:1;min-width:120px"><label>General inflation %</label><br><input name="inf_gen" value="2.5" style="width:100%"></div>
      <div style="flex:1;min-width:120px"><label>Electricity inflation %</label><br><input name="inf_el" value="3" style="width:100%"></div>
      <div style="flex:1;min-width:120px"><label>Fossil inflation %</label><br><input name="inf_fos" value="3.5" style="width:100%"></div>
    </div>
  </fieldset>

  <fieldset style="border:1px solid #c5cdd8;border-radius:8px;padding:12px 16px;margin-bottom:16px">
    <legend style="font-weight:600;padding:0 6px">Credit run time and depreciation</legend>
    <table style="width:100%;table-layout:fixed">
      <tr>
        <th style="width:18%"></th>
        <th>12–20 years<br><small>battery, heat-pump, air exchange</small></th>
        <th>20–35 years<br><small>photovoltaic</small></th>
        <th>>35 years<br><small>house, insulation, deep drill</small></th>
        <th>unchanged<br><small>the ground</small></th>
      </tr>
      <tr><td>Credit years</td>
        <td><input name="y_12_20" value="15" style="width:60px"></td>
        <td><input name="y_20_35" value="25" style="width:60px"></td>
        <td><input name="y_35" value="30" style="width:60px"></td>
        <td><input name="y_ground" value="30" style="width:60px"></td>
      </tr>
      <tr><td>Depreciation % / year</td>
        <td><input name="p_12_20" value="4" style="width:60px"></td>
        <td><input name="p_20_35" value="3" style="width:60px"></td>
        <td><input name="p_35" value="1" style="width:60px"></td>
        <td><input name="p_ground" value="0" style="width:60px"></td>
      </tr>
    </table>
  </fieldset>
  <div class="actions" style="margin-top:12px">
    <button type="button" id="saveConfig">Save configuration</button>
    <button type="button" id="saveNew">Save as a new configuration</button>
    <span id="saveReason" style="margin-left:12px;color:#666;font-size:12px"></span>
  </div>
</form>
<script>
(function() {
  const origProject = document.getElementById("projName") ? document.getElementById("projName").value : "";
  const origSim = document.getElementById("simName") ? document.getElementById("simName").value : "";
  const saveConfig = document.getElementById("saveConfig");
  const saveNew = document.getElementById("saveNew");
  const saveReason = document.getElementById("saveReason");
  function checkNames() {
    const proj = document.getElementById("projName") ? document.getElementById("projName").value : "";
    const sim = document.getElementById("simName") ? document.getElementById("simName").value : "";
    if (proj !== origProject || sim !== origSim) {
      if (saveConfig) saveConfig.disabled = true;
      if (saveReason) saveReason.textContent = "name changed — only Save as a new configuration";
    } else {
      if (saveConfig) saveConfig.disabled = false;
      if (saveReason) saveReason.textContent = "";
    }
  }
  const pn = document.getElementById("projName");
  const sn = document.getElementById("simName");
  if (pn) pn.addEventListener("input", checkNames);
  if (sn) sn.addEventListener("input", checkNames);
  if (saveConfig) saveConfig.addEventListener("click", () => {
    const project = pn ? pn.value : "";
    const sim = sn ? sn.value : "";
    fetch("/project-finance/save", {method: "POST", headers: {"Content-Type": "application/x-www-form-urlencoded"}, body: "project=" + encodeURIComponent(project) + "&sim=" + encodeURIComponent(sim) + "&mode=keep"}).then(r => r.text()).then(t => { document.open(); document.write(t); document.close(); });
  });
  if (saveNew) saveNew.addEventListener("click", () => {
    const project = pn ? pn.value : "";
    const sim = sn ? sn.value : "";
    fetch("/project-finance/save", {method: "POST", headers: {"Content-Type": "application/x-www-form-urlencoded"}, body: "project=" + encodeURIComponent(project) + "&sim=" + encodeURIComponent(sim) + "&mode=new"}).then(r => r.text()).then(t => { document.open(); document.write(t); document.close(); });
  });
})();
</script>

<script>
async function loadObjects(sel) {
  const previous = sel.value || "";
  const r = await fetch("/api/workspace");
  const data = await r.json();
  sel.innerHTML = "<option value=''>— select object —</option>";
  (data.objects || []).forEach(n => {
    const o = document.createElement("option");
    o.value = n; o.textContent = n;
    sel.appendChild(o);
  });
  if (previous) sel.value = previous;
}
async function loadSims(objSel, simSel) {
  const previous = simSel.value || simSel.dataset.want || "";
  simSel.innerHTML = "<option value=''>— simulation —</option>";
  const obj = objSel.value;
  if (!obj) return;
  const r = await fetch("/api/object-files?object=" + encodeURIComponent(obj));
  const data = await r.json();
  (data.files || []).forEach(f => {
    const o = document.createElement("option");
    o.value = f.name; o.textContent = f.name;
    simSel.appendChild(o);
  });
  if (previous) {
    // match by name or by path ending
    for (const o of simSel.options) {
      if (o.value === previous || previous.endsWith(o.value) || o.value === previous.split("/").pop().replace(/\\.pdf$/,"")) {
        simSel.value = o.value;
        break;
      }
    }
  }
}
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("select[name$='_n'], select[name$='_e'], select[name$='_p']").forEach(sel => {
    if (sel.name.startsWith("obj_")) loadObjects(sel);
  });
  // pair object/sim if present
  ["n","e","p"].forEach(sfx => {
    const o = document.querySelector("select[name='obj_" + sfx + "']");
    const s = document.querySelector("select[name='sim_" + sfx + "']");
    if (o && s) o.onchange = () => loadSims(o, s);
    if (s) {
      s.addEventListener("change", () => {
        if (s.value) {
          const el = document.querySelector("input[name='el_" + sfx + "']");
          const fos = document.querySelector("input[name='fos_" + sfx + "']");
          if (el) { el.value = ""; el.placeholder = "by object"; }
          if (fos) { fos.value = ""; fos.placeholder = "by object"; }
          if (el) el.dispatchEvent(new Event("blur"));
          if (fos) fos.dispatchEvent(new Event("blur"));
        }
      });
    }
  });
  // auto-save on leaving the page
  
  ["n","e","p"].forEach(sfx => {
    const sim = document.querySelector("select[name='sim_" + sfx + "']");
    if (sim) {
    sim.addEventListener("change", () => {
      if (sim.value) {
        const el = document.querySelector("input[name='el_" + sfx + "']");
        const fos = document.querySelector("input[name='fos_" + sfx + "']");
        if (el) el.value = "0";
        if (fos) fos.value = "0";
      }
    });
  }
  });

  
  document.querySelectorAll("select[name^='sim_']").forEach(sim => {
    sim.addEventListener("change", () => {
      const sfx = sim.name.split("_")[1];
      if (sim.value) {
        const el = document.querySelector("input[name='el_" + sfx + "']");
        const fos = document.querySelector("input[name='fos_" + sfx + "']");
        if (el) el.value = "0";
        if (fos) fos.value = "0";
      }
    });
  });

  window.addEventListener("beforeunload", () => {});
});
</script>


<script>
async function saveDefault() {
  const form = document.querySelector("form");
  const fd = new FormData(form);
  const body = new URLSearchParams(fd).toString();
  const r = await fetch("/project-finance/set-default", {
    method: "POST",
    headers: {"Content-Type": "application/x-www-form-urlencoded"},
    body: body
  });
  const html = await r.text();
  document.open();
  document.write(html);
  document.close();
}
</script>

<script>
(function() {
  function reportField(el) {
    const project = (document.getElementById("projName") && document.getElementById("projName").value) || localStorage.getItem("pf_project") || "";
    if (!project) return;
    localStorage.setItem("pf_project", project);
    const path = location.pathname.split("/").pop() || "";
    const letter = path === "baseline" ? "BASELINE" : ("abcde".includes(path) ? path.toUpperCase() : "");
    const body = "project=" + encodeURIComponent(project) + "&name=" + encodeURIComponent(el.name || el.id) + "&value=" + encodeURIComponent(el.value || "") + (letter ? "&letter=" + letter : "");
    fetch("/project-finance/field", {
      method: "POST",
      headers: {"Content-Type": "application/x-www-form-urlencoded"},
      body: body,
      keepalive: true
    }).catch(() => {});
  }
  function fillForm(data) {
    if (!data) return;
    Object.keys(data).forEach(k => {
      const el = document.querySelector("[name='" + k + "']") || document.getElementById(k);
      if (el && "value" in el) el.value = data[k];
    });
  }
  async function loadProjects() {
    const sel = document.getElementById("projPick");
    if (!sel) return;
    const r = await fetch("/api/projects");
    const js = await r.json();
    sel.innerHTML = "<option value=''>— select —</option>";
    (js.projects || []).forEach(p => {
      const o = document.createElement("option");
      o.value = p.name; o.textContent = p.name;
      sel.appendChild(o);
    });
    const stored = localStorage.getItem("pf_project");
    if (stored) sel.value = stored;
  }
  async function loadSims(project) {
    const sel = document.getElementById("simPick");
    if (!sel || !project) return;
    const r = await fetch("/api/projects");
    const js = await r.json();
    const proj = (js.projects || []).find(p => p.name === project);
    sel.innerHTML = "<option value=''>— select —</option>";
    if (proj) (proj.sims || []).forEach(s => {
      const o = document.createElement("option");
      o.value = s; o.textContent = s;
      sel.appendChild(o);
    });
  }
  async function loadData(project, sim) {
    const r = await fetch("/api/project-finance?project=" + encodeURIComponent(project) + (sim ? "&sim=" + encodeURIComponent(sim) : ""));
    const js = await r.json();
    fillForm(js.data || {});
    if (document.getElementById("projName")) document.getElementById("projName").value = project;
    if (sim && document.getElementById("simName")) {
      const m = sim.match(/\\d{4}-\\d{2}-\\d{2} \\d{4} (.+)/);
      document.getElementById("simName").value = m ? m[1] : sim;
    }
  }
  document.addEventListener("DOMContentLoaded", async () => {
    await loadProjects();
    const projPick = document.getElementById("projPick");
    const simPick = document.getElementById("simPick");
    // Server already filled the form from PF_STORE. Only load sims list for the current project.
    const last = localStorage.getItem("pf_project") || (document.getElementById("projName") && document.getElementById("projName").value) || "";
    if (last && projPick) {
      projPick.value = last;
      await loadSims(last);
      const full = window.PF_SELECTED_SIM || localStorage.getItem("pf_sim") || "";
      if (window.PF_SELECTED_SIM) localStorage.setItem("pf_sim", window.PF_SELECTED_SIM);
      if (simPick && full) {
        for (const o of simPick.options) {
          if (o.value === full) { simPick.value = full; break; }
        }
        const m = full.match(/\\d{4}-\\d{2}-\\d{2} \\d{4} (.+)/);
        const nameEl = document.getElementById("simName");
        if (nameEl && !nameEl.value) nameEl.value = m ? m[1] : full;
      }
    }
    if (projPick) {
      projPick.addEventListener("change", () => {
        const p = projPick.value;
        if (!p) return;
        localStorage.setItem("pf_project", p);
        location.href = "/project-finance?project=" + encodeURIComponent(p);
      });
    }
    if (simPick) {
      simPick.addEventListener("change", () => {
        const s = simPick.value;
        if (!s) return;
        localStorage.setItem("pf_sim", s);
        location.href = "/project-finance?sim=" + encodeURIComponent(s);
      });
    }
    document.querySelectorAll("input, select, textarea").forEach(el => {});
    // When a house simulation is chosen, clear electricity and fossil and show "by object"
    function clearEnergy(sfx) {
      const el = document.querySelector("input[name='el_" + sfx + "']");
      const fos = document.querySelector("input[name='fos_" + sfx + "']");
      if (el) { el.value = ""; el.placeholder = "by object"; el.readOnly = true; }
      if (fos) { fos.value = ""; fos.placeholder = "by object"; fos.readOnly = true; }
    }
    ["n","e","p"].forEach(sfx => {
      const sim = document.querySelector("select[name='sim_" + sfx + "']");
      if (!sim) return;
      if (sim.value) clearEnergy(sfx);
      sim.addEventListener("change", () => {
        if (sim.value) clearEnergy(sfx);
        else {
          const el = document.querySelector("input[name='el_" + sfx + "']");
          const fos = document.querySelector("input[name='fos_" + sfx + "']");
          if (el) { el.readOnly = false; el.placeholder = ""; }
          if (fos) { fos.readOnly = false; fos.placeholder = ""; }
        }
      });
    });
    // flush on leaving — send the whole form so nothing is lost
    function flushNow() {}
    window.addEventListener("beforeunload", flushNow);
    document.querySelectorAll("nav a").forEach(a => {
      a.addEventListener("click", function (e) {
        flushNow();
      });
    });
  });
})();
</script>
"""

BASELINE_HTML = """
<h2>Project finance</h2>
<nav style="margin-bottom:14px">
  <a href="/project-finance?view=general" class="nav" style="margin-right:14px">General</a><a href="/project-finance/baseline" class="nav" style="margin-right:14px">Baseline</a><a href="/project-finance/a" class="nav" style="margin-right:14px">A</a><a href="/project-finance/b" class="nav" style="margin-right:14px">B</a><a href="/project-finance/c" class="nav" style="margin-right:14px">C</a><a href="/project-finance/d" class="nav" style="margin-right:14px">D</a><a href="/project-finance/e" class="nav" style="margin-right:14px">E</a><a href="/project-finance/simulate" class="nav" style="margin-right:14px">Simulate</a><a href="/project-finance/pdf" class="nav">PDF creation</a>
</nav>
<style>
.nav.on { background:#003399; color:#fff; padding:4px 10px; border-radius:4px; }
</style>
<script>
document.addEventListener("DOMContentLoaded", () => {
  const path = location.pathname;
  document.querySelectorAll(".nav").forEach(a => {
    if (a.getAttribute("href") === path) a.classList.add("on");
  });
});
</script>

<form method="post" action="/project-finance/baseline" enctype="application/x-www-form-urlencoded">
  <h3>Baseline</h3>
  <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:12px">
    <div><label>Income increase %</label><br><input name="inc_inc" value="2" style="width:80px"></div>
    <div><label>On bank account</label><br><input name="base_money" value="0" style="width:100px"></div>
    <div><label>Years to pension</label><br><input name="years_pension" value="20" style="width:80px"></div>
    <div><label>Decrease pension % / year emergency</label><br><input name="pens_dec" value="1.5" style="width:80px"></div>
  </div>
  <div style="display:flex;gap:8px;margin-bottom:12px">
    <label>Stress test in years</label><br>
    <input name="stress_5" value="5" style="width:60px">
    <input name="stress_10" value="10" style="width:60px">
    <input name="stress_15" value="15" style="width:60px">
    <input name="stress_20" value="20" style="width:60px">
  </div>
  <p class="hint">Stress test: years after the investment a surprise (unemployed or bankrupt). Receives money, for example Notstandshilfe in Austria. Every year of emergency decreases the pension by the percent above.</p>

  <table style="width:100%;margin:12px 0">
    <tr><th></th><th>normal</th><th>emergency</th><th>pension</th></tr>
    <tr><td>Income</td>
      <td><input name="inc_n" value="" style="width:90px"></td>
      <td><input name="inc_e" value="" style="width:90px"></td>
      <td><input name="inc_p" value="" style="width:90px"></td></tr>
    <tr><td>Expenses non energy</td>
      <td><input name="exp_n" value="" style="width:90px"></td>
      <td><input name="exp_e" value="" style="width:90px"></td>
      <td><input name="exp_p" value="" style="width:90px"></td></tr>
    <tr><td>Expenses electricity</td>
      <td><input name="el_n" value="" style="width:90px" placeholder=""></td>
      <td><input name="el_e" value="" style="width:90px" placeholder=""></td>
      <td><input name="el_p" value="" style="width:90px" placeholder=""></td></tr>
    <tr><td>Expenses fossil energy</td>
      <td><input name="fos_n" value="" style="width:90px" placeholder=""></td>
      <td><input name="fos_e" value="" style="width:90px" placeholder=""></td>
      <td><input name="fos_p" value="" style="width:90px" placeholder=""></td></tr>
  </table>

  <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:8px">
    <div style="flex:1"><label>Normal</label><br>
      <select name="obj_n" style="width:100%"><option value="">— object —</option></select>
      <select name="sim_n" style="width:100%;margin-top:4px"><option value="">— simulation —</option></select></div>
    <div style="flex:1"><label>Emergency</label><br>
      <select name="obj_e" style="width:100%"><option value="">— object —</option></select>
      <select name="sim_e" style="width:100%;margin-top:4px"><option value="">— simulation —</option></select></div>
    <div style="flex:1"><label>Pension</label><br>
      <select name="obj_p" style="width:100%"><option value="">— object —</option></select>
      <select name="sim_p" style="width:100%;margin-top:4px"><option value="">— simulation —</option></select></div>
  </div>
  <p class="hint">When a simulation is chosen, electricity and fossil for that column are set to 0 (handled by object). Emergency and pension often differ in car use and temperature range.</p>
  <div class="field wide"><label>Description</label><textarea name="baseline_description" rows="3" style="width:100%"></textarea></div>
  
</form>

<script>
async function loadObjects(sel) {
  const r = await fetch("/api/workspace");
  const data = await r.json();
  sel.innerHTML = "<option value=''>— select object —</option>";
  (data.objects || []).forEach(n => {
    const o = document.createElement("option");
    o.value = n; o.textContent = n;
    sel.appendChild(o);
  });
}
async function loadSims(objSel, simSel) {
  const previous = simSel.value || simSel.dataset.want || "";
  simSel.innerHTML = "<option value=''>— simulation —</option>";
  const obj = objSel.value;
  if (!obj) return;
  const r = await fetch("/api/object-files?object=" + encodeURIComponent(obj));
  const data = await r.json();
  (data.files || []).forEach(f => {
    const o = document.createElement("option");
    o.value = f.name; o.textContent = f.name;
    simSel.appendChild(o);
  });
  if (previous) {
    // match by name or by path ending
    for (const o of simSel.options) {
      if (o.value === previous || previous.endsWith(o.value) || o.value === previous.split("/").pop().replace(/\\.pdf$/,"")) {
        simSel.value = o.value;
        break;
      }
    }
  }
}
document.addEventListener("DOMContentLoaded", () => {
  const stored = window.PF_STORED || {};
  for (const sfx of ["n","e","p"]) {
    const o = document.querySelector("select[name='obj_" + sfx + "']");
    const s = document.querySelector("select[name='sim_" + sfx + "']");
    if (o && stored["obj_" + sfx]) o.value = stored["obj_" + sfx];
    if (s && stored["sim_" + sfx]) s.value = stored["sim_" + sfx];
    if (o && s) o.onchange = () => loadSims(o, s);
  }
  // auto-save on leaving the page
  window.addEventListener("beforeunload", () => {});
});
</script>


<script>
(function() {
  function reportField(el) {
    const project = (document.getElementById("projName") && document.getElementById("projName").value) || localStorage.getItem("pf_project") || "";
    if (!project) return;
    localStorage.setItem("pf_project", project);
    const path = location.pathname.split("/").pop() || "";
    const letter = path === "baseline" ? "BASELINE" : ("abcde".includes(path) ? path.toUpperCase() : "");
    const body = "project=" + encodeURIComponent(project) + "&name=" + encodeURIComponent(el.name || el.id) + "&value=" + encodeURIComponent(el.value || "") + (letter ? "&letter=" + letter : "");
    fetch("/project-finance/field", {
      method: "POST",
      headers: {"Content-Type": "application/x-www-form-urlencoded"},
      body: body,
      keepalive: true
    }).catch(() => {});
  }
  function fillForm(data) {
    if (!data) return;
    Object.keys(data).forEach(k => {
      const el = document.querySelector("[name='" + k + "']") || document.getElementById(k);
      if (el && "value" in el) el.value = data[k];
    });
  }
  async function loadProjects() {
    const sel = document.getElementById("projPick");
    if (!sel) return;
    const r = await fetch("/api/projects");
    const js = await r.json();
    sel.innerHTML = "<option value=''>— select —</option>";
    (js.projects || []).forEach(p => {
      const o = document.createElement("option");
      o.value = p.name; o.textContent = p.name;
      sel.appendChild(o);
    });
    const stored = localStorage.getItem("pf_project");
    if (stored) sel.value = stored;
  }
  async function loadSims(project) {
    const sel = document.getElementById("simPick");
    if (!sel || !project) return;
    const r = await fetch("/api/projects");
    const js = await r.json();
    const proj = (js.projects || []).find(p => p.name === project);
    sel.innerHTML = "<option value=''>— select —</option>";
    if (proj) (proj.sims || []).forEach(s => {
      const o = document.createElement("option");
      o.value = s; o.textContent = s;
      sel.appendChild(o);
    });
  }
  async function loadData(project, sim) {
    const r = await fetch("/api/project-finance?project=" + encodeURIComponent(project) + (sim ? "&sim=" + encodeURIComponent(sim) : ""));
    const js = await r.json();
    fillForm(js.data || {});
    if (document.getElementById("projName")) document.getElementById("projName").value = project;
    if (sim && document.getElementById("simName")) {
      const m = sim.match(/\\d{4}-\\d{2}-\\d{2} \\d{4} (.+)/);
      document.getElementById("simName").value = m ? m[1] : sim;
    }
  }
  document.addEventListener("DOMContentLoaded", async () => {
    await loadProjects();
    const projPick = document.getElementById("projPick");
    const simPick = document.getElementById("simPick");
    // Server already filled the form from PF_STORE. Only load sims list for the current project.
    const last = localStorage.getItem("pf_project") || (document.getElementById("projName") && document.getElementById("projName").value) || "";
    if (last && projPick) {
      projPick.value = last;
      await loadSims(last);
      const full = localStorage.getItem("pf_sim") || "";
      if (simPick && full) {
        for (const o of simPick.options) {
          if (o.value === full) { simPick.value = full; break; }
        }
        const m = full.match(/\\d{4}-\\d{2}-\\d{2} \\d{4} (.+)/);
        const nameEl = document.getElementById("simName");
        if (nameEl && !nameEl.value) nameEl.value = m ? m[1] : full;
      }
    }
    if (projPick) {
      projPick.addEventListener("change", () => {
        const p = projPick.value;
        if (!p) return;
        localStorage.setItem("pf_project", p);
        location.href = "/project-finance?project=" + encodeURIComponent(p);
      });
    }
    if (simPick) {
      simPick.addEventListener("change", () => {
        const s = simPick.value;
        if (!s) return;
        localStorage.setItem("pf_sim", s);
        location.href = "/project-finance?sim=" + encodeURIComponent(s);
      });
    }
    document.querySelectorAll("input, select, textarea").forEach(el => {});
    // When a house simulation is chosen, clear electricity and fossil and show "by object"
    function clearEnergy(sfx) {
      const el = document.querySelector("input[name='el_" + sfx + "']");
      const fos = document.querySelector("input[name='fos_" + sfx + "']");
      if (el) { el.value = ""; el.placeholder = "by object"; el.readOnly = true; }
      if (fos) { fos.value = ""; fos.placeholder = "by object"; fos.readOnly = true; }
    }
    ["n","e","p"].forEach(sfx => {
      const sim = document.querySelector("select[name='sim_" + sfx + "']");
      if (!sim) return;
      if (sim.value) clearEnergy(sfx);
      sim.addEventListener("change", () => {
        if (sim.value) clearEnergy(sfx);
        else {
          const el = document.querySelector("input[name='el_" + sfx + "']");
          const fos = document.querySelector("input[name='fos_" + sfx + "']");
          if (el) { el.readOnly = false; el.placeholder = ""; }
          if (fos) { fos.readOnly = false; fos.placeholder = ""; }
        }
      });
    });
    // flush on leaving — send the whole form so nothing is lost
    function flushNow() {}
    window.addEventListener("beforeunload", flushNow);
    document.querySelectorAll("nav a").forEach(a => {
      a.addEventListener("click", function (e) {
        flushNow();
      });
    });
  });
})();
</script>
"""

VARIANT_HTML = """
<h2>Project finance</h2>
<nav style="margin-bottom:14px">
  <a href="/project-finance?view=general" class="nav" style="margin-right:14px">General</a><a href="/project-finance/baseline" class="nav" style="margin-right:14px">Baseline</a><a href="/project-finance/a" class="nav" style="margin-right:14px">A</a><a href="/project-finance/b" class="nav" style="margin-right:14px">B</a><a href="/project-finance/c" class="nav" style="margin-right:14px">C</a><a href="/project-finance/d" class="nav" style="margin-right:14px">D</a><a href="/project-finance/e" class="nav" style="margin-right:14px">E</a><a href="/project-finance/simulate" class="nav" style="margin-right:14px">Simulate</a><a href="/project-finance/pdf" class="nav">PDF creation</a>
</nav>
<style>
.nav.on { background:#003399; color:#fff; padding:4px 10px; border-radius:4px; }
</style>
<script>
document.addEventListener("DOMContentLoaded", () => {
  const path = location.pathname;
  document.querySelectorAll(".nav").forEach(a => {
    if (a.getAttribute("href") === path) a.classList.add("on");
  });
});
</script>

<form method="post" action="/project-finance/variant">
  <h3>Variant {letter}</h3>
  <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:12px">
    <div style="flex:1"><label>Normal</label><br>
      <select name="obj_n" style="width:100%"><option value="">— object —</option></select>
      <select name="sim_n" style="width:100%;margin-top:4px"><option value="">— simulation —</option></select></div>
    <div style="flex:1"><label>Emergency</label><br>
      <select name="obj_e" style="width:100%"><option value="">— object —</option></select>
      <select name="sim_e" style="width:100%;margin-top:4px"><option value="">— simulation —</option></select></div>
    <div style="flex:1"><label>Pension</label><br>
      <select name="obj_p" style="width:100%"><option value="">— object —</option></select>
      <select name="sim_p" style="width:100%;margin-top:4px"><option value="">— simulation —</option></select></div>
  </div>
  <table style="width:100%;margin:12px 0">
    <tr><th>Investment</th><th>12–20 years</th><th>20–35 years</th><th>>35 years</th><th>ground</th></tr>
    <tr><td>Amount</td>
      <td><input name="inv_12_20" oninput="saveField(this)" value="" style="width:90px"></td>
      <td><input name="inv_20_35" oninput="saveField(this)" value="" style="width:90px"></td>
      <td><input name="inv_35" oninput="saveField(this)" value="" style="width:90px"></td>
      <td><input name="inv_ground" oninput="saveField(this)" value="" style="width:90px"></td></tr>
    <tr><td>From bank account</td>
      <td><input name="bank_12_20" value="" style="width:90px" oninput="saveField(this)"></td>
      <td><input name="bank_20_35" value="" style="width:90px" oninput="saveField(this)"></td>
      <td><input name="bank_35" value="" style="width:90px" oninput="saveField(this)"></td>
      <td><input name="bank_ground" value="" style="width:90px" oninput="saveField(this)"></td></tr>
  </table>
  <div class="field wide"><label>Description</label><textarea name="{letter_lower}_description" rows="3" style="width:100%"></textarea></div>
  
</form>

<script>
async function loadObjects(sel) {{
  const previous = sel.value || "";
  const r = await fetch("/api/workspace");
  const data = await r.json();
  sel.innerHTML = "<option value=''>— select object —</option>";
  (data.objects || []).forEach(n => {{
    const o = document.createElement("option");
    o.value = n; o.textContent = n;
    sel.appendChild(o);
  }});
  if (previous) sel.value = previous;
}}
async function loadSims(objSel, simSel) {{
  const previous = simSel.value || simSel.dataset.want || "";
  simSel.innerHTML = "<option value=''>— simulation —</option>";
  const obj = objSel.value;
  if (!obj) return;
  const r = await fetch("/api/object-files?object=" + encodeURIComponent(obj));
  const data = await r.json();
  (data.files || []).forEach(f => {{
    const o = document.createElement("option");
    o.value = f.name; o.textContent = f.name;
    simSel.appendChild(o);
  }});
  if (previous) {{
    for (const o of simSel.options) {{
      if (o.value === previous || o.textContent === previous) {{
        simSel.value = o.value;
        break;
      }}
    }}
  }}
}}
document.addEventListener("DOMContentLoaded", () => {{
  document.querySelectorAll("select[name^='obj_']").forEach(sel => {{
    const sfx = sel.name.slice(4);
    const sim = document.querySelector("select[name='sim_" + sfx + "']");
    sel.onchange = () => {{ if (sim) loadSims(sel, sim); }};
  }});
}});
</script>


<script>
window.addEventListener("beforeunload", () => {
  document.querySelectorAll("input, textarea, select").forEach(el => {
    if (el.name && el.value) saveField(el);
  });
});
async function saveField(el) {
  const project = localStorage.getItem("pf_project") || "";
  if (!project) { alert("Enter a project name on General first"); return; }
  const letter = location.pathname.split("/").pop() || "a";
  await fetch("/project-finance/field", {
    method: "POST",
    headers: {"Content-Type": "application/x-www-form-urlencoded"},
    body: "project=" + encodeURIComponent(project) + "&letter=" + encodeURIComponent(letter) + "&name=" + encodeURIComponent(el.name) + "&value=" + encodeURIComponent(el.value)
  });
}
</script>

<script>
(function() {
  function reportField(el) {
    const project = (document.getElementById("projName") && document.getElementById("projName").value) || localStorage.getItem("pf_project") || "";
    if (!project) return;
    localStorage.setItem("pf_project", project);
    const path = location.pathname.split("/").pop() || "";
    const letter = path === "baseline" ? "BASELINE" : ("abcde".includes(path) ? path.toUpperCase() : "");
    const body = "project=" + encodeURIComponent(project) + "&name=" + encodeURIComponent(el.name || el.id) + "&value=" + encodeURIComponent(el.value || "") + (letter ? "&letter=" + letter : "");
    fetch("/project-finance/field", {
      method: "POST",
      headers: {"Content-Type": "application/x-www-form-urlencoded"},
      body: body,
      keepalive: true
    }).catch(() => {});
  }
  function fillForm(data) {
    if (!data) return;
    Object.keys(data).forEach(k => {
      const el = document.querySelector("[name='" + k + "']") || document.getElementById(k);
      if (el && "value" in el) el.value = data[k];
    });
  }
  async function loadProjects() {
    const sel = document.getElementById("projPick");
    if (!sel) return;
    const r = await fetch("/api/projects");
    const js = await r.json();
    sel.innerHTML = "<option value=''>— select —</option>";
    (js.projects || []).forEach(p => {
      const o = document.createElement("option");
      o.value = p.name; o.textContent = p.name;
      sel.appendChild(o);
    });
    const stored = localStorage.getItem("pf_project");
    if (stored) sel.value = stored;
  }
  async function loadSims(project) {
    const sel = document.getElementById("simPick");
    if (!sel || !project) return;
    const r = await fetch("/api/projects");
    const js = await r.json();
    const proj = (js.projects || []).find(p => p.name === project);
    sel.innerHTML = "<option value=''>— select —</option>";
    if (proj) (proj.sims || []).forEach(s => {
      const o = document.createElement("option");
      o.value = s; o.textContent = s;
      sel.appendChild(o);
    });
  }
  async function loadData(project, sim) {
    const r = await fetch("/api/project-finance?project=" + encodeURIComponent(project) + (sim ? "&sim=" + encodeURIComponent(sim) : ""));
    const js = await r.json();
    fillForm(js.data || {});
    if (document.getElementById("projName")) document.getElementById("projName").value = project;
    if (sim && document.getElementById("simName")) {
      const m = sim.match(/\\d{4}-\\d{2}-\\d{2} \\d{4} (.+)/);
      document.getElementById("simName").value = m ? m[1] : sim;
    }
  }
  document.addEventListener("DOMContentLoaded", async () => {
    await loadProjects();
    const projPick = document.getElementById("projPick");
    const simPick = document.getElementById("simPick");
    // Server already filled the form from PF_STORE. Only load sims list for the current project.
    const last = localStorage.getItem("pf_project") || (document.getElementById("projName") && document.getElementById("projName").value) || "";
    if (last && projPick) {
      projPick.value = last;
      await loadSims(last);
      const full = localStorage.getItem("pf_sim") || "";
      if (simPick && full) {
        for (const o of simPick.options) {
          if (o.value === full) { simPick.value = full; break; }
        }
        const m = full.match(/\\d{4}-\\d{2}-\\d{2} \\d{4} (.+)/);
        const nameEl = document.getElementById("simName");
        if (nameEl && !nameEl.value) nameEl.value = m ? m[1] : full;
      }
    }
    if (projPick) {
      projPick.addEventListener("change", () => {
        const p = projPick.value;
        if (!p) return;
        localStorage.setItem("pf_project", p);
        location.href = "/project-finance?project=" + encodeURIComponent(p);
      });
    }
    if (simPick) {
      simPick.addEventListener("change", () => {
        const s = simPick.value;
        if (!s) return;
        localStorage.setItem("pf_sim", s);
        location.href = "/project-finance?sim=" + encodeURIComponent(s);
      });
    }
    document.querySelectorAll("input, select, textarea").forEach(el => {});
    // When a house simulation is chosen, clear electricity and fossil and show "by object"
    function clearEnergy(sfx) {
      const el = document.querySelector("input[name='el_" + sfx + "']");
      const fos = document.querySelector("input[name='fos_" + sfx + "']");
      if (el) { el.value = ""; el.placeholder = "by object"; el.readOnly = true; }
      if (fos) { fos.value = ""; fos.placeholder = "by object"; fos.readOnly = true; }
    }
    ["n","e","p"].forEach(sfx => {
      const sim = document.querySelector("select[name='sim_" + sfx + "']");
      if (!sim) return;
      if (sim.value) clearEnergy(sfx);
      sim.addEventListener("change", () => {
        if (sim.value) clearEnergy(sfx);
        else {
          const el = document.querySelector("input[name='el_" + sfx + "']");
          const fos = document.querySelector("input[name='fos_" + sfx + "']");
          if (el) { el.readOnly = false; el.placeholder = ""; }
          if (fos) { fos.readOnly = false; fos.placeholder = ""; }
        }
      });
    });
    // flush on leaving — send the whole form so nothing is lost
    function flushNow() {}
    window.addEventListener("beforeunload", flushNow);
    document.querySelectorAll("nav a").forEach(a => {
      a.addEventListener("click", function (e) {
        flushNow();
      });
    });
  });
})();
</script>
"""




def pf_simulate(variant: str, stress: str) -> dict:
    """Calculate 40 years of project finance. Returns monthly data for charts.
    Energy figures come from the linked simulation sidecar when available.
    """
    pf_ensure_loaded()
    data = PF_STORE.get("data") or {}
    variants = PF_STORE.get("variants") or {}
    key = "BASELINE" if variant == "baseline" else variant.upper()
    v = variants.get(key) or {}

    # Load energy simulations for normal / emergency / pension
    from pege_energy_sim_common import data_root
    def load_sim(sfx):
        sn = str(v.get(f"sim_{sfx}") or "").strip()
        on = str(v.get(f"obj_{sfx}") or "").strip()
        if not sn or not on:
            return None
        folder = data_root() / "objects" / on
        if not folder.is_dir():
            return None
        for c in list(folder.glob(f"*{sn}*.json")) + list(folder.glob(f"{sn}.json")):
            try:
                raw = json.loads(c.read_text(encoding="utf-8"))
                if raw.get("outcome"):
                    return raw
            except Exception:
                pass
        return None
    sims = {"n": load_sim("n"), "e": load_sim("e") or load_sim("n"), "p": load_sim("p") or load_sim("n")}
    month_cols = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    def monthly_from(sim, section):
        if not sim:
            return [0.0] * 12
        pages = (sim.get("outcome") or {}).get("pages") or {}
        rows = pages.get("Energy by price") or []
        in_sec = False
        for r in rows:
            title = (r.get("title") or "").strip()
            if r.get("head"):
                in_sec = title.lower() == section.lower()
                continue
            if in_sec and "month average" in title.lower():
                return [float(r.get(c) or 0) for c in month_cols]
        return [0.0] * 12
    export_by = {k: monthly_from(s, "Export") for k, s in sims.items()}
    import_by = {k: monthly_from(s, "Import incl. peak fee") for k, s in sims.items()}

    base = variants.get("BASELINE") or {}
    base_money = float(base.get("base_money") or data.get("base_money") or 0)
    years_pension = int(base.get("years_pension") or data.get("years_pension") or 20)
    pens_dec = float(base.get("pens_dec") or data.get("pens_dec") or 1.5) / 100
    inf_el = float(data.get("inf_el") or 2) / 100
    inf_avg = float(data.get("inf_avg") or 2) / 100
    inf_inc = float(base.get("inc_inc") or data.get("inc_inc") or 2) / 100

    stress_years = {
        "s1": int(base.get("stress_5") or 5),
        "s2": int(base.get("stress_10") or 10),
        "s3": int(base.get("stress_15") or 15),
        "s4": int(base.get("stress_20") or 20),
    }
    stress_year = stress_years.get(stress, years_pension)

    def getv(key, default=0):
        val = v.get(key)
        if (val is None or val == "") and key in ("inc_n", "inc_e", "inc_p", "exp_n", "exp_e", "exp_p"):
            val = (variants.get("BASELINE") or {}).get(key)
        if val is None or val == "":
            return default
        try:
            return float(val)
        except Exception:
            return default
    inc_n = getv("inc_n")
    inc_e = getv("inc_e", inc_n)
    inc_p = getv("inc_p")
    exp_n = getv("exp_n")
    exp_e = getv("exp_e", exp_n)
    exp_p = getv("exp_p")
    el_n = getv("el_n")
    fos_n = getv("fos_n")

    # Credits from variant investment amounts and General credit years
    def credit_amount(inv_key, bank_key):
        inv = float(v.get(inv_key) or 0)
        from_bank = float(v.get(bank_key) or 0)
        return max(0, inv - from_bank)
    credit_rate = float(data.get("credit_int") or 4) / 100
    def make_credit(inv_key, bank_key, years_key, label):
        amount = credit_amount(inv_key, bank_key)
        years = int(data.get(years_key) or 15)
        n = years * 12
        r = credit_rate / 12
        if amount > 0 and n > 0 and r > 0:
            payment = amount * r * (1 + r) ** n / ((1 + r) ** n - 1)
        else:
            payment = amount / n if n > 0 else 0
        return {"amount": amount, "years": years, "n": n, "payment": payment, "label": label, "rest": amount}
    credits = [
        make_credit("inv_12_20", "bank_12_20", "y_12_20", "12-20y"),
        make_credit("inv_20_35", "bank_20_35", "y_20_35", "20-35y"),
        make_credit("inv_35", "bank_35", "y_35", ">35y"),
    ]

    months = []
    bank = base_money

    for year in range(40):
        for month in range(12):
            m = year * 12 + month
            # Base phase from stress / pension schedule
            if year < stress_year:
                phase = "normal"
                income = inc_n * ((1 + inf_inc) ** year)
                expenses = exp_n * ((1 + inf_avg) ** year)
            elif year < years_pension:
                phase = "emergency"
                income = inc_e * ((1 + inf_avg) ** year)
                expenses = exp_e * ((1 + inf_avg) ** year)
            else:
                phase = "pension"
                years_in_emergency = max(0, years_pension - stress_year)
                pens_factor = (1 - pens_dec) ** years_in_emergency
                income = inc_p * pens_factor * ((1 + inf_avg) ** year)
                expenses = exp_p * ((1 + inf_avg) ** year)

            # Emergency trigger
            forced_emergency = False
            if phase == "emergency":
                forced_emergency = True
            else:
                exp_e_now = exp_e * ((1 + inf_avg) ** year)
                monthly_gap = expenses - income
                # Bank would be negative within 12 months at the current gap
                months_to_negative = (bank / monthly_gap) if monthly_gap > 0 else 999
                will_be_negative_soon = months_to_negative <= 12
                if phase == "pension":
                    # Switch if emergency is cheaper and bank is already negative
                    # or would be negative within 12 months
                    if exp_e_now < expenses and (bank <= 0 or will_be_negative_soon):
                        forced_emergency = True
                        expenses = exp_e_now
                        phase = "emergency"
                elif monthly_gap > 0 and bank < monthly_gap:
                    forced_emergency = True
                    expenses = exp_e_now
                    phase = "emergency"

            # Energy by price from the phase-specific simulation, inflated with electricity rate.
            # If the phase-specific data is missing or all zero, fall back to the normal simulation
            # so emergency/pension never silently drop to zero energy cost.
            sfx = "e" if phase == "emergency" else ("p" if phase == "pension" else "n")
            em = export_by.get(sfx) or [0.0]*12
            im = import_by.get(sfx) or [0.0]*12
            if not any(im) and not any(em):
                em = export_by.get("n") or [0.0]*12
                im = import_by.get("n") or [0.0]*12
            if any(em) or any(im):
                energy_sold = em[month] * ((1 + inf_el) ** year)
                energy_purchased = im[month] * ((1 + inf_el) ** year)
            else:
                energy_sold = 0.0
                energy_purchased = (el_n + fos_n) * ((1 + inf_el) ** year) / 12

            # Depreciation/maintenance: base amount inflated with general inflation
            dep_rate_12 = float(data.get("p_12_20") or 0) / 100
            dep_rate_20 = float(data.get("p_20_35") or 0) / 100
            dep_rate_35 = float(data.get("p_35") or 0) / 100
            inv_12 = float(v.get("inv_12_20") or 0)
            inv_20 = float(v.get("inv_20_35") or 0)
            inv_35 = float(v.get("inv_35") or 0)
            dep_base = (inv_12 * dep_rate_12 + inv_20 * dep_rate_20 + inv_35 * dep_rate_35) / 12
            depreciation = dep_base * ((1 + inf_avg) ** year)
            bank_rate = float(data.get("bank_int") or 0.5) / 100
            interest = bank * bank_rate / 12 if bank > 0 else 0
            month_balance = income + energy_sold + interest - expenses - energy_purchased
            if energy_sold == 0 and energy_purchased > 0:
                month_balance = income - expenses - energy_purchased

            # Annuity: monthly payment, rest decreases by principal portion
            total_payment = 0
            for c in credits:
                if c["rest"] > 0 and m < c["n"]:
                    interest_part = c["rest"] * credit_rate / 12
                    principal = c["payment"] - interest_part
                    c["rest"] = max(0, c["rest"] - principal)
                    total_payment += c["payment"]
                else:
                    c["rest"] = 0
            total_credit = sum(c["rest"] for c in credits)

            # Month balance includes the credit payment
            month_balance -= total_payment
            net = month_balance
            bank += net

            months.append({
                "m": m, "year": year, "month": month,
                "phase": phase,
                "emergency": forced_emergency,
                "income": round(income, 2),
                "income_real": round(income / ((1 + inf_avg) ** year), 2),
                "expenses": round(expenses, 2),
                "expenses_real": round(expenses / ((1 + inf_avg) ** year), 2),
                "energy_sold": round(energy_sold, 2),
                "energy_sold_real": round(energy_sold / ((1 + inf_avg) ** year), 2),
                "energy_purchased": round(energy_purchased, 2),
                "energy_purchased_real": round(energy_purchased / ((1 + inf_avg) ** year), 2),
                "interest": round(interest, 2),
                "interest_real": round(interest / ((1 + inf_avg) ** year), 2),
                "month_balance": round(month_balance, 2),
                "month_balance_real": round(month_balance / ((1 + inf_avg) ** year), 2),
                "net": round(net, 2),
                "net_real": round(net / ((1 + inf_avg) ** year), 2),
                "bank": round(bank, 2),
                "bank_real": round(bank / ((1 + inf_avg) ** year), 2),
                "credit": round(total_credit, 2),
                "credit_real": round(total_credit / ((1 + inf_avg) ** year), 2),
                "credit_payment": round(total_payment, 2),
                "credit_payment_real": round(total_payment / ((1 + inf_avg) ** year), 2),
                "depreciation": round(depreciation, 2),
                "depreciation_real": round(dep_base, 2),
                "credits": [{"label": c["label"], "rest": round(c["rest"], 2)} for c in credits],
            })

    return {"months": months, "variant": variant, "stress": stress, "has_sim": bool(sims.get("n")), "sim_name": str(v.get("sim_n") or "")}


SIMULATE_HTML = """
<h2>Project finance</h2>
<nav style="margin-bottom:14px">
  <a href="/project-finance?view=general" class="nav" style="margin-right:14px">General</a><a href="/project-finance/baseline" class="nav" style="margin-right:14px">Baseline</a><a href="/project-finance/a" class="nav" style="margin-right:14px">A</a><a href="/project-finance/b" class="nav" style="margin-right:14px">B</a><a href="/project-finance/c" class="nav" style="margin-right:14px">C</a><a href="/project-finance/d" class="nav" style="margin-right:14px">D</a><a href="/project-finance/e" class="nav" style="margin-right:14px">E</a><a href="/project-finance/simulate" class="nav" style="margin-right:14px">Simulate</a><a href="/project-finance/pdf" class="nav">PDF creation</a>
</nav>
<style>
.nav.on { background:#003399; color:#fff; padding:4px 10px; border-radius:4px; }
</style>
<script>
document.addEventListener("DOMContentLoaded", () => {
  const path = location.pathname;
  document.querySelectorAll(".nav").forEach(a => {
    if (a.getAttribute("href") === path) a.classList.add("on");
  });
});
</script>

<div style="margin-bottom:12px">
  <span id="varSel">
    <button type="button" class="sel" data-v="baseline">Baseline</button>
    <button type="button" class="sel" data-v="a">A</button>
    <button type="button" class="sel" data-v="b">B</button>
    <button type="button" class="sel" data-v="c">C</button>
    <button type="button" class="sel" data-v="d">D</button>
    <button type="button" class="sel" data-v="e">E</button>
  </span>
  <span id="stressSel" style="margin-left:24px">
    <button type="button" class="sel" data-s="normal">Normal</button>
    <button type="button" class="sel" data-s="s1">Stress 1</button>
    <button type="button" class="sel" data-s="s2">Stress 2</button>
    <button type="button" class="sel" data-s="s3">Stress 3</button>
    <button type="button" class="sel" data-s="s4">Stress 4</button>
  </span>
  <span id="valSel" style="margin-left:24px">
    <button type="button" class="sel" data-val="nominal">Nominal</button>
    <button type="button" class="sel" data-val="real">Real</button>
  </span>
</div>
<div id="charts">
  <canvas id="chart1" width="900" height="280" style="border:1px solid #ccc;display:block"></canvas>
  <div id="yearAxis" style="height:22px;position:relative;margin:0 0 4px 0;font:10px sans-serif;color:#666"></div>
  <canvas id="chart2" width="900" height="280" style="border:1px solid #ccc;display:block"></canvas>
</div>
<p id="simStatus" style="margin-top:12px;color:#333"></p>
<style>
.sel { margin-right:6px; padding:6px 12px; border:1px solid #ccc; background:#fff; color:#000; cursor:pointer; }
.sel.on { background:#003399; color:#fff; border-color:#003399; }
#tip { position:fixed; background:#fff; border:1px solid #ccc; padding:8px; font-size:11px; pointer-events:none; z-index:99; box-shadow:0 2px 6px rgba(0,0,0,.15); max-width:420px; }
#tip table { border-collapse:collapse; width:100%; }
#tip th, #tip td { padding:2px 8px; text-align:right; border-bottom:1px solid #eee; }
#tip th:first-child, #tip td:first-child { text-align:left; white-space:nowrap; }
#tip tr:nth-child(even) { background:#f5f5f5; }
#tip tr:nth-child(odd) { background:#fff; }
</style>
<script>
function niceMax(v) {
  if (v <= 0) return 1;
  const seq = [1, 1.5, 2, 3, 4, 5, 6, 7, 8];
  const mag = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / mag;
  let nice = 10;
  for (const s of seq) { if (n <= s) { nice = s; break; } }
  return nice * mag;
}
let scaleDetermined = false;
let scale = { nominal: { c1: 1, c2: 1 }, real: { c1: 1, c2: 1 } };
async function determineScale() {
  const variants = ["baseline", "a", "b", "c", "d", "e"];
  const stresses = ["normal", "s1", "s2", "s3", "s4"];
  const jobs = [];
  for (const v of variants) {
    for (const s of stresses) {
      jobs.push(fetch("/api/pf-simulate?v=" + v + "&s=" + s).then(r => r.json()).catch(() => null));
    }
  }
  const results = await Promise.all(jobs);
  let n1 = 1, n2 = 1, r1 = 1, r2 = 1;
  for (const data of results) {
    if (!data || !data.months) continue;
    for (const m of data.months) {
      n1 = Math.max(n1, Math.abs(m.income || 0), Math.abs(m.expenses || 0), Math.abs(m.energy_sold || 0), Math.abs(m.energy_purchased || 0), Math.abs(m.net || 0));
      r1 = Math.max(r1, Math.abs(m.income_real || 0), Math.abs(m.expenses_real || 0), Math.abs(m.energy_sold_real || 0), Math.abs(m.energy_purchased_real || 0), Math.abs(m.net_real || 0));
      n2 = Math.max(n2, Math.abs(m.bank || 0) + Math.abs(m.credit || 0));
      r2 = Math.max(r2, Math.abs(m.bank_real || 0) + Math.abs(m.credit_real || 0));
    }
  }
  scale.nominal.c1 = niceMax(n1);
  scale.nominal.c2 = niceMax(n2);
  scale.real.c1 = niceMax(r1);
  scale.real.c2 = niceMax(r2);
  scaleDetermined = true;
}
function axisLabel(n) {
  return Number(n).toLocaleString("en-US");
}
document.addEventListener("DOMContentLoaded", () => {
  function setOn(group, attr, val) {
    document.querySelectorAll(group + " .sel").forEach(b => b.classList.toggle("on", b.dataset[attr] === val));
  }
  setOn("#varSel", "v", "baseline");
  setOn("#stressSel", "s", "normal");
  document.querySelectorAll("#varSel .sel").forEach(b => b.onclick = () => { setOn("#varSel", "v", b.dataset.v); loadCharts(); });
  document.querySelectorAll("#stressSel .sel").forEach(b => b.onclick = () => { setOn("#stressSel", "s", b.dataset.s); loadCharts(); });
  document.querySelectorAll("#valSel .sel").forEach(b => b.onclick = () => { setOn("#valSel", "val", b.dataset.val); loadCharts(); });
  setOn("#valSel", "val", "nominal");
  loadCharts();
});
async function loadCharts() {
  const v = document.querySelector("#varSel .sel.on")?.dataset.v || "baseline";
  const s = document.querySelector("#stressSel .sel.on")?.dataset.s || "normal";
  window.currentVal = document.querySelector("#valSel .sel.on")?.dataset.val || "nominal";
  if (!scaleDetermined) await determineScale();
  fetch("/api/pf-simulate?v=" + v + "&s=" + s)
    .then(r => r.json())
    .then(data => {
      drawChart1(data.months); drawChart2(data.months); drawYearAxis(data.months);
      const st = document.getElementById("simStatus");
      if (st) st.textContent = data.sim_name ? ("Simulation: " + data.sim_name) : "No simulation linked for this variant.";
    })
    .catch(() => {});
}
function drawYearAxis(months) {
  const el = document.getElementById("yearAxis");
  if (!el || !months.length) return;
  el.innerHTML = "";
  const w = 900;
  for (let y = 0; y <= 40; y += 5) {
    const x = 40 + (y * 12) * (w - 40) / months.length;
    const s = document.createElement("span");
    s.textContent = y;
    s.style.cssText = "position:absolute;left:" + x + "px;transform:translateX(-50%)";
    el.appendChild(s);
  }
}
function fmt(n) {
  if (n == null || isNaN(n)) return "—";
  return Number(n).toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});
}
function tipTable(title, rows) {
  let h = "<b>" + title + "</b><table><tr><th></th><th>Nominal</th><th>Real</th></tr>";
  rows.forEach(r => { h += "<tr><td>" + r[0] + "</td><td>" + fmt(r[1]) + "</td><td>" + fmt(r[2]) + "</td></tr>"; });
  return h + "</table>";
}
function showTip(e, html) {
  let tip = document.getElementById("tip");
  if (!tip) { tip = document.createElement("div"); tip.id = "tip"; document.body.appendChild(tip); }
  tip.innerHTML = html;
  tip.style.display = "block";
  const w = tip.offsetWidth || 280;
  const h = tip.offsetHeight || 120;
  let left = e.clientX + 12;
  let top = e.clientY + 12;
  if (left + w > window.innerWidth - 8) left = Math.max(8, e.clientX - w - 12);
  if (top + h > window.innerHeight - 8) top = Math.max(8, e.clientY - h - 12);
  tip.style.left = left + "px";
  tip.style.top = top + "px";
}
function hideTip() { const t = document.getElementById("tip"); if (t) t.remove(); }
function drawChart1(months) {
  const c = document.getElementById("chart1");
  const ctx = c.getContext("2d");
  ctx.clearRect(0, 0, c.width, c.height);
  const max = (currentVal === "real" ? scale.real.c1 : scale.nominal.c1) || 1;
  const h = c.height / 2;
  ctx.strokeStyle = "#ccc";
  ctx.beginPath(); ctx.moveTo(40, h); ctx.lineTo(c.width, h); ctx.stroke();
  ctx.fillStyle = "#666"; ctx.font = "10px sans-serif"; ctx.textAlign = "right";
  ctx.fillText(axisLabel(max), 35, 12);
  ctx.fillText("0", 35, h + 3);
  ctx.fillText("-" + axisLabel(max), 35, c.height - 4);
  months.forEach((m, i) => {
    const x = 40 + i * (c.width - 40) / months.length;
    const w = (c.width - 40) / months.length;
    if (m.bank < 0) { ctx.fillStyle = "#ffcdd2"; }
    else if (m.emergency) { ctx.fillStyle = "#ffe082"; }
    else { ctx.fillStyle = "#c8e6c9"; }
    ctx.fillRect(x, 0, w, c.height);
    const inc = currentVal === "real" ? m.income_real : m.income;
    const exp = currentVal === "real" ? m.expenses_real : m.expenses;
    if (inc > 0) { ctx.fillStyle = "#4caf50"; ctx.fillRect(x, h - inc / max * h, w, inc / max * h); }
    if (exp > 0) { ctx.fillStyle = "#ff9800"; ctx.fillRect(x, h, w, exp / max * h); }
  });
  ctx.strokeStyle = "#000"; ctx.lineWidth = 1.5;
  ctx.beginPath();
  months.forEach((m, i) => {
    const x = 40 + i * (c.width - 40) / months.length;
    const net = currentVal === "real" ? m.net_real : m.net;
    const y = h - net / max * h;
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.stroke();
  c.onmousemove = function(e) {
    const rect = c.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const i = Math.floor((mx - 40) / (c.width - 40) * months.length);
    if (i >= 0 && i < months.length) {
      const m = months[i];
      const rows = [
        [_t("Income (other)"), m.income, m.income_real],
        [_t("Energy sold"), m.energy_sold, m.energy_sold_real],
        [_t("Interest (bank)"), m.interest, m.interest_real],
        [_t("Month balance"), m.month_balance, m.month_balance_real],
        [_t("Expenses (other)"), m.expenses, m.expenses_real],
        [_t("Energy purchased"), m.energy_purchased, m.energy_purchased_real],
        [_t("Depreciation/maintenance"), m.depreciation, m.depreciation_real],
        [_t("Downpayment credit"), m.credit_payment, m.credit_payment_real],
      ];
      showTip(e, tipTable("Year " + m.year + " month " + (m.month + 1) + " (" + m.phase + ")", rows));
    }
  };
  c.onmouseleave = hideTip;
}
function drawChart2(months) {
  const c = document.getElementById("chart2");
  const ctx = c.getContext("2d");
  ctx.clearRect(0, 0, c.width, c.height);
  const max = (currentVal === "real" ? scale.real.c2 : scale.nominal.c2) || 1;
  const h = c.height / 2;
  ctx.strokeStyle = "#ccc";
  ctx.beginPath(); ctx.moveTo(40, h); ctx.lineTo(c.width, h); ctx.stroke();
  ctx.fillStyle = "#666"; ctx.font = "10px sans-serif"; ctx.textAlign = "right";
  ctx.fillText(axisLabel(max), 35, 12);
  ctx.fillText("0", 35, h + 3);
  ctx.fillText("-" + axisLabel(max), 35, c.height - 4);
  months.forEach((m, i) => {
    const x = 40 + i * (c.width - 40) / months.length;
    const w = (c.width - 40) / months.length;
    // background: red if bank < 0, yellow if will be negative within 12 months, green otherwise
    let bg = "#c8e6c9";
    if (m.bank < 0) bg = "#ffcdd2";
    else {
      const future = months.slice(i, i + 12);
      if (future.some(f => f.bank < 0)) bg = "#ffe082";
    }
    ctx.fillStyle = bg;
    ctx.fillRect(x, 0, w, c.height);
    const bank = currentVal === "real" ? m.bank_real : m.bank;
    const cred = currentVal === "real" ? m.credit_real : m.credit;
    if (bank > 0) { ctx.fillStyle = "#4caf50"; ctx.fillRect(x, h - bank / max * h, w, bank / max * h); }
    if (cred > 0) { ctx.fillStyle = "#f44336"; ctx.fillRect(x, h, w, cred / max * h); }
  });
  ctx.strokeStyle = "#000"; ctx.lineWidth = 1.5;
  ctx.beginPath();
  months.forEach((m, i) => {
    const x = 40 + i * (c.width - 40) / months.length;
    const bank = currentVal === "real" ? m.bank_real : m.bank;
    const cred = currentVal === "real" ? m.credit_real : m.credit;
    const y = h - (bank - cred) / max * h;
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.stroke();
  c.onmousemove = function(e) {
    const rect = c.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const i = Math.floor((mx - 40) / (c.width - 40) * months.length);
    if (i >= 0 && i < months.length) {
      const m = months[i];
      const rows = [
        [_t("Bank account"), m.bank, m.bank_real],
        [_t("Rest value all credits"), m.credit, m.credit_real],
      ];
      (m.credits || []).forEach(c => {
        if (c.rest > 0) rows.push([c.label, c.rest, c.rest]);
      });
      showTip(e, tipTable("Year " + m.year + " month " + (m.month + 1), rows));
    }
  };
  c.onmouseleave = hideTip;
}
</script>

<script>
(function() {
  function reportField(el) {
    const project = (document.getElementById("projName") && document.getElementById("projName").value) || localStorage.getItem("pf_project") || "";
    if (!project) return;
    localStorage.setItem("pf_project", project);
    const path = location.pathname.split("/").pop() || "";
    const letter = path === "baseline" ? "BASELINE" : ("abcde".includes(path) ? path.toUpperCase() : "");
    const body = "project=" + encodeURIComponent(project) + "&name=" + encodeURIComponent(el.name || el.id) + "&value=" + encodeURIComponent(el.value || "") + (letter ? "&letter=" + letter : "");
    fetch("/project-finance/field", {
      method: "POST",
      headers: {"Content-Type": "application/x-www-form-urlencoded"},
      body: body,
      keepalive: true
    }).catch(() => {});
  }
  function fillForm(data) {
    if (!data) return;
    Object.keys(data).forEach(k => {
      const el = document.querySelector("[name='" + k + "']") || document.getElementById(k);
      if (el && "value" in el) el.value = data[k];
    });
  }
  async function loadProjects() {
    const sel = document.getElementById("projPick");
    if (!sel) return;
    const r = await fetch("/api/projects");
    const js = await r.json();
    sel.innerHTML = "<option value=''>— select —</option>";
    (js.projects || []).forEach(p => {
      const o = document.createElement("option");
      o.value = p.name; o.textContent = p.name;
      sel.appendChild(o);
    });
    const stored = localStorage.getItem("pf_project");
    if (stored) sel.value = stored;
  }
  async function loadSims(project) {
    const sel = document.getElementById("simPick");
    if (!sel || !project) return;
    const r = await fetch("/api/projects");
    const js = await r.json();
    const proj = (js.projects || []).find(p => p.name === project);
    sel.innerHTML = "<option value=''>— select —</option>";
    if (proj) (proj.sims || []).forEach(s => {
      const o = document.createElement("option");
      o.value = s; o.textContent = s;
      sel.appendChild(o);
    });
  }
  async function loadData(project, sim) {
    const r = await fetch("/api/project-finance?project=" + encodeURIComponent(project) + (sim ? "&sim=" + encodeURIComponent(sim) : ""));
    const js = await r.json();
    fillForm(js.data || {});
    if (document.getElementById("projName")) document.getElementById("projName").value = project;
    if (sim && document.getElementById("simName")) {
      const m = sim.match(/\\d{4}-\\d{2}-\\d{2} \\d{4} (.+)/);
      document.getElementById("simName").value = m ? m[1] : sim;
    }
  }
  document.addEventListener("DOMContentLoaded", async () => {
    await loadProjects();
    const projPick = document.getElementById("projPick");
    const simPick = document.getElementById("simPick");
    // Server already filled the form from PF_STORE. Only load sims list for the current project.
    const last = localStorage.getItem("pf_project") || (document.getElementById("projName") && document.getElementById("projName").value) || "";
    if (last && projPick) {
      projPick.value = last;
      await loadSims(last);
      const full = localStorage.getItem("pf_sim") || "";
      if (simPick && full) {
        for (const o of simPick.options) {
          if (o.value === full) { simPick.value = full; break; }
        }
        const m = full.match(/\\d{4}-\\d{2}-\\d{2} \\d{4} (.+)/);
        const nameEl = document.getElementById("simName");
        if (nameEl && !nameEl.value) nameEl.value = m ? m[1] : full;
      }
    }
    if (projPick) {
      projPick.addEventListener("change", () => {
        const p = projPick.value;
        if (!p) return;
        localStorage.setItem("pf_project", p);
        location.href = "/project-finance?project=" + encodeURIComponent(p);
      });
    }
    if (simPick) {
      simPick.addEventListener("change", () => {
        const s = simPick.value;
        if (!s) return;
        localStorage.setItem("pf_sim", s);
        location.href = "/project-finance?sim=" + encodeURIComponent(s);
      });
    }
    document.querySelectorAll("input, select, textarea").forEach(el => {});
    // When a house simulation is chosen, clear electricity and fossil and show "by object"
    function clearEnergy(sfx) {
      const el = document.querySelector("input[name='el_" + sfx + "']");
      const fos = document.querySelector("input[name='fos_" + sfx + "']");
      if (el) { el.value = ""; el.placeholder = "by object"; el.readOnly = true; }
      if (fos) { fos.value = ""; fos.placeholder = "by object"; fos.readOnly = true; }
    }
    ["n","e","p"].forEach(sfx => {
      const sim = document.querySelector("select[name='sim_" + sfx + "']");
      if (!sim) return;
      if (sim.value) clearEnergy(sfx);
      sim.addEventListener("change", () => {
        if (sim.value) clearEnergy(sfx);
        else {
          const el = document.querySelector("input[name='el_" + sfx + "']");
          const fos = document.querySelector("input[name='fos_" + sfx + "']");
          if (el) { el.readOnly = false; el.placeholder = ""; }
          if (fos) { fos.readOnly = false; fos.placeholder = ""; }
        }
      });
    });
    // flush on leaving — send the whole form so nothing is lost
    function flushNow() {}
    window.addEventListener("beforeunload", flushNow);
    document.querySelectorAll("nav a").forEach(a => {
      a.addEventListener("click", function (e) {
        flushNow();
      });
    });
  });
})();
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
    from pege_energy_sim_common import demand_form_html, demand_to_form_values, parse_demand_fields  # noqa: E402
except Exception as _exc:
    print("WARNING: demand_model.py not loaded:", _exc)
    demand_form_html = _missing_demand_html
    demand_to_form_values = _missing_demand
    parse_demand_fields = _missing_demand

try:
    from pege_energy_sim_common import (  # noqa: E402
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
    from pege_energy_sim_common import set_language
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
    raise SystemExit(1)


_need_packages()

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
N_ROWS = 25
# Folder the process was started in. Objects, locations and compares live here.
START_DIR = Path.cwd().resolve()

LAST_FORM: dict[str, str] = {}
BASE_URL = f"http://{HOST}:{DEFAULT_PORT}"
DEFAULT_OBJECT = "GEMINI-60-house-with-garage"

def open_browser(url: str):
    """Open the system default browser."""
    import webbrowser
    print(f"{datetime.now():%Y-%m-%d %H:%M}  opening default browser {url}")
    webbrowser.open(url)

def _pdf_lang() -> str:
    return LAST_FORM.get("pdf_language") or "english"

def startup_form_values() -> dict[str, str]:
    return dict(LAST_FORM) if LAST_FORM else {}





def page_shell(title: str, body: str) -> bytes:
    link_script = """
<script>
(function() {
  let focused = null;
  let lastField = null;
  let lastValue = "";
  function pageName() {
    const path = location.pathname.split("/").pop() || "general";
    return path === "project-finance" ? "general" : path;
  }
  function updateLinks() {
    if (!focused || !focused.name) return;
    const params = new URLSearchParams();
    params.set("page", pageName());
    params.set("field", focused.name);
    params.set("value", focused.value || "");
    const proj = document.getElementById("projName");
    if (proj && proj.value) params.set("project", proj.value);
    const qs = params.toString();
    document.querySelectorAll("a").forEach(a => {
      if (!a.href || a.href.startsWith("javascript:")) return;
      const base = a.href.split("?")[0];
      a.href = base + "?" + qs;
    });
  }
  function reportField(field, value) {
    const proj = document.getElementById("projName");
    const project = (proj && proj.value) || localStorage.getItem("pf_project") || "";
    if (!project || !field) return;
    const body = "project=" + encodeURIComponent(project) + "&page=" + encodeURIComponent(pageName()) + "&field=" + encodeURIComponent(field) + "&value=" + encodeURIComponent(value);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/project-finance/field", false);
    xhr.setRequestHeader("Content-Type", "application/x-www-form-urlencoded");
    xhr.send(body);
  }
  function resetLinks() {
    document.querySelectorAll("a").forEach(a => {
      if (!a.href || a.href.startsWith("javascript:")) return;
      a.href = a.href.split("?")[0];
    });
  }
  function check() {
    const cur = document.activeElement;
    const curField = (cur && cur.name) ? cur.name : null;
    const curValue = (cur && "value" in cur) ? cur.value : "";
    if (curField === lastField) {
      if (curValue !== lastValue) {
        lastValue = curValue;
        updateLinks();
      }
    } else {
      if (lastField) reportField(lastField, lastValue);
      lastField = curField;
      lastValue = curValue;
      focused = cur;
      resetLinks();
      updateLinks();
    }
  }
  setInterval(check, 200);
})();
</script>
"""
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
    font: 14px/1.4 "Segoe UI", DejaVu Sans, sans-serif; color: #1a1a1a; overflow-x: hidden; }}
  header {{ background: var(--eu); color: #fff; position: relative; padding: 12px 20px; }}
  header h1 {{ margin: 0; font-size: 17px; font-weight: 650; letter-spacing: .02em; }}
  header p {{ margin: 4px 0 0; font-size: 12px; opacity: .85; }}
  main {{ max-width: 1180px; margin: 16px auto 40px; background: #fff;
    border: 1px solid var(--line); padding: 16px 18px 22px; overflow-x: auto; }}
  table {{ width: 100%; table-layout: fixed; border-collapse: collapse; }}
  th, td {{ word-wrap: break-word; overflow-wrap: break-word; }}
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
    overflow-wrap: break-word; word-break: break-all;
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
  <a href="#" onclick="document.getElementById('verbox').style.display='block';return false;" style="position:absolute;right:0;top:0;width:80px;height:100%;"></a>
  <div id="verbox" style="display:none;position:absolute;right:12px;top:8px;background:#fff8d6;color:#6a5200;padding:8px 12px;border-radius:4px;max-width:360px;font-size:13px">
    <a href="/about?view=remote" style="color:inherit;text-decoration:none">A newer version is listed.</a>
  </div>
  <nav class="menu">
    <a href="/files">Files</a>
    <a href="/form">Object house</a>
    <a href="/compare-houses">Compare houses</a>
    <a href="/project-finance?view=general">Project finance</a>
    <a href="/objects">Other objects</a>
    <a href="/community">Community</a>
    <a href="/compare-communities">Compare communities</a>
    <a href="/pdf-output">PDF output</a>
    <a href="/about">About</a>
  </nav>
</header>
<main>
{body}
</main>
{link_script}
</body>
</html>
"""
    return doc.encode("utf-8")



PDF_PROGRESS: list = []

def pf_build_pdf(progress=None) -> str | None:
    """Build the project-finance PDF. progress is an optional callback(msg)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    def log(msg):
        PDF_PROGRESS.append(msg)
        if progress:
            progress(msg)

    PDF_PROGRESS.clear()
    pf_ensure_loaded()
    from pege_energy_sim_common import set_language, _pdf_lang, _t
    set_language(_pdf_lang())
    project = PF_STORE.get("project") or ""
    if not project:
        return None
    folder = pf_folder(project)
    folder.mkdir(parents=True, exist_ok=True)
    selected = (PF_STORE.get("selected_sim") or "").strip()
    if not selected or selected in ("simulation", "default"):
        sims = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        selected = sims[0].stem if sims else f"{datetime.now().strftime('%Y-%m-%d %H%M')} simulation"
    pdf_path = folder / f"{selected}.pdf"
    log(f"Writing {pdf_path.name}")

    data = PF_STORE.get("data") or {}
    variants = PF_STORE.get("variants") or {}

    HEADER = "#3d5a80"
    ALT = ("#ffffff", "#f0f0f0")  # white / light grey only

    def style_table(tbl, header_rows=1):
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(7.5)
        for (r, c), cell in tbl.get_celld().items():
            cell.set_edgecolor("#cccccc")
            cell.set_linewidth(0.3)
            if r < header_rows:
                cell.set_facecolor(HEADER)
                cell.set_text_props(color="white", fontweight="bold")
            else:
                cell.set_facecolor(ALT[(r - header_rows) % 2])

    def new_page(title):
        fig = plt.figure(figsize=(11.69, 8.27))
        fig.suptitle(title, fontsize=13, fontweight="bold", y=0.97)
        return fig

    with PdfPages(str(pdf_path)) as pdf:
        # Page 1
        log("Page 1: global and individual")
        fig = new_page(f"{project} — {selected}")
        ax = fig.add_axes([0.03, 0.03, 0.94, 0.90])
        ax.axis("off")
        ax.text(0.0, 0.98, _t("Global situation"), fontsize=11, fontweight="bold", va="top")
        g_rows = [
            ["", _t("value")],
            [_t("Bank interest %"), str(data.get("bank_int") or "")],
            [_t("Credit interest %"), str(data.get("credit_int") or "")],
            [_t("General inflation %"), str(data.get("inf_gen") or data.get("inf_avg") or "")],
            [_t("Electricity inflation %"), str(data.get("inf_el") or "")],
            [_t("Fossil inflation %"), str(data.get("inf_fos") or "")],
        ]
        t1 = ax.table(cellText=g_rows, loc="upper left", cellLoc="left",
                      colWidths=[0.28, 0.14], bbox=[0.0, 0.62, 0.42, 0.32])
        style_table(t1)
        c_rows = [
            ["", "12-20 y", "20-35 y", ">35 y", "ground"],
            [_t("Credit years"), str(data.get("y_12_20") or ""), str(data.get("y_20_35") or ""), str(data.get("y_35") or ""), str(data.get("y_ground") or "")],
            [_t("Depreciation % / year"), str(data.get("p_12_20") or ""), str(data.get("p_20_35") or ""), str(data.get("p_35") or ""), str(data.get("p_ground") or "")],
        ]
        t2 = ax.table(cellText=c_rows, loc="upper left", cellLoc="center",
                      colWidths=[0.16, 0.08, 0.08, 0.08, 0.08], bbox=[0.50, 0.62, 0.48, 0.32])
        style_table(t2)
        ax.text(0.0, 0.58, _t("Individual situation"), fontsize=11, fontweight="bold", va="top")
        ax.text(0.52, 0.58, _t("Jump to"), fontsize=11, fontweight="bold", va="top")
        base = variants.get("BASELINE") or {}
        i_rows = [
            ["", _t("value")],
            [_t("Income increase %"), str(base.get("inc_inc") or "")],
            [_t("On bank account"), str(base.get("base_money") or "")],
            [_t("Years to pension"), str(base.get("years_pension") or "")],
            [_t("Decrease pension % / year emergency"), str(base.get("pens_dec") or "")],
            [_t("Income normal"), str(base.get("inc_n") or "")],
            [_t("Income emergency"), str(base.get("inc_e") or "")],
            [_t("Income pension"), str(base.get("inc_p") or "")],
            [_t("Expenses normal"), str(base.get("exp_n") or "")],
            [_t("Expenses emergency"), str(base.get("exp_e") or "")],
            [_t("Expenses pension"), str(base.get("exp_p") or "")],
        ]
        t3 = ax.table(cellText=i_rows, loc="upper left", cellLoc="left",
                      colWidths=[0.28, 0.12], bbox=[0.0, 0.02, 0.42, 0.52])
        style_table(t3)
        # Page-number table: rows = scenarios, columns = stress levels
        jump_header = ["", _t("Normal"), _t("Stress 1"), _t("Stress 2"), _t("Stress 3"), _t("Stress 4")]
        jump_rows = [jump_header]
        letters = [("baseline", "BASELINE"), ("a", "A"), ("b", "B"), ("c", "C"), ("d", "D"), ("e", "E")]
        pg = 2
        for code, key in letters:
            v = variants.get(key) or {}
            if not any(str(x).strip() for x in v.values()):
                continue
            # scenario page at pg, then 5 stresses * 4 pages (chart + 3 tables)
            row = [key]
            chart_pg = pg + 1
            for _ in range(5):
                row.append(str(chart_pg))
                chart_pg += 4
            jump_rows.append(row)
            pg += 1 + 5 * 4
        jt = ax.table(cellText=jump_rows, loc="upper left", cellLoc="center",
                      colWidths=[0.10, 0.07, 0.07, 0.07, 0.07, 0.07], bbox=[0.50, 0.20, 0.48, 0.35])
        style_table(jt)
        pdf.savefig(fig); plt.close(fig)
        # Scenario pages
        for code, key in letters:
            v = variants.get(key) or {}
            if not any(str(x).strip() for x in v.values()):
                continue
            log(f"{_t('Scenario')} {key}")
            fig = new_page(f"{_t('Scenario')} {key}")
            ax = fig.add_axes([0.06, 0.06, 0.88, 0.86])
            ax.axis("off")
            y = 0.95
            desc = v.get(f"{code}_description") or v.get("description") or v.get("baseline_description") or ""
            if desc:
                # wrap description into multiple lines
                import textwrap
                wrapped = textwrap.fill(desc, width=100)
                ax.text(0, y, wrapped, fontsize=8, va="top")
                y -= 0.04 * (wrapped.count("\n") + 2)
            for sfx, label in [("n", _t("Normal")), ("e", _t("Emergency")), ("p", _t("Pension"))]:
                on = str(v.get(f"obj_{sfx}") or "").strip()
                sn = str(v.get(f"sim_{sfx}") or "").strip()
                if on and sn:
                    rel = f"../../objects/{on}/{sn}.pdf"
                    txt = ax.text(0, y, f"{label}: {on} / {sn}", fontsize=8, va="top", color="#003399")
                    try:
                        txt.set_url(rel)
                    except Exception:
                        pass
                    y -= 0.04
            y -= 0.02
            # Credit years from global + allocation
            cred = [
                ["", "12-20 y", "20-35 y", ">35 y", "ground"],
                [_t("Credit years"), str(data.get("y_12_20") or ""), str(data.get("y_20_35") or ""), str(data.get("y_35") or ""), str(data.get("y_ground") or "")],
                [_t("Amount"), str(v.get("inv_12_20") or ""), str(v.get("inv_20_35") or ""), str(v.get("inv_35") or ""), str(v.get("inv_ground") or "")],
                [_t("From bank"), str(v.get("bank_12_20") or ""), str(v.get("bank_20_35") or ""), str(v.get("bank_35") or ""), str(v.get("bank_ground") or "")],
            ]
            t = ax.table(cellText=cred, loc="upper left", cellLoc="center",
                         colWidths=[0.18, 0.16, 0.16, 0.16, 0.16], bbox=[0, max(0.05, y-0.22), 0.82, 0.18])
            style_table(t)
            pdf.savefig(fig); plt.close(fig)

            for stress, slabel in [("normal", _t("Normal")), ("s1", _t("Stress 1")), ("s2", _t("Stress 2")), ("s3", _t("Stress 3")), ("s4", _t("Stress 4"))]:
                result = pf_simulate(code, stress)
                months = result.get("months") or []
                if not months:
                    continue
                log(f"  {slabel}")
                # Charts with real values and phase background
                fig = new_page(f"{key} — {slabel} — charts (real)")
                ax1 = fig.add_axes([0.08, 0.55, 0.88, 0.35])
                xs = list(range(len(months)))
                # background by bank status, grouped into runs
                def status_col(m):
                    if (m.get("bank") or 0) < 0:
                        return "#ffcdd2"
                    if m.get("emergency"):
                        return "#ffe082"
                    return "#c8e6c9"
                run_start, run_col = 0, status_col(months[0]) if months else "#c8e6c9"
                for i, m in enumerate(months):
                    c = status_col(m)
                    if c != run_col:
                        ax1.axvspan(run_start - 0.5, i - 0.5, color=run_col, linewidth=0)
                        run_start, run_col = i, c
                if months:
                    ax1.axvspan(run_start - 0.5, len(months) - 0.5, color=run_col, linewidth=0)
                inc = [m.get("income_real") or 0 for m in months]
                exp = [-(m.get("expenses_real") or 0) for m in months]
                ax1.bar(xs, inc, width=1, color="#4caf50", linewidth=0)
                ax1.bar(xs, exp, width=1, color="#ff9800", linewidth=0)
                nets = [m.get("net_real") or 0 for m in months]
                ax1.plot(xs, nets, color="black", linewidth=0.8)
                ax1.axhline(0, color="#ccc", linewidth=0.5)
                ax1.set_xlim(-0.5, len(months) - 0.5)
                ax1.set_xticks([i*12 for i in range(0, 41, 5)])
                ax1.set_xticklabels([str(i) for i in range(0, 41, 5)])
                ax1.set_title("Income / expenses (real)", fontsize=9)
                ax2 = fig.add_axes([0.08, 0.08, 0.88, 0.35])
                run_start, run_col = 0, status_col(months[0]) if months else "#c8e6c9"
                for i, m in enumerate(months):
                    c = status_col(m)
                    if c != run_col:
                        ax2.axvspan(run_start - 0.5, i - 0.5, color=run_col, linewidth=0)
                        run_start, run_col = i, c
                if months:
                    ax2.axvspan(run_start - 0.5, len(months) - 0.5, color=run_col, linewidth=0)
                bank = [m.get("bank_real") or 0 for m in months]
                cred_v = [-(m.get("credit_real") or 0) for m in months]
                ax2.fill_between(xs, 0, bank, color="#a5d6a7", linewidth=0, alpha=0.6)
                ax2.fill_between(xs, 0, cred_v, color="#ef9a9a", linewidth=0, alpha=0.6)
                ax2.plot(xs, bank, color="black", linewidth=0.8)
                ax2.axhline(0, color="#ccc", linewidth=0.5)
                ax2.set_xlim(-0.5, len(months) - 0.5)
                ax2.set_xticks([i*12 for i in range(0, 41, 5)])
                ax2.set_xticklabels([str(i) for i in range(0, 41, 5)])
                ax2.set_title("Bank account / credits (real)", fontsize=9)
                pdf.savefig(fig); plt.close(fig)

                year_groups = [(0, 5, 10), (15, 20, 25), (30, 35, 40)]
                for yg in year_groups:
                    fig = new_page(f"{key} — {slabel} — years {yg[0]}, {yg[1]}, {yg[2]}")
                    def month_at(year, month):
                        for m in months:
                            if m.get("year") == year and m.get("month") == month:
                                return m
                        return None
                    header = ["", f"nominal {yg[0]}", f"nominal {yg[1]}", f"nominal {yg[2]}", f"real {yg[0]}", f"real {yg[1]}", f"real {yg[2]}"]
                    jan_labels = [
                        (_t("Income (other)"), "income", "income_real"),
                        (_t("Energy sold"), "energy_sold", "energy_sold_real"),
                        (_t("Interest (bank)"), "interest", "interest_real"),
                        (_t("Month balance"), "month_balance", "month_balance_real"),
                        (_t("Expenses (other)"), "expenses", "expenses_real"),
                        (_t("Energy purchased"), "energy_purchased", "energy_purchased_real"),
                        (_t("Depreciation/maintenance"), "depreciation", "depreciation_real"),
                        (_t("Downpayment credit"), "credit_payment", "credit_payment_real"),
                    ]
                    def make_rows(month_idx, labels):
                        rows = [["", f"nominal {yg[0]}", f"nominal {yg[1]}", f"nominal {yg[2]}", f"real {yg[0]}", f"real {yg[1]}", f"real {yg[2]}"]]
                        for lab, kn, kr in labels:
                            row = [lab]
                            for y in yg:
                                m = month_at(y, month_idx)
                                row.append(f"{m.get(kn, 0):,.0f}" if m else "—")
                            for y in yg:
                                m = month_at(y, month_idx)
                                row.append(f"{m.get(kr, 0):,.0f}" if m else "—")
                            rows.append(row)
                        return rows
                    ax = fig.add_axes([0.02, 0.02, 0.96, 0.92])
                    ax.axis("off")
                    ax.text(0.0, 0.98, _t("January"), fontsize=10, fontweight="bold", va="top")
                    t1 = ax.table(cellText=make_rows(0, jan_labels), loc="upper left", cellLoc="right",
                                  colWidths=[0.20, 0.13, 0.13, 0.13, 0.13, 0.13, 0.13], bbox=[0, 0.66, 0.96, 0.30])
                    style_table(t1)
                    ax.text(0.0, 0.64, _t("July"), fontsize=10, fontweight="bold", va="top")
                    t2 = ax.table(cellText=make_rows(6, jan_labels), loc="upper left", cellLoc="right",
                                  colWidths=[0.20, 0.13, 0.13, 0.13, 0.13, 0.13, 0.13], bbox=[0, 0.32, 0.96, 0.30])
                    style_table(t2)
                    ax.text(0.0, 0.30, _t("December bank & credit"), fontsize=10, fontweight="bold", va="top")
                    dec_labels = [(_t("Bank account"), "bank", "bank_real"), (_t("Rest value all credits"), "credit", "credit_real")]
                    t3 = ax.table(cellText=make_rows(11, dec_labels), loc="upper left", cellLoc="right",
                                  colWidths=[0.20, 0.13, 0.13, 0.13, 0.13, 0.13, 0.13], bbox=[0, 0.05, 0.96, 0.22])
                    style_table(t3)
                    pdf.savefig(fig); plt.close(fig)
    log("Done")
    return str(pdf_path)


def pf_build_zip() -> str | None:
    """ZIP with objects/ and project-finance/ containing all involved files."""
    import zipfile
    pf_ensure_loaded()
    project = PF_STORE.get("project") or ""
    if not project:
        return None
    folder = pf_folder(project)
    selected = (PF_STORE.get("selected_sim") or "").strip()
    if not selected:
        sims = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        selected = sims[0].stem if sims else "export"
    zip_path = folder / f"{selected}.zip"
    variants = PF_STORE.get("variants") or {}
    from pege_energy_sim_common import data_root
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        # project-finance JSON and PDF
        for ext in (".json", ".pdf"):
            p = folder / f"{selected}{ext}"
            if p.is_file():
                z.write(p, f"project-finance/{project}/{p.name}")
        # involved object files
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
                        z.write(f, f"objects/{on}/{f.name}")
    return str(zip_path)


def pf_build_7z() -> str | None:
    """7z with objects/ and project-finance/ containing all involved files."""
    import subprocess, shutil, tempfile
    pf_ensure_loaded()
    project = PF_STORE.get("project") or ""
    if not project:
        return None
    folder = pf_folder(project)
    selected = (PF_STORE.get("selected_sim") or "").strip()
    if not selected:
        last = Path(PF_STORE.get("last_pdf") or "")
        selected = last.stem if last.stem else ""
    if not selected:
        sims = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        selected = sims[0].stem if sims else "export"
    out_path = folder / f"{selected}.7z"
    variants = PF_STORE.get("variants") or {}
    from pege_energy_sim_common import data_root
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
    if not files or not shutil.which("7z"):
        return None
    with tempfile.TemporaryDirectory() as td:
        for src, arc in files:
            dest = Path(td) / arc
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes())
        subprocess.run(["7z", "a", "-t7z", str(out_path), "."], cwd=td, check=False, capture_output=True)
    return str(out_path) if out_path.is_file() else None
