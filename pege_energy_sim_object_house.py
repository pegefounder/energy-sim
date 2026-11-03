#!/usr/bin/env python3
"""Object house form and helpers."""

from __future__ import annotations

import html
import json
import numpy as np
import pandas as pd
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode

from pege_energy_sim_common import (
    LOCAL_VERSION,
    CONFIG, LAST_FORM, N_ROWS, data_root, first, sanitize_name, location_remark,
)
from pege_energy_sim_project_finance import page_shell
from pege_energy_sim_common import demand_form_html, DEFAULTS, default_prices, parse_demand_fields, demand_to_form_values
from pege_energy_sim_common import thermal_and_loads, space_thermal

def form_html(values: dict | None = None) -> bytes:
    v = values or {}
    name = html.escape(str(v.get("name") or ""))
    try:
        _lat = float(str(v.get("lat") or "").replace(",", "."))
        _lon = float(str(v.get("lon") or "").replace(",", "."))
        _stored = location_remark(_lat, _lon, cache_dir=data_root())
    except Exception:
        _stored = ""
    locremark = html.escape(str(v.get("locremark") or _stored))
    lat = html.escape(str(v.get("lat") or ""))
    lon = html.escape(str(v.get("lon") or ""))
    obj = html.escape(str(v.get("object") or ""))
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
<form id="pvform" method="post" action="/run">
  <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:12px">
    <div>
      <label>Select object</label><br>
      <select id="objectPick" style="width:100%"><option value="">— select —</option></select>
      <span id="pickStatus" style="font-size:11px;color:#666"></span>
    </div>
    <div>
      <label>Select simulation</label><br>
      <select id="simPick" style="width:100%"><option value="">— select —</option></select>
    </div>
    <div>
      <label>Select location</label><br>
      <select id="locationPick" style="width:100%"><option value="">— select —</option></select>
    </div>
    <div>
      <label>Object name</label><br>
      <input name="object" id="objectName" value="{obj}" placeholder="" style="width:100%">
      <button type="submit" form="pvform" style="margin-top:6px">Run simulation — create PDF</button>
    </div>
    <div>
      <label>Simulation name</label><br>
      <input name="simname" id="simName" value="{html.escape(str(v.get('simname','')))}" placeholder="" style="width:100%">
      <button type="button" id="saveConfig" style="margin-top:6px">Save configuration</button>
      <button type="button" id="saveNew" style="margin-top:6px">Save as a new configuration</button>
    </div>
    <div>
      <label>Location name</label><br>
      <input name="name" value="{name}" required placeholder="" autocomplete="off" style="width:100%">
      <div style="display:flex;gap:8px;margin-top:8px">
        <div style="flex:1"><label>Latitude</label><br><input name="lat" value="{lat}" required inputmode="decimal" style="width:100%"></div>
        <div style="flex:1"><label>Longitude</label><br><input name="lon" value="{lon}" required inputmode="decimal" style="width:100%"></div>
      </div>
    </div>
  </div>
<div id="locmap" style="height:280px;margin:0 0 14px;border:1px solid #d5dbe6"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
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
  <div style="overflow-x:auto">
  <table id="pvtable">
    <thead><tr><th>#</th><th>Use</th><th>Name</th><th>Slope °</th><th>Azimuth °</th><th>Wp</th><th>Wall dist. m</th><th>Wall height m</th><th>Bifacial</th><th>Bi %</th></tr></thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
  </div>
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
  <div class="actions" style="display:flex;gap:8px">
    <button type="submit">Run — download missing files and write PDF</button>
    <button type="button" id="saveConfig2">Save configuration</button>
    <button type="button" id="saveNew2">Save as a new configuration</button>
  </div>
</form>
<script>
function saveState() {{
  var form = document.getElementById("pvform");
  var data = new FormData(form);
  var state = {{}};
  data.forEach(function(v, k) {{ state[k] = v; }});
  console.log("SAVE", state.slope1);
  var xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/state", false);
  xhr.setRequestHeader("Content-Type", "application/json");
  xhr.send(JSON.stringify(state));
}}
document.querySelectorAll("input, select").forEach(function(el) {{
  el.addEventListener("change", saveState);
}});
function selectByValue(sel, val) {{
  if (!sel || !val) return;
  for (var i = 0; i < sel.options.length; i++) {{
    if (sel.options[i].value === val) {{ sel.selectedIndex = i; return; }}
  }}
}}
fetch("/api/workspace").then(function(r) {{ return r.json(); }}).then(function(d) {{
  var oSel = document.getElementById("objectPick");
  var lSel = document.getElementById("locationPick");
  if (oSel) {{
    (d.objects || []).forEach(function(n) {{
      var o = document.createElement("option");
      o.value = n; o.textContent = n;
      oSel.appendChild(o);
    }});
  }}
  if (lSel) {{
    (d.locations || []).forEach(function(loc) {{
      var o = document.createElement("option");
      o.value = JSON.stringify(loc);
      o.textContent = loc.folder || loc.name || "unknown";
      lSel.appendChild(o);
    }});
  }}
  fetch("/api/state").then(function(r) {{ return r.json(); }}).then(function(s) {{
    console.log("state", s._last_loaded, s.object);
    var objName = document.getElementById("objectName");
    var obj = (objName && objName.value) || s.object || "";
    var loadedPath = s._last_loaded || "";
    selectByValue(oSel, obj);
    var locName = document.querySelector("[name=name]");
    var loc = (locName && locName.value) || s.location || "";
    if (lSel && loc) {{
      for (var i = 0; i < lSel.options.length; i++) {{
        if (lSel.options[i].textContent.indexOf(loc) >= 0) {{ lSel.selectedIndex = i; break; }}
      }}
    }}
    if (obj) {{
      var sSel = document.getElementById("simPick");
      if (sSel) {{
        fetch("/api/object-files?object=" + encodeURIComponent(obj)).then(function(r) {{ return r.json(); }}).then(function(data) {{
          console.log("files", (data.files || []).length, "path", loadedPath);
          var want = "";
          if (loadedPath) {{
            want = loadedPath.split("/").pop().replace(/\\.pdf$/i, "").replace(/\\.json$/i, "");
          }}
          var matched = false;
          (data.files || []).forEach(function(f) {{
            var o = document.createElement("option");
            o.value = f.path || f.name;
            o.textContent = f.name || f.simname;
            if (want && f.name === want) {{
              o.selected = true;
              matched = true;
            }}
            sSel.appendChild(o);
          }});
          // Only if the loaded simulation is not in the list, fall back to a location match.
          if (!matched) {{
            var locName = (document.querySelector("[name=name]") || {{}}).value || "";
            if (locName) {{
              for (var i = 0; i < sSel.options.length; i++) {{
                if (sSel.options[i].textContent.indexOf(locName) >= 0) {{
                  sSel.selectedIndex = i;
                  break;
                }}
              }}
            }}
          }}
        }});
      }}
    }}
  }});
}});
document.getElementById("objectPick").addEventListener("change", function() {{
  saveState();
  var obj = this.value;
  var sSel = document.getElementById("simPick");
  if (!sSel) return;
  sSel.innerHTML = "<option value=''>— select —</option>";
  if (!obj) return;
  fetch("/api/object-files?object=" + encodeURIComponent(obj)).then(function(r) {{ return r.json(); }}).then(function(data) {{
    (data.files || []).forEach(function(f) {{
      var o = document.createElement("option");
      o.value = f.path || f.name; o.textContent = f.name || f.simname;
      sSel.appendChild(o);
    }});
  }});
}});
function clearLocationIfChanged() {{
  var sel = document.getElementById("locationPick");
  if (sel && sel.value) {{
    var nameEl = document.querySelector("[name=name]");
    var latEl = document.querySelector("[name=lat]");
    var lonEl = document.querySelector("[name=lon]");
    var text = sel.options[sel.selectedIndex].textContent || "";
    var m = text.match(/^(.*) - (-?[0-9.]+) - (-?[0-9.]+)$/);
    var ok = m && nameEl && nameEl.value === m[1]
          && latEl && Math.abs(parseFloat(latEl.value) - parseFloat(m[2])) < 0.002
          && lonEl && Math.abs(parseFloat(lonEl.value) - parseFloat(m[3])) < 0.002;
    if (!ok) sel.value = "";
  }}
  // A location change also makes the previously loaded simulation misleading.
  var sim = document.getElementById("simPick");
  if (sim && sim.value) sim.value = "";
  var remark = document.querySelector("[name=locremark]");
  if (remark) remark.value = "";
}}
["name", "lat", "lon"].forEach(function (n) {{
  var el = document.querySelector("[name=" + n + "]");
  if (el) el.addEventListener("input", clearLocationIfChanged);
}});
document.getElementById("locationPick").addEventListener("change", function() {{
  saveState();
  var val = this.value;
  if (!val) return;
  try {{
    var loc = JSON.parse(val);
    var nameEl = document.querySelector("[name=name]");
    var latEl = document.querySelector("[name=lat]");
    var lonEl = document.querySelector("[name=lon]");
    var text = this.options[this.selectedIndex].textContent;
    var name = text.replace(/ - -?[0-9.]+ - -?[0-9.]+$/, "");
    if (nameEl) nameEl.value = name;
    if (latEl) latEl.value = loc.lat || "";
    if (lonEl) lonEl.value = loc.lon || "";
  }} catch (e) {{}}
}});
document.getElementById("simPick").addEventListener("change", function() {{
  saveState();
  var path = this.value;
  if (path) window.location = "/load?json=" + encodeURIComponent(path);
}});
window.addEventListener("beforeunload", saveState);
document.querySelectorAll("nav a").forEach(function(a) {{
  a.addEventListener("click", saveState);
}});
</script>
<script>
function initMap() {{
  if (typeof L === "undefined") {{ setTimeout(initMap, 100); return; }}
  var box = document.getElementById("locmap");
  if (!box) return;
  var latEl = document.querySelector("[name=lat]");
  var lonEl = document.querySelector("[name=lon]");
  var lat = parseFloat(latEl.value) || 48.4;
  var lon = parseFloat(lonEl.value) || 15.989;
  var map = L.map(box).setView([lat, lon], 8);
  window._pegeMap = map;
  L.tileLayer("https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{maxZoom: 18}}).addTo(map);
  var marker = L.marker([lat, lon], {{draggable: true}}).addTo(map);
  window._pegeMarker = marker;
  map.on("click", function(e) {{
    latEl.value = e.latlng.lat.toFixed(3);
    lonEl.value = e.latlng.lng.toFixed(3);
    marker.setLatLng(e.latlng);
    if (typeof clearLocationIfChanged === "function") clearLocationIfChanged();
  }});
  marker.on("dragend", function() {{
    var p = marker.getLatLng();
    latEl.value = p.lat.toFixed(3);
    lonEl.value = p.lng.toFixed(3);
    if (typeof clearLocationIfChanged === "function") clearLocationIfChanged();
  }});
}}
initMap();
document.getElementById("placeGo").addEventListener("click", function () {{
  var q = (document.getElementById("placeSearch").value || "").trim();
  var hit = document.getElementById("placeHit");
  if (!q) {{ hit.textContent = "type a place"; return; }}
  hit.textContent = "searching…";
  fetch("https://nominatim.openstreetmap.org/search?format=json&limit=1&q=" + encodeURIComponent(q))
    .then(function (r) {{ return r.json(); }})
    .then(function (rows) {{
      if (!rows.length) {{ hit.textContent = "not found"; return; }}
      var lat = parseFloat(rows[0].lat), lon = parseFloat(rows[0].lon);
      var latEl = document.querySelector("[name=lat]");
      var lonEl = document.querySelector("[name=lon]");
      latEl.value = lat.toFixed(3);
      lonEl.value = lon.toFixed(3);
      if (window._pegeMap && window._pegeMarker) {{
        window._pegeMarker.setLatLng([lat, lon]);
        window._pegeMap.setView([lat, lon], 10);
      }}
      if (typeof clearLocationIfChanged === "function") clearLocationIfChanged();
      var nameEl = document.querySelector("[name=name]");
      if (nameEl) nameEl.value = "";
      hit.textContent = rows[0].display_name.split(",").slice(0, 2).join(",");
    }})
    .catch(function () {{ hit.textContent = "search failed"; }});
}});
</script>
<script>
function saveConfig(mode) {{
  var form = document.getElementById("pvform");
  form.action = "/save-config";
  if (mode) {{
    var inp = document.createElement("input");
    inp.type = "hidden"; inp.name = "mode"; inp.value = mode;
    form.appendChild(inp);
  }}
  form.submit();
}}
document.getElementById("saveConfig").addEventListener("click", function() {{ saveConfig(""); }});
document.getElementById("saveNew").addEventListener("click", function() {{ saveConfig("new"); }});
document.getElementById("saveConfig2").addEventListener("click", function() {{ saveConfig(""); }});
document.getElementById("saveNew2").addEventListener("click", function() {{ saveConfig("new"); }});
</script>
"""
    return page_shell("PEGE energy sim", body)


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
    outdir_s = first(fields, "outdir")
    outdir = Path(outdir_s).expanduser() if outdir_s else Path.cwd()
    outdir.mkdir(parents=True, exist_ok=True)
    object_name = sanitize_name(first(fields, "object") or "GEMINI-60-house-with-garage")
    demand = parse_demand_fields(fields)
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
            if p.suffix.lower() == ".json":
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    rec["pdf_language"] = data.get("pdf_language", "english")
                    rec["version"] = data.get("software_version", "0.0")
                except Exception:
                    rec["pdf_language"] = "english"
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
BUILD_VERSION = "2026-10-25 16:33"  # matches the ZIP timestamp


# Spot market — house only

def price_band(hour_wt: int) -> str:
    """Single source of truth for price bands (winter time).
    High 06:00–09:00 and 17:00–20:00, low 09:00–15:00, medium the rest.
    Returns 'hi', 'mid' or 'lo'. Used by price keys and the simulation.
    """
    h = int(hour_wt)
    if 6 <= h < 9 or 17 <= h < 20:
        return "hi"
    if 9 <= h < 15:
        return "lo"
    return "mid"


def price_cent(demand: dict, month: int, hour_wt: int, weekend: bool, direction: str) -> float:
    season = "su" if 4 <= int(month) <= 9 else "wi"
    day = "we" if weekend else "wd"
    key = f"px_{season}_{day}_{direction}_{price_band(hour_wt)}"
    prices = demand.get("prices") or {}
    if key in prices:
        return float(prices[key])
    if key in demand:
        return float(demand[key])
    return float(default_prices().get(key, 0.0))



def _maps(demand: dict) -> tuple[dict, dict]:
    cop, kw = {}, {}
    raw_c = demand.get("hp_cop") or {}
    raw_k = demand.get("hp_kw") or {}
    for src, dst in ((raw_c, cop), (raw_k, kw)):
        for k, v in src.items():
            if isinstance(k, str) and "/" in k:
                a, b = k.split("/")
                dst[(int(a), int(b))] = float(v)
            elif isinstance(k, tuple):
                dst[(int(k[0]), int(k[1]))] = float(v)
    return cop, kw


def _hour_wt(ts, utc_offset: int = 1) -> int:
    """Civil hour from longitude-based UTC offset. Default UTC+1."""
    return int((ts.hour + utc_offset) % 24)


def _weekend(ts) -> bool:
    """Saturday or Sunday on the winter-time clock (UTC+1), all year."""
    wd = (ts.weekday() + (1 if int(ts.hour) == 23 else 0)) % 7
    return wd >= 5


def _doy(ts) -> int:
    """1–366 axis. Leap day keeps its own number; later days shift by one."""
    return int(ts.timetuple().tm_yday)



def simulate(combined: pd.DataFrame, demand: dict, log=print, stop=None) -> dict:
    cop_map, kw_map = _maps(demand)
    batt = float(demand.get("battery_kwh") or 0)
    crate = max(0.05, float(demand.get("c_rate") or 0.5))
    c_kw = crate * batt if batt else 1e9
    eta_pv_batt = float(demand.get("eta_pv_batt") or demand.get("eta_charge") or 0.97)
    eta_pv_ac = float(demand.get("eta_pv_ac") or 0.96)
    eta_pv_dc = float(demand.get("eta_pv_dc") or 0.97)
    eta_batt_ac = float(demand.get("eta_batt_ac") or demand.get("eta_discharge") or 0.95)
    eta_batt_dc = float(demand.get("eta_batt_dc") or 0.97)
    eta_ac_dc = float(demand.get("eta_ac_dc") or 0.92)
    eta_c = eta_pv_batt
    eta_d = eta_batt_ac
    soc_min = float(demand.get("soc_min") or 0.1) * batt
    soc_max = float(demand.get("soc_max") or 0.95) * batt
    soc_plan = float(demand.get("soc_plan_min") or 0.2) * batt
    soc_plan = min(soc_max, max(soc_min, soc_plan))
    soc = float(demand.get("soc_start") or 0.5) * batt
    inv = float(demand.get("inverter_kw") or 34)
    grid_kw = float(demand.get("grid_kw") or 0)
    exp_kw = min(grid_kw, float(demand.get("export_kw") if demand.get("export_kw") is not None else grid_kw))
    dc_kw = float(demand.get("dc_charger_kw") or 30)
    caps = list(demand.get("import_cap_kw") or [2.0] * 12)
    if len(caps) < 12:
        caps = (caps + [2.0] * 12)[:12]

    df = combined.sort_values("time").reset_index(drop=True)
    times = df["time"].dt.tz_convert("UTC")
    n = len(df)
    pv = (df["P_all"].to_numpy(dtype=float) / 1000.0).clip(min=0)  # kWh in that hour
    tair = df["T2m"].to_numpy(dtype=float)

    t_heat = float(demand.get("t_heat_on") or 22)
    t_cool = float(demand.get("t_cool_on") or 25)
    t_in = 0.5 * (t_heat + t_cool)
    Cmass = max(0.5, float(demand.get("thermal_mass_kwh_k") or 25.0))
    if batt:
        log(f"Demand sim {n} hours, battery {batt:g} kWh, C-rate {crate:g} → {c_kw:g} kW, mass {Cmass:g} kWh/K")
    else:
        log(f"Demand sim {n} hours, no battery, mass {Cmass:g} kWh/K")

    rows = []
    # plan buffers: kWh intended this hour
    plan_imp = np.zeros(n)
    target_floor = np.full(n, soc_max)
    plan_exp = np.zeros(n)
    hold_kwh = np.zeros(n)

    # precompute loads + longitude-based civil hour
    loads = []
    hwt = np.empty(n, dtype=int)
    months = np.empty(n, dtype=int)
    doys = np.empty(n, dtype=int)
    years = np.empty(n, dtype=int)
    utc_offset = int(round(float(demand.get("lon") or 15) / 15))
    for i in range(n):
        ts = times.iloc[i].to_pydatetime()
        h = _hour_wt(ts, utc_offset)
        hwt[i] = h
        months[i] = ts.month
        doys[i] = _doy(ts)
        years[i] = ts.year
        loads.append(thermal_and_loads(float(tair[i]), ts.month, h, demand, cop_map, kw_map))
    day_yield: dict[int, list[float]] = {}
    cur_key = None
    for k in range(n):
        key = (int(years[k]), int(doys[k]))
        if key != cur_key:
            day_yield.setdefault(int(doys[k]), []).append(0.0)
            cur_key = key
        day_yield[int(doys[k])][-1] += float(pv[k])
    top5 = [0.0] * 367
    top5_rows = []
    for doy, vals in day_yield.items():
        best = sorted(vals, reverse=True)[:5]
        avg = float(sum(best) / len(best)) if best else 0.0
        top5[doy] = avg
        top5_rows.append({"day_of_year": int(doy), "days": len(best), "top5_avg_kwh": round(avg, 3)})
    top5_rows.sort(key=lambda r: r["day_of_year"])

    exp_px = np.zeros(n)
    imp_px = np.zeros(n)
    fixed_ct = float((demand or {}).get("fixed_ct") or 0.0)
    for i in range(n):
        ts = times.iloc[i].to_pydatetime()
        if fixed_ct > 0:
            # Fixed tariff: no export, import only at this price.
            exp_px[i] = -1.0
            imp_px[i] = fixed_ct
        else:
            exp_px[i] = price_cent(demand, int(months[i]), int(hwt[i]), _weekend(ts), "exp")
            imp_px[i] = price_cent(demand, int(months[i]), int(hwt[i]), _weekend(ts), "imp")

    # Demand run: one row per hour. Surplus that cannot leave (price <= 0, or PV above
    # the grid connection) is used to move the room toward the middle of the band.
    # The export run can be a little closer to that middle, so its demand is lower.
    demand_el = np.zeros(n)
    demand_t = np.zeros(n)
    t_d = t_in
    s_d = soc
    for i in range(n):
        pvk = float(pv[i])
        locked = exp_px[i] <= 0 or pvk >= exp_kw - 0.05
        room = max(0.0, soc_max - s_d)
        ch = min(pvk * eta_c, room, c_kw)
        left = max(0.0, pvk - (ch / eta_c if eta_c else 0.0))
        extra = left if locked and (room - ch) < 0.05 else 0.0
        space_d = space_thermal(t_d, float(tair[i]), demand, cop_map, kw_map, extra)
        t_d = space_d["t_next"]
        Lk = loads[i]
        demand_el[i] = (
            Lk["house"] + space_d["heat_el"] + space_d["cool_el"]
            + Lk["dhw_el"] + Lk["night_ac"] + Lk["lunch_dc"]
        )
        demand_t[i] = t_d
        s_d = min(soc_max, s_d + ch)
        need = max(0.0, demand_el[i] - min(left, demand_el[i]))
        take = min(need, max(0.0, s_d - soc_min) * eta_d)
        s_d -= take / eta_d if eta_d else 0.0
        s_d = min(soc_max, max(soc_min, s_d))
    log(f"Demand run {demand_el.sum():.0f} kWh")

    # Export is decided at 15:00 winter time, not at midnight.
    horizon = 120
    soc_end_min = 0.30 * batt
    soc_plan_full = 0.90 * batt

    def forecast(i0, power, soc0):
        """120 h energy balance. Returns end SOC and first hour SOC >= 90%."""
        s = soc0
        hit = None
        j = min(n, i0 + horizon)
        for k in range(i0, j):
            load = loads[k]["own"]
            pvk = float(pv[k])
            room = soc_max - s
            ch = min(pvk * eta_c, max(0.0, room), c_kw)
            s += ch
            left = pvk - (ch / eta_c if eta_c else 0.0)
            from_pv = min(left, load)
            left -= from_pv
            need = load - from_pv
            avail = max(0.0, s - soc_min) * eta_d
            from_b = min(need, avail)
            s -= from_b / eta_d if eta_d else 0.0
            if hwt[k] < 9 or hwt[k] >= 15:
                exp = min(power, left + max(0.0, s - soc_min) * eta_d, exp_kw)
                from_left = min(left, exp)
                s -= (exp - from_left) / eta_d if eta_d else 0.0
            if hit is None and s >= soc_plan_full:
                hit = k
            if s > soc_max:
                s = soc_max
            if s < soc_min:
                s = soc_min
        return s, hit

    def export_power(i0, soc0):
        """Largest export kW whose 120 h end SOC is still at least 30%."""
        if forecast(i0, 0.0, soc0)[0] < soc_end_min:
            return 0.0
        lo, hi = 0.0, exp_kw
        for _ in range(12):
            mid = 0.5 * (lo + hi)
            if forecast(i0, mid, soc0)[0] >= soc_end_min:
                lo = mid
            else:
                hi = mid
        return lo

    recs = []
    last_ym = None
    line = ""
    month_emerg = 0
    for i in range(n):
        if stop and stop():
            raise RuntimeError("Stopped by user.")
        ym = (int(years[i]), int(months[i]))
        if ym != last_ym:
            if last_ym is not None:
                if month_emerg == 0:
                    mark = "."
                elif month_emerg <= 9:
                    mark = str(month_emerg)
                else:
                    mark = "#"
                if line.endswith("."):
                    line = line[:-1] + mark
                    log("PROGRESS " + line)
            month_emerg = 0
            if last_ym is not None and ym[0] != last_ym[0]:
                log("PROGRESS-END")
                line = ""
            if not line:
                line = f"{ym[0]} "
                log("PROGRESS " + line)
            else:
                line += "."
                log("PROGRESS " + line)
            last_ym = ym
        L = loads[i]
        lunch = min(L["lunch_dc"], dc_kw)
        if hwt[i] == 9 or hwt[i] == 15:
            cap_now = float(caps[months[i] - 1])
            s = soc
            short = 0.0
            j = min(n, i + 120)
            for k in range(i, j):
                load = loads[k]["own"]
                pvk = float(pv[k])
                room = soc_max - s
                ch = min(pvk * eta_c, max(0.0, room), c_kw)
                s += ch
                left = pvk - (ch / eta_c if eta_c else 0.0)
                need = max(0.0, load - left)
                avail = max(0.0, s - soc_min) * eta_d
                take = min(need, avail)
                s -= take / eta_d if eta_d else 0.0
                gap = need - take
                if gap > cap_now:
                    missing = gap - cap_now
                    short += missing
                    s += missing / eta_d if eta_d else 0.0
                s = min(soc_max, max(soc_min, s))
            hold_kwh[i:j] = np.maximum(hold_kwh[i:j], short)
            if hwt[i] == 9 and short > 0:
                # Low price first, then medium. High price is not a planned import.
                # 18 cheap hours at the month cap cover a zero-sun day of house,
                # car, heat and DHW without crossing the cap.
                def rank(k):
                    return (0 if price_band(hwt[k]) == "lo" else 1, k)
                slots = [k for k in range(i, j) if price_band(hwt[k]) != "hi"]
                slots.sort(key=rank)
                left = short
                for k in slots:
                    room = max(0.0, cap_now - plan_imp[k])
                    take = min(left, room)
                    plan_imp[k] += take
                    left -= take
                    if left <= 1e-9:
                        break
        if hwt[i] == 15 and exp_kw > 0:
            # Critical path, 66 h from the 24 h mark: 15:00 tomorrow to 09:00 on day +3.
            # Both efficiencies. Surplus yield is stored at charge η. Unmet demand
            # leaves at discharge η. Yield is discounted by hours into the window.
            h24 = min(n, i + 24)
            h90 = min(n, i + 24 + 66)
            level = soc_plan
            min_24 = soc_plan
            for k in range(h24, h90):
                ahead = k - h24 + 1
                y = float(pv[k]) * max(0.0, 1.0 - ahead / 66.0)
                load = float(loads[k]["own"])
                if y >= load:
                    level += (y - load) * eta_c
                else:
                    level -= (load - y) / max(eta_d, 0.2)
                if level < soc_plan:
                    min_24 += soc_plan - level
                    level = soc_plan
                level = min(level, soc_max)
            min_24 = min(soc_max, max(soc_plan, min_24))
            # Empty room from the top-5 yield of the day 24 h ahead.
            doy = int(times[h24 - 1].timetuple().tm_yday) if h24 > i else 1
            top = float(top5[doy]) if doy < len(top5) else 0.0
            house = float(demand.get("house_kwh_day") or 0)
            car = float(demand.get("lunch_kwh") or 0) + float(demand.get("night_kwh") or 0)
            day_hp = 0.0
            if h24 > i:
                day = int(doys[h24 - 1])
                yr = int(years[h24 - 1])
                day_hp = float(sum(
                    loads[k]["heat_el"] + loads[k]["cool_el"] + loads[k]["dhw_el"]
                    for k in range(n) if int(doys[k]) == day and int(years[k]) == yr
                ))
            empty = top - house - car - day_hp - 6.0 * exp_kw
            if empty <= 0:
                max_24 = soc_max
            else:
                max_24 = soc_max - empty
            max_24 = min(soc_max, max(min_24, max_24))
            target_floor[i:h24] = min_24
            # Import only if the next 24 h cannot reach the minimum.
            s = soc
            for k in range(i, h24):
                ahead = k - i + 1
                y = float(pv[k]) * max(0.0, 1.0 - ahead / 66.0)
                load = float(loads[k]["own"])
                if y >= load:
                    s += (y - load) * eta_c
                else:
                    s -= (load - y) / max(eta_d, 0.2)
                s = min(soc_max, s)
            deficit = max(0.0, min_24 - s) / max(eta_c, 0.2)
            imp_slots = [k for k in range(i, h24) if price_band(int(hwt[k])) != "hi"]
            imp_slots.sort(key=lambda k: (0 if price_band(int(hwt[k])) == "lo" else 1, int(hwt[k]) >= 20, k))
            left_imp = deficit
            for k in imp_slots:
                cap_k = float(caps[int(months[k]) - 1])
                room = max(0.0, cap_k - plan_imp[k])
                take = min(left_imp, room)
                plan_imp[k] += take
                left_imp -= take
                if left_imp <= 1e-9:
                    break
            # Maximum SOC is for 15:00 tomorrow. Export before 09:00 so the
            # low-price hours 09:00–15:00 can charge instead of selling.
            h09 = min(n, i + 18)
            y_low = float(pv[h09:h24].sum()) if h24 > h09 else 0.0
            load_low = float(sum(loads[k]["own"] for k in range(h09, h24)))
            room_at_09 = max(min_24, max_24 - max(0.0, y_low - load_low))
            s09 = soc
            for k in range(i, h09):
                y = float(pv[k])
                load = float(loads[k]["own"])
                if y >= load:
                    s09 += (y - load) * eta_c
                else:
                    s09 -= (load - y) / max(eta_d, 0.2)
                s09 = min(soc_max, max(soc_min, s09))
            extra = max(0.0, s09 - room_at_09)
            slots = [k for k in range(i, h09) if exp_px[k] > 0 and float(pv[k]) < exp_kw - 0.05]
            slots.sort(key=lambda k: (-exp_px[k], k))
            left_exp = extra
            for k in slots:
                if left_exp <= 1e-9:
                    break
                room = max(0.0, exp_kw - float(pv[k]))
                take = min(left_exp, room)
                plan_exp[k] = max(plan_exp[k], take)
                left_exp -= take
        elif hwt[i] == 15:
            target_floor[i:min(n, i + 24)] = soc_max
        pv_left = float(pv[i])
        # DC lunch: PV first, rest from the battery. Not through the inverter.
        from_pv_lunch = min(lunch, pv_left * eta_pv_dc)
        pv_left -= from_pv_lunch / eta_pv_dc if eta_pv_dc else 0.0
        lunch_rest = lunch - from_pv_lunch
        from_batt_lunch = min(lunch_rest, max(0.0, soc - soc_min) * eta_batt_dc, c_kw)
        soc -= from_batt_lunch / eta_batt_dc if eta_batt_dc else 0.0
        lunch_emerg = lunch_rest - from_batt_lunch
        c_dis_left = max(0.0, c_kw - from_batt_lunch)

        # PV charges up to the maximum. The 15:00 target limits grid import
        # and battery export, not PV that will not fit on the export line.
        room = soc_max - soc
        want = min(pv_left * eta_c, room)
        charge_c_need = (want / batt) if batt else 0.0
        charge = min(want, c_kw)
        soc += charge
        pv_left -= charge / eta_c if eta_c else 0.0
        charge_c = (charge / batt) if batt else 0.0

        # PV that cannot be sold (price <= 0) and will not fit → pre-heat / pre-cool.
        extra_el = 0.0
        if exp_px[i] <= 0:
            j = i
            while j < n and j < i + 8 and exp_px[j] <= 0:
                j += 1
            room_left = max(0.0, soc_max - soc)
            pv_ahead = float(pv[i:j].sum())
            load_ahead = float(sum(
                loads[k]["house"] + loads[k]["dhw_el"] + loads[k]["night_ac"] + loads[k]["lunch_dc"]
                for k in range(i, j)
            ))
            extra_el = max(0.0, pv_ahead - room_left / max(eta_c, 0.2) - load_ahead)
            extra_el = min(extra_el, pv_left)

        space = space_thermal(t_in, float(tair[i]), demand, cop_map, kw_map, extra_el)
        heat_el = space["heat_el"]
        cool_el = space["cool_el"]
        t_in = space["t_next"]
        ac_load = L["house"] + heat_el + cool_el + L["dhw_el"] + L["night_ac"]

        pv_to_ac = min(pv_left * eta_pv_ac, inv, ac_load)
        pv_left -= pv_to_ac / eta_pv_ac if eta_pv_ac else 0.0
        remain_ac = ac_load - pv_to_ac
        inv_left = max(0.0, inv - pv_to_ac)

        from_batt = min(remain_ac, inv_left, max(0.0, soc - soc_min) * eta_d, c_dis_left * eta_d)
        soc_drop = from_batt / eta_d if eta_d else 0.0
        soc -= soc_drop
        remain_ac -= from_batt
        inv_left -= from_batt

        cap = float(caps[months[i] - 1])
        band_imp = price_band(hwt[i])
        preferred = band_imp != "hi"
        px = float(exp_px[i])
        # Above 0: sell PV and planned battery kWh. At 0: PV yield only.
        # Below 0: do not sell.
        grid_imp = remain_ac
        grid_exp = 0.0
        # Dark forecast: little or no export ahead. Charge at low prices first.
        # Medium only for what the remaining low-price hours cannot take.
        # Stay at the month cap. Import already above the cap is emergency.
        if preferred:
            end = min(n, i + 72)
            exportable = 0.0
            need = 0.0
            low_left = 0
            for k in range(i, end):
                pvk = float(pv[k])
                load = float(loads[k]["own"])
                exportable += max(0.0, pvk - load)
                need += max(0.0, load - pvk)
                if price_band(int(hwt[k])) == "lo":
                    low_left += 1
            want = float(plan_imp[i])
            if exportable < 8.0:
                # Keep a reserve so the next hour is not served from an empty battery.
                target = min(soc_max, soc_min + min(need, 40.0))
                want = max(want, max(0.0, target - soc))
                if band_imp == "mid":
                    want = max(0.0, want - low_left * cap)
            headroom = max(0.0, cap - grid_imp)
            room = max(0.0, soc_max - soc)
            take = min(want, headroom, room / max(eta_batt_ac, 0.2), max(0.0, c_kw - charge))
            if take > 1e-9:
                soc += take * eta_batt_ac
                grid_imp += take
                charge += take * eta_batt_ac
        if px > 0:
            band_now = price_band(hwt[i])
            # Low and medium hours store PV if the battery has room.
            # High hours, and a full battery, export up to the export limit.
            room_store = max(0.0, soc_max - soc)
            if band_now == "hi" or room_store < 0.05:
                take_pv = min(pv_left, exp_kw, inv_left)
            else:
                take_pv = 0.0
            pv_left -= take_pv
            inv_left -= take_pv
            if take_pv == 0.0 and room_store > 0.05:
                store = min(pv_left * eta_c, room_store, max(0.0, c_kw - charge))
                soc += store
                pv_left -= store / eta_c if eta_c else 0.0
                charge += store
                overflow = min(pv_left, exp_kw, inv_left)
                pv_left -= overflow
                inv_left -= overflow
                take_pv += overflow
            floor = min(soc_max, float(target_floor[i]))
            floor = max(floor, soc_min)
            room_exp = max(0.0, soc - floor)
            planned = plan_exp[i]
            take_b = min(
                planned,
                room_exp * eta_d,
                exp_kw - take_pv,
                inv_left,
                c_dis_left * eta_d,
            )
            take_b = max(0.0, take_b)
            if take_b > 0:
                soc -= take_b / eta_d
                inv_left -= take_b
                c_dis_left -= take_b / eta_d if eta_d else 0.0
            grid_exp = take_pv + take_b
            soc_drop += take_b / eta_d if eta_d else 0.0
        elif px == 0:
            take_pv = min(pv_left, exp_kw, inv_left)
            pv_left -= take_pv
            grid_exp = take_pv

        if lunch_emerg > 0:
            grid_imp += lunch_emerg / eta_ac_dc if eta_ac_dc else lunch_emerg

        wasted = pv_left
        discharge = from_batt_lunch + soc_drop
        discharge_c = (discharge / batt) if batt else 0.0

        # Emergency only: this hour's import above the month cap (kW = kWh in the hour).
        emerg = max(0.0, grid_imp - cap - 0.05)
        if emerg > 0:
            month_emerg += 1

        band = price_band(hwt[i])
        recs.append({
            "time": times.iloc[i],
            "year": years[i],
            "month": months[i],
            "doy": doys[i],
            "hour_wt": hwt[i],
            "pv": float(pv[i]),
            "import_kwh": grid_imp,
            "export_kwh": grid_exp,
            "export_high": grid_exp if band == "hi" else 0.0,
            "export_med": grid_exp if band == "mid" else 0.0,
            "export_low": grid_exp if band == "lo" else 0.0,
            "import_eur": grid_imp * float(imp_px[i]) / 100.0,
            "export_eur": grid_exp * float(exp_px[i]) / 100.0,
            "exp_ct": float(exp_px[i]),
            "imp_ct": float(imp_px[i]),
            "demand_kwh": float(demand_el[i]),
            "demand_t": float(demand_t[i]),
            "hp_kwh": heat_el + cool_el + L["dhw_el"],
            "heat_kwh": heat_el,
            "cool_kwh": cool_el,
            "dhw_kwh": L["dhw_el"],
            "direct_kwh": space["backup_el"],
            "own_kwh": L["house"] + heat_el + cool_el + L["dhw_el"] + L["night_ac"] + L["lunch_dc"],
            "wasted_kwh": wasted,
            "discharge_kwh": max(0.0, discharge),
            "charge_c": charge_c,
            "charge_c_need": charge_c_need,
            "discharge_c": discharge_c,
            "emerg_kw": emerg,
            "soc": soc,
            "cap_kw": cap,
            "lunch_emerg": lunch_emerg,
        })

    out = pd.DataFrame(recs)
    if line:
        log('PROGRESS-END')

    log(
        f"  import {out['import_kwh'].sum():.0f} kWh   "
        f"export {out['export_kwh'].sum():.0f} kWh   "
        f"emergency hours {(out['emerg_kw'] > 1e-6).sum()}"
    )
    return {"hourly": out, "demand": demand, "top5_yield": top5_rows}

