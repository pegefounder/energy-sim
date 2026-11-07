#!/usr/bin/env python3
"""Shared helpers for PEGE energy sim. Imported by the server and the menu modules."""

from __future__ import annotations

import json
import re
import math
from matplotlib.backends.backend_pdf import PdfPages
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import requests
from datetime import datetime, timezone, timedelta
from io import StringIO
import pandas as pd
import numpy as np
from pathlib import Path
from urllib.parse import parse_qs


PVGIS_URL = "https://re.jrc.ec.europa.eu/api/v5_3/seriescalc"
REQUEST_TIMEOUT = 120
def parse_form(raw: str, content_type: str) -> dict:
    if "multipart" in content_type or raw.startswith("------"):
        out = {}
        parts = raw.split("------")
        for part in parts:
            if 'name="' not in part:
                continue
            try:
                name = part.split('name="', 1)[1].split('"', 1)[0]
                if "\r\n\r\n" in part:
                    body = part.split("\r\n\r\n", 1)[1]
                else:
                    body = part.split("\n\n", 1)[-1]
                body = body.split("\r\n------", 1)[0].split("\n------", 1)[0].strip()
                if name and not name.startswith("------") and "\r" not in name and "\n" not in name:
                    out[name] = body
            except Exception:
                continue
        return out
    fields = parse_qs(raw, keep_blank_values=True)
    out = {}
    for k, v in fields.items():
        ks = str(k)
        if "\r" in ks or "\n" in ks or ks.startswith("------") or "WebKit" in ks or "Content-Disposition" in ks:
            continue
        out[ks] = v[0] if isinstance(v, list) else str(v)
    return out


def data_root() -> Path:
    """Folder where the server was started."""
    return Path.cwd()


def first(fields: dict, key: str, default: str = "") -> str:
    v = fields.get(key)
    if isinstance(v, list):
        return v[0] if v else default
    return str(v) if v is not None else default


HOST = "127.0.0.1"

LOCAL_VERSION = "0.3"

DEFAULT_PORT = 8765
N_ROWS = 25
LAST_FORM: dict[str, str] = {}
CONFIG = {
    "name": "Austria - Salzburg",
    "lat": "47.8",
    "lon": "13.0",
    "startyear": "2005",
    "endyear": "2023",
    "loss": "14",
    "raddatabase": "PVGIS-SARAH3",
    "usehorizon": True,
}

BASE_URL = f"http://{HOST}:{DEFAULT_PORT}"

def startup_form_values() -> dict[str, str]:
    return {}

def _pdf_lang() -> str:
    lang = LAST_FORM.get("pdf_language") or ""
    if not lang:
        try:
            settings = Path(__file__).with_name("settings.json")
            if settings.is_file():
                lang = json.loads(settings.read_text(encoding="utf-8")).get("pdf_language") or ""
        except Exception:
            pass
    return lang or "english"



def open_browser(url: str):
    """Open the system default browser."""
    import webbrowser
    from datetime import datetime
    print(f"{datetime.now():%Y-%m-%d %H:%M}  opening default browser {url}")
    webbrowser.open(url)



# Yield and demand — shared by house, community, and other objects
def thermal_and_loads(t_air: float, month: int, hour_wt: int, demand: dict, cop_map, kw_map) -> dict:
    ua = float(demand.get("ua_w_per_k") or 0)
    t_heat = float(demand["t_heat_on"])
    t_cool = float(demand["t_cool_on"])
    t_ground = float(demand["t_ground"])
    slope = float(demand["slope"])
    tmin = float(demand["t_flow_min"])
    tmax = float(demand["t_flow_max"])
    mode, q_w = building_load_w(t_air, ua, t_heat, t_cool)
    q_kw = q_w / 1000.0
    hp_el = 0.0
    backup_el = 0.0
    qmax = 0.0
    cop = 3.0
    cooling = mode == "cool"
    hp_src = str(demand.get("hp_source") or "both")
    t_src = source_temp(t_air, t_ground, cooling, hp_src)
    heat_el = 0.0
    cool_el = 0.0
    if mode == "heat":
        tw = flow_temp(t_air, t_heat, slope, tmin, tmax)
        cop, qmax = hp_heating(t_src, tw, cop_map, kw_map)
        if q_kw <= qmax:
            hp_el = q_kw / max(cop, 0.2)
        else:
            hp_el = qmax / max(cop, 0.2)
            backup_el = q_kw - qmax
            q_kw = qmax
        heat_el = hp_el + backup_el
    elif mode == "cool":
        cop, qmax = hp_cooling(t_src, cop_map, kw_map)
        if q_kw <= qmax:
            hp_el = q_kw / max(cop, 0.2)
        else:
            hp_el = qmax / max(cop, 0.2)
            backup_el = q_kw - qmax
            q_kw = qmax
        cool_el = hp_el + backup_el

    dhw = dhw_day(
        float(demand.get("dhw_liters_40") or 0),
        clamp_tank_c(float(demand.get("dhw_tank_c") or 60)),
        pipe_temp_for_month(month, demand.get("dhw_pipe_c") or (8, 10, 12, 10)),
    )
    dhw_from = int(demand.get("dhw_from") or 8)
    dhw_to = int(demand.get("dhw_to") or 18)
    dhw_th = 0.0
    dhw_el = 0.0
    if dhw_from <= hour_wt < dhw_to and dhw["kwh_th"] > 0:
        hours = max(1, dhw_to - dhw_from)
        dhw_th = dhw["kwh_th"] / hours
        t_src_d = source_temp(t_air, t_ground, False, hp_src)
        cop_d, qmax_d = hp_heating(
            t_src_d, float(demand.get("dhw_tank_c") or 60), cop_map, kw_map,
        )
        # DHW is heating. Cooling load does not steal this capacity.
        heat_used = q_kw if mode == "heat" else 0.0
        remain = max(0.0, qmax_d - heat_used)
        if remain >= dhw_th - 1e-9:
            dhw_el = dhw_th / max(cop_d, 0.2)
        elif remain > 0:
            dhw_el = remain / max(cop_d, 0.2) + (dhw_th - remain)
        else:
            dhw_el = dhw_th

    house = float(demand.get("house_kwh_day") or 0) / 24.0
    lunch = float(demand.get("lunch_kwh") or 0) if hour_wt == 12 else 0.0
    night_span = list(range(20, 24)) + list(range(0, 7))
    night = 0.0
    if hour_wt in night_span:
        n = len(night_span)
        raw = float(demand.get("night_kwh") or 0) / n
        night = min(raw, float(demand.get("ac_charger_kw") or raw))

    return {
        "house": house,
        "hp_el": hp_el,
        "heat_el": heat_el,
        "cool_el": cool_el,
        "backup_el": backup_el,
        "dhw_el": dhw_el,
        "night_ac": night,
        "lunch_dc": lunch,
        "own": house + hp_el + backup_el + dhw_el + night + lunch,
        "mode": mode,
    }



def space_thermal(t_in, t_air, demand, cop_map, kw_map, extra_el=0.0):
    """Indoor mass model. extra_el = surplus PV (kWh) to push T toward mid-band."""
    ua = float(demand.get("ua_w_per_k") or 0) / 1000.0
    C = max(0.5, float(demand.get("thermal_mass_kwh_k") or 25.0))
    t_heat = float(demand["t_heat_on"])
    t_cool = float(demand["t_cool_on"])
    t_mid = 0.5 * (t_heat + t_cool)
    t_ground = float(demand["t_ground"])
    slope = float(demand["slope"])
    tmin = float(demand["t_flow_min"])
    tmax = float(demand["t_flow_max"])
    hp_src = str(demand.get("hp_source") or "both")
    q_loss = ua * (t_in - t_air)  # kW out of the room
    heat_el = cool_el = backup = 0.0
    q_th = 0.0  # + into the room
    mode = "off"
    if t_in < t_heat:
        mode = "heat"
        target = t_heat
        need_th = q_loss + C * (target - t_in)
        need_th = max(0.0, need_th)
        t_src = source_temp(t_air, t_ground, False, hp_src)
        tw = flow_temp(t_air, t_heat, slope, tmin, tmax)
        cop, qmax = hp_heating(t_src, tw, cop_map, kw_map)
        use = min(need_th, qmax)
        heat_el = use / max(cop, 0.2)
        if need_th > qmax:
            backup = need_th - qmax
            heat_el += backup
            use = need_th
        q_th = use
    elif t_in > t_cool:
        mode = "cool"
        target = t_cool
        need_th = -q_loss + C * (t_in - target)  # cooling demand
        need_th = max(0.0, need_th)
        t_src = source_temp(t_air, t_ground, True, hp_src)
        cop, qmax = hp_cooling(t_src, cop_map, kw_map)
        use = min(need_th, qmax)
        cool_el = use / max(cop, 0.2)
        if need_th > qmax:
            backup = need_th - qmax
            cool_el += backup
            use = need_th
        q_th = -use
    # Pre-condition toward mid with otherwise wasted PV
    if extra_el > 0.05 and abs(t_in - t_mid) > 0.05:
        if t_in > t_mid:
            t_src = source_temp(t_air, t_ground, True, hp_src)
            cop, qmax = hp_cooling(t_src, cop_map, kw_map)
            already = -q_th if q_th < 0 else 0.0
            room_th = max(0.0, qmax - already)
            want_th = min(C * (t_in - t_mid), extra_el * cop, room_th)
            cool_el += want_th / max(cop, 0.2)
            q_th -= want_th
            extra_el -= want_th / max(cop, 0.2)
        else:
            t_src = source_temp(t_air, t_ground, False, hp_src)
            tw = flow_temp(t_air, t_heat, slope, tmin, tmax)
            cop, qmax = hp_heating(t_src, tw, cop_map, kw_map)
            already = q_th if q_th > 0 else 0.0
            room_th = max(0.0, qmax - already)
            want_th = min(C * (t_mid - t_in), extra_el * cop, room_th)
            heat_el += want_th / max(cop, 0.2)
            q_th += want_th
            extra_el -= want_th / max(cop, 0.2)
    t_next = t_in + (q_th - q_loss) / C
    return {
        "heat_el": heat_el, "cool_el": cool_el, "backup_el": backup,
        "q_th": q_th, "t_next": t_next, "mode": mode,
    }



# Report — PDF, charts, path helpers
PDF_LANG = {
    "english": {},
    "german": {
        "Day of year": "Tag des Jahres",
        "Records 1–10": "Rekorde 1–10",
        "Hours at or above a share of the record": "Stunden bei oder über einem Anteil des Rekords",
        "Records of each photovoltaic field": "Rekorde jedes Photovoltaikfeldes",
        "kWh that day": "kWh an diesem Tag",
        "Scenario — PV": "Szenario — PV",
        "Scenario — house, heat pump, battery": "Szenario — Haus, Wärmepumpe, Batterie",
        "PVGIS combined PV report": "PVGIS kombinierter PV-Bericht",
        "Compare — what differs": "Vergleich — was sich unterscheidet",
        "A and B are named under the table": "A und B stehen unter der Tabelle",
        "Same on both": "Bei beiden gleich",
        "Light green: B is better.  Light red: B is worse.  Difference = B − A.": "Hellgrün: B ist besser. Hellrot: B ist schlechter. Differenz = B − A.",
        "Record hour": "Rekordstunde",
        "W combined.": "W kombiniert.",
        "Clock is winter time UTC+1. Emergency import is an hour above the monthly kW cap.": "Die Uhr ist Winterzeit UTC+1. Notimport ist eine Stunde über dem monatlichen kW-Limit.",
        "No photovoltaic installed.": "Keine Photovoltaik installiert.",
        "Table": "Tabelle",
    },
    "spanish": {
        "Day of year": "Día del año",
        "Records 1–10": "Récords 1–10",
        "Hours at or above a share of the record": "Horas en o por encima de una parte del récord",
        "Records of each photovoltaic field": "Récords de cada campo fotovoltaico",
        "kWh that day": "kWh ese día",
        "Scenario — PV": "Escenario — FV",
        "Scenario — house, heat pump, battery": "Escenario — casa, bomba de calor, batería",
        "PVGIS combined PV report": "Informe PVGIS combinado",
        "Compare — what differs": "Comparación — qué difiere",
        "A and B are named under the table": "A y B se nombran bajo la tabla",
        "Same on both": "Igual en ambos",
        "Light green: B is better.  Light red: B is worse.  Difference = B − A.": "Verde claro: B es mejor. Rojo claro: B es peor. Diferencia = B − A.",
        "Record hour": "Hora récord",
        "W combined.": "W combinados.",
        "Clock is winter time UTC+1. Emergency import is an hour above the monthly kW cap.": "El reloj es hora de invierno UTC+1. La importación de emergencia es una hora por encima del límite mensual de kW.",
        "No photovoltaic installed.": "Sin fotovoltaica instalada.",
        "Table": "Tabla",
    },
    "portugese": {
        "Day of year": "Dia do ano",
        "Records 1–10": "Recordes 1–10",
        "kWh that day": "kWh nesse dia",
        "Scenario — PV": "Cenário — FV",
        "No photovoltaic installed.": "Sem fotovoltaica instalada.",
        "Table": "Tabela",
    },
    "french": {
        "Day of year": "Jour de l'année",
        "Records 1–10": "Records 1–10",
        "kWh that day": "kWh ce jour-là",
        "Scenario — PV": "Scénario — PV",
        "No photovoltaic installed.": "Pas de photovoltaïque installé.",
        "Table": "Tableau",
    },
    "italian": {
        "Day of year": "Giorno dell'anno",
        "Records 1–10": "Record 1–10",
        "kWh that day": "kWh quel giorno",
        "Scenario — PV": "Scenario — FV",
        "No photovoltaic installed.": "Nessun fotovoltaico installato.",
        "Table": "Tabella",
    },
    "polish": {
        "Day of year": "Dzień roku",
        "Records 1–10": "Rekordy 1–10",
        "kWh that day": "kWh tego dnia",
        "No photovoltaic installed.": "Brak zainstalowanej fotowoltaiki.",
        "Table": "Tabela",
    },
    "czech": {
        "Day of year": "Den v roce",
        "Records 1–10": "Rekordy 1–10",
        "kWh that day": "kWh toho dne",
        "No photovoltaic installed.": "Žádná fotovoltaika není nainstalována.",
        "Table": "Tabulka",
    },
    "slovak": {
        "Day of year": "Deň v roku",
        "Records 1–10": "Rekordy 1–10",
        "kWh that day": "kWh toho dňa",
        "No photovoltaic installed.": "Žiadna fotovoltaika nie je nainštalovaná.",
        "Table": "Tabuľka",
    },
    "hungarian": {
        "Day of year": "Az év napja",
        "Records 1–10": "Rekordok 1–10",
        "kWh that day": "kWh azon a napon",
        "No photovoltaic installed.": "Nincs telepítve fotovoltaika.",
        "Table": "Táblázat",
    },
    "romanian": {
        "Day of year": "Ziua anului",
        "Records 1–10": "Recorduri 1–10",
        "kWh that day": "kWh în acea zi",
        "No photovoltaic installed.": "Fără fotovoltaic instalat.",
        "Table": "Tabel",
    },
    "slovenian": {
        "Day of year": "Dan v letu",
        "Records 1–10": "Rekordi 1–10",
        "kWh that day": "kWh tisti dan",
        "No photovoltaic installed.": "Ni nameščene fotovoltaike.",
        "Table": "Tabela",
    },
    "croatian": {
        "Day of year": "Dan u godini",
        "Records 1–10": "Rekordi 1–10",
        "kWh that day": "kWh tog dana",
        "No photovoltaic installed.": "Nema instalirane fotovoltaike.",
        "Table": "Tablica",
    },
}

_CURRENT_LANG = "english"

MONTH_STARTS = [1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335]
MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]

def _t(text, lang=None):
    """Return the translation of text. Uses lang if given, else the current report language."""
    use = lang or _CURRENT_LANG
    if not use or use == "english":
        return text
    return PDF_LANG.get(use, {}).get(text, text)

def _fmt(n, digits=0):
    """Format a number with thousands separator for the current language."""
    if n is None:
        return ""
    try:
        val = float(n)
    except Exception:
        return str(n)
    if digits == 0:
        s = f"{val:,.0f}"
    else:
        s = f"{val:,.{digits}f}"
    if _CURRENT_LANG in {"german", "spanish", "portugese", "french", "italian", "polish", "czech", "slovak", "hungarian", "romanian", "slovenian", "croatian"}:
        s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return s


def sanitize_name(name: str) -> str:
    name = name.strip()
    name = re.sub(r"[\\/:*?\"<>|]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "location"


def fmt_coord(value: float) -> str:
    return f"{value:.3f}"


_page_counter = 0
def _save(pdf, fig, page_no=None):
    """Save the figure and stamp a page number at the right bottom."""
    global _page_counter
    _page_counter += 1
    n = page_no if page_no is not None else _page_counter
    fig.text(0.98, 0.012, str(n), ha="right", va="bottom", fontsize=8, color="#333")
    pdf.savefig(fig, dpi=160)
    plt.close(fig)



def country_for(lat: float, lon: float, cache_dir: Path | None = None) -> str:
    """English country name from coordinates. Cached. Offline fallback: Unknown."""
    import json as _json
    import urllib.request

    key = f"{round(float(lat), 3)},{round(float(lon), 3)}"
    base = Path(cache_dir or Path.cwd())
    loc = base / "locations"
    try:
        loc.mkdir(parents=True, exist_ok=True)
    except Exception:
        loc = base
    cache_path = loc / ".pvgis_country_cache.json"
    legacy = base / ".pvgis_country_cache.json"
    if legacy.is_file() and not cache_path.is_file():
        try:
            legacy.rename(cache_path)
        except Exception:
            cache_path = legacy
    cache: dict = {}
    if cache_path.is_file():
        try:
            cache = _json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    if key in cache and cache[key]:
        return str(cache[key])
    country = "Unknown"
    url = (
        "https://nominatim.openstreetmap.org/reverse"
        f"?lat={lat}&lon={lon}&format=json&zoom=3&accept-language=en"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "pege-energy-sim/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        country = (
            (data.get("address") or {}).get("country")
            or data.get("display_name")
            or "Unknown"
        )
        country = sanitize_name(str(country).split(",")[0])
    except Exception:
        pass
    cache[key] = country
    try:
        cache_path.write_text(_json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return country



def _country_cache_path(cache_dir: Path | None) -> Path:
    base = Path(cache_dir or Path.cwd())
    loc = base / "locations"
    try:
        loc.mkdir(parents=True, exist_ok=True)
    except Exception:
        loc = base
    return loc / ".pvgis_country_cache.json"


def _load_country_cache(cache_dir: Path | None) -> tuple[Path, dict]:
    import json as _json
    path = _country_cache_path(cache_dir)
    cache: dict = {}
    if path.is_file():
        try:
            cache = _json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    if not isinstance(cache, dict):
        cache = {}
    return path, cache


def location_remark(lat: float, lon: float, cache_dir: Path | None = None, text: str | None = None) -> str:
    """Remark for a place, stored in locations/.pvgis_country_cache.json."""
    import json as _json
    path, cache = _load_country_cache(cache_dir)
    remarks = cache.get("_remarks")
    if not isinstance(remarks, dict):
        remarks = {}
    key = f"{fmt_coord(lat)},{fmt_coord(lon)}"
    if text is None:
        return str(remarks.get(key) or "")
    remarks[key] = str(text)[:2000]
    cache["_remarks"] = remarks
    try:
        path.write_text(_json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return remarks[key]


def location_stem(name: str, lat: float, lon: float, country: str | None = None,
                  cache_dir: Path | None = None) -> str:
    title = sanitize_name(name)
    ctry = country or country_for(lat, lon, cache_dir=cache_dir)
    prefix = sanitize_name(ctry) + " - "
    if " - " in title:
        full = title
    else:
        full = prefix + title
    return f"{full} - {fmt_coord(lat)} - {fmt_coord(lon)}"


def locations_root(parent: Path) -> Path:
    """Weather CSV folders live in locations/, next to objects/ and compares/."""
    root = Path(parent) / "locations"
    root.mkdir(parents=True, exist_ok=True)
    return root


def compares_root(parent: Path) -> Path:
    root = Path(parent) / "compares"
    root.mkdir(parents=True, exist_ok=True)
    return root


def location_folder(parent: Path, name: str, lat: float, lon: float) -> Path:
    parent = Path(parent)
    parent.mkdir(parents=True, exist_ok=True)
    new_name = location_stem(name, lat, lon, cache_dir=parent)
    folder = locations_root(parent) / new_name
    if folder.is_dir():
        return folder
    # A folder not yet moved into locations/ is still used, not copied.
    legacy = parent / new_name
    if legacy.is_dir():
        return legacy
    old = parent / f"{sanitize_name(name)} - {fmt_coord(lat)} - {fmt_coord(lon)}"
    if old.is_dir():
        try:
            old.rename(folder)
            return folder
        except Exception:
            return old
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def objects_root(parent: Path) -> Path:
    root = Path(parent) / "objects"
    root.mkdir(parents=True, exist_ok=True)
    return root


def object_folder(parent: Path, object_name: str) -> Path:
    folder = objects_root(parent) / sanitize_name(object_name)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def object_definition_path(folder: Path) -> Path:
    return folder / f"{folder.name}.json"


def list_objects(parent: Path) -> list[str]:
    root = Path(parent) / "objects"
    if not root.is_dir():
        return []
    names = []
    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if child.is_dir() and not child.name.startswith("."):
            names.append(child.name)
    return names


def normalize_pv_list(pv_list: list) -> list[dict]:
    """Accept (slope, az, wp), (name, slope, az, wp) or (name, slope, az, wp, dist_m, height_m)."""
    arrays = []
    arrays_off = []
    for i, item in enumerate(pv_list, 1):
        bifacial = False
        bifaciality = None
        if isinstance(item, dict):
            label = str(item.get("label") or f"PV {i}").strip() or f"PV {i}"
            slope, azimuth, wp = item["slope"], item["azimuth"], item["wp"]
            wall_d, wall_h = item.get("wall_d"), item.get("wall_h")
            bifacial = bool(item.get("bifacial"))
            bifaciality = item.get("bifaciality")
        elif not isinstance(item, (tuple, list)):
            raise ValueError(f"PV {i}: expected a tuple of values")
        elif len(item) == 3:
            slope, azimuth, wp = item
            label, wall_d, wall_h = f"PV {i}", None, None
        elif len(item) == 4:
            label, slope, azimuth, wp = item
            label = str(label).strip() or f"PV {i}"
            wall_d, wall_h = None, None
        elif len(item) == 6:
            label, slope, azimuth, wp, wall_d, wall_h = item
            label = str(label).strip() or f"PV {i}"
            bifacial = False
        elif len(item) == 7:
            label, slope, azimuth, wp, wall_d, wall_h, bifacial = item
            label = str(label).strip() or f"PV {i}"
        elif len(item) == 8:
            label, slope, azimuth, wp, wall_d, wall_h, bifacial, bifaciality = item
        elif len(item) >= 9:
            label, slope, azimuth, wp, wall_d, wall_h, bifacial, bifaciality = item[:8]
            if item[8] is False:
                arrays_off.append({
                    "label": str(label).strip() or f"PV {i}",
                    "slope": float(slope),
                    "azimuth": float(azimuth),
                    "wp": float(wp),
                    "wall_d": None if wall_d in (None, "") else int(wall_d),
                    "wall_h": None if wall_h in (None, "") else int(wall_h),
                    "enabled": False,
                })
                continue
            label = str(label).strip() or f"PV {i}"
        else:
            raise ValueError(f"PV {i}: name,slope,azimuth,Wp[,dist,height[,bifacial[,bifaciality]]]")
        if bifaciality is not None and bifaciality != "":
            bifaciality = float(bifaciality)
            if bifaciality > 1.5:
                bifaciality = bifaciality / 100.0
        arrays.append({
            "label": label,
            "slope": float(slope),
            "azimuth": float(azimuth),
            "wp": float(wp),
            "wall_d": None if wall_d in (None, "") else int(wall_d),
            "wall_h": None if wall_h in (None, "") else int(wall_h),
            "bifacial": bool(bifacial),
            "bifaciality": bifaciality,
            "count_wp": True,
            "face": 1,
        })
    i = 0
    while i < len(arrays):
        if arrays[i].get("bifacial") and i + 1 < len(arrays):
            factor = arrays[i].get("bifaciality")
            if factor is None:
                factor = 0.80
            arrays[i]["bifaciality"] = factor
            arrays[i]["count_wp"] = True
            arrays[i + 1]["count_wp"] = False
            arrays[i + 1]["face"] = 2
            arrays[i + 1]["bifacial"] = False
            arrays[i + 1]["bifaciality"] = factor
            if abs(arrays[i + 1]["wp"] - arrays[i]["wp"]) < 0.51:
                arrays[i + 1]["wp"] = arrays[i]["wp"] * factor
            i += 2
        else:
            arrays[i]["bifacial"] = False
            arrays[i]["count_wp"] = True
            i += 1
    normalize_pv_list.off = arrays_off
    return arrays


def stat_groups(arrays: list[dict]) -> list[dict]:
    """One stats column per module. A bifacial pair is one module (Wp once)."""
    groups = []
    i = 0
    while i < len(arrays):
        if arrays[i].get("bifacial") and i + 1 < len(arrays):
            a, b = arrays[i], arrays[i + 1]
            groups.append({
                "label": a["label"],
                "indices": [i, i + 1],
                "wp": a["wp"],
                "header": (
                    f"{a['label']}\n"
                    f"{a['slope']:g}°/{a['azimuth']:g}° + {b['slope']:g}°/{b['azimuth']:g}°\n"
                    f"{a['wp']:g} Wp bifacial ×{int(round((a.get('bifaciality') or 0.8)*100))}%"
                ),
            })
            i += 2
        else:
            a = arrays[i]
            groups.append({
                "label": a["label"],
                "indices": [i],
                "wp": a["wp"],
                "header": f"{a['label']}\n{a['slope']:g}°/{a['azimuth']:g}°\n{_fmt(a['wp'], 0)} Wp",
            })
            i += 1
    return groups


def obstacle_slope_code(wall_d: int | None, wall_h: int | None) -> str | None:
    """d5h4 and d10h8 are the same horizon: 100 × height/distance → s80."""
    if wall_d is None or wall_h is None:
        return None
    if int(wall_d) <= 0:
        return None
    return f"s{int(round(100.0 * int(wall_h) / int(wall_d)))}"


def orientation_filename(
    slope: float, azimuth: float, start: int, end: int,
    wall_d: int | None = None, wall_h: int | None = None,
) -> str:
    name = f"hourly_1kW_slope{slope:g}_az{azimuth:g}_{start}-{end}"
    code = obstacle_slope_code(wall_d, wall_h)
    if code:
        name += f"_{code}"
    return name + ".csv"


def resolve_hourly_cache(
    folder: Path,
    slope: float, azimuth: float, start: int, end: int,
    wall_d: int | None = None, wall_h: int | None = None,
) -> Path:
    """Prefer _s80; reuse an old _d5h4 file if it is the same slope."""
    dest = folder / orientation_filename(slope, azimuth, start, end, wall_d, wall_h)
    if dest.is_file():
        return dest
    if wall_d is None or wall_h is None:
        return dest
    old = folder / (
        f"hourly_1kW_slope{slope:g}_az{azimuth:g}_{start}-{end}"
        f"_d{int(wall_d)}h{int(wall_h)}.csv"
    )
    if old.is_file():
        try:
            old.rename(dest)
            return dest
        except Exception:
            return old
    code = obstacle_slope_code(wall_d, wall_h)
    if code:
        for p in folder.glob(
            f"hourly_1kW_slope{slope:g}_az{azimuth:g}_{start}-{end}_d*h*.csv"
        ):
            m = re.search(r"_d(\d+)h(\d+)\.csv$", p.name)
            if m and obstacle_slope_code(int(m.group(1)), int(m.group(2))) == code:
                try:
                    p.rename(dest)
                    return dest
                except Exception:
                    return p
    return dest


def pvgis_aspect_to_compass(aspect: float) -> float:
    """PVGIS 0=south, 90=west → compass degrees clockwise from north."""
    return (float(aspect) + 180.0) % 360.0


def wall_horizon_profile(aspect: float, dist_m: int, height_m: int, n: int = 36) -> list[float]:
    """Horizon heights [°], n points from north clockwise.

    Wall stands in the direction the PV faces. Width = 10 × distance.
    """
    if dist_m <= 0:
        raise ValueError("Wall distance must be a positive integer metre")
    if height_m < 0:
        raise ValueError("Wall height must be a non-negative integer metre")
    elev = math.degrees(math.atan2(height_m, dist_m))
    half_az = math.degrees(math.atan2(5.0, 1.0))  # width/2 = 5 × distance
    center = pvgis_aspect_to_compass(aspect)
    step = 360.0 / n
    out = []
    for i in range(n):
        phi = i * step
        delta = abs((phi - center + 180.0) % 360.0 - 180.0)
        out.append(round(elev, 2) if delta <= half_az + 1e-9 else 0.0)
    return out


def fetch_terrain_horizon(lat: float, lon: float, n: int = 36, log=print) -> list[float] | None:
    """DEM horizon resampled to n points starting north, clockwise."""
    url = "https://re.jrc.ec.europa.eu/api/v5_3/printhorizon"
    try:
        r = requests.get(url, params={"lat": lat, "lon": lon, "outputformat": "json"}, timeout=60)
        r.raise_for_status()
        profile = r.json()["outputs"]["horizon_profile"]
    except Exception as exc:
        log(f"  terrain horizon not available: {exc}")
        return None
    # PVGIS A: 0 = south, same as module aspect
    by_compass = []
    for pt in profile:
        compass = (float(pt["A"]) + 180.0) % 360.0
        by_compass.append((compass, float(pt["H_hor"])))
    by_compass.sort()
    cs = [p[0] for p in by_compass]
    hs = [p[1] for p in by_compass]
    # wrap for interpolation
    cs = [cs[-1] - 360.0] + cs + [cs[0] + 360.0]
    hs = [hs[-1]] + hs + [hs[0]]
    out = []
    step = 360.0 / n
    for i in range(n):
        phi = i * step
        j = 0
        while j < len(cs) - 1 and cs[j + 1] < phi:
            j += 1
        t = 0.0 if cs[j + 1] == cs[j] else (phi - cs[j]) / (cs[j + 1] - cs[j])
        out.append(max(0.0, hs[j] + t * (hs[j + 1] - hs[j])))
    return out


def merge_horizons(wall: list[float], terrain: list[float] | None) -> list[float]:
    if not terrain or len(terrain) != len(wall):
        return wall
    return [max(w, t) for w, t in zip(wall, terrain)]


def choose_database(lat: float, lon: float, requested: str) -> list[str]:
    """Return candidate radiation databases in preference order."""
    if requested and requested.lower() != "auto":
        return [requested]
    # SARAH3: 65°W–65°E, 65°N–60°S. NSRDB: Americas. ERA5: global fallback.
    if -65.0 <= lon <= 65.0 and -60.0 <= lat <= 65.0:
        return ["PVGIS-SARAH3", "PVGIS-ERA5"]
    if -180.0 <= lon <= -30.0 and -20.0 <= lat <= 60.0:
        return ["PVGIS-NSRDB", "PVGIS-ERA5"]
    return ["PVGIS-ERA5", "PVGIS-SARAH3", "PVGIS-NSRDB"]


def parse_pvgis_csv(text: str) -> pd.DataFrame:
    lines = text.splitlines()
    header_idx = None
    footer_idx = len(lines)
    for i, line in enumerate(lines):
        if line.startswith("time"):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("PVGIS CSV has no 'time' header line")
    for i in range(header_idx + 1, len(lines)):
        if not lines[i].strip() or lines[i].startswith("P:") or lines[i].startswith("PVGIS"):
            footer_idx = i
            break
    chunk = "\n".join(lines[header_idx:footer_idx])
    df = pd.read_csv(StringIO(chunk))
    if "time" not in df.columns or "P" not in df.columns:
        raise ValueError(f"Unexpected PVGIS columns: {list(df.columns)}")
    df["time"] = pd.to_datetime(df["time"], format="%Y%m%d:%H%M", utc=True)
    df["P"] = pd.to_numeric(df["P"], errors="coerce").fillna(0.0)
    if "T2m" in df.columns:
        df["T2m"] = pd.to_numeric(df["T2m"], errors="coerce")
    df = df.dropna(subset=["time"]).sort_values("time").reset_index(drop=True)
    return df


def download_hourly(
    lat: float,
    lon: float,
    slope: float,
    azimuth: float,
    dest: Path,
    startyear: int,
    endyear: int,
    loss: float,
    databases: Iterable[str],
    usehorizon: bool,
    log=print,
    userhorizon: list[float] | None = None,
) -> tuple[pd.DataFrame, str]:
    if dest.exists() and dest.stat().st_size > 1000:
        log(f"  cache hit: {dest.name}")
        text = dest.read_text(encoding="utf-8", errors="replace")
        db = "cached"
        for line in text.splitlines()[:12]:
            if "Radiation database" in line:
                db = line.split(":")[-1].strip()
                break
        return parse_pvgis_csv(text), db

    last_error = None
    for db in databases:
        params = {
            "lat": lat,
            "lon": lon,
            "raddatabase": db,
            "startyear": startyear,
            "endyear": endyear,
            "pvcalculation": 1,
            "peakpower": 1,
            "loss": loss,
            "pvtechchoice": "crystSi",
            "mountingplace": "free",
            "trackingtype": 0,
            "angle": slope,
            "aspect": azimuth,
            "usehorizon": 1 if usehorizon or userhorizon else 0,
            "outputformat": "csv",
            "browser": 0,
        }
        if userhorizon:
            params["userhorizon"] = ",".join(f"{h:.2f}" for h in userhorizon)
        log(f"  downloading {dest.name}  db={db} …")
        try:
            r = requests.get(PVGIS_URL, params=params, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            last_error = exc
            log(f"    request failed: {exc}")
            continue
        if r.status_code != 200:
            last_error = RuntimeError(f"HTTP {r.status_code}: {r.text[:400]}")
            log(f"    {db} failed ({r.status_code})")
            continue
        if "time" not in r.text[:4000]:
            last_error = RuntimeError(r.text[:400])
            log(f"    {db} returned no time series")
            continue
        dest.write_text(r.text, encoding="utf-8")
        log(f"    saved {dest.name} ({len(r.content):,} bytes)")
        return parse_pvgis_csv(r.text), db
    raise RuntimeError(f"PVGIS download failed for slope={slope} az={azimuth}: {last_error}")


def day_axis(series: pd.Series) -> np.ndarray:
    """Map calendar dates onto a 1–365 axis; 29 Feb sits on day 60."""
    doy = series.dt.dayofyear.to_numpy()
    leap = series.dt.is_leap_year.to_numpy()
    # Shift days after Feb 28 in leap years so months line up with non-leap grid
    doy = np.where(leap & (doy >= 60), doy - 1, doy)
    return doy.astype(float)


def scatter_page(
    pdf: PdfPages,
    x: np.ndarray,
    y: np.ndarray,
    title: str,
    ylabel: str,
    face: str,
    edge: str,
    y_unit_step: float,
    location_title: str,
    y_from_top: tuple[float, float] | None = None,
):
    fig, ax = plt.subplots(figsize=(11.69, 8.27))  # A4 landscape
    ax.scatter(
        x,
        y,
        s=14,
        facecolors=face,
        edgecolors=edge,
        linewidths=0.45,
        alpha=0.78,
        zorder=3,
    )
    ax.set_title(_t(title) + "\n" + location_title, fontsize=13, pad=10)
    ax.set_ylabel(ylabel, fontsize=11)
    ax.set_xlim(1, 366)
    ax.set_xticks(MONTH_STARTS)
    ax.set_xticklabels([_t(m) for m in MONTH_LABELS])
    ax.set_xlabel(_t("Day of year"), fontsize=11)

    finite = y[np.isfinite(y)]
    if y_from_top is not None:
        y_lo, y_hi = y_from_top
        ax.set_ylim(y_lo, y_hi)
        ax.set_yticks(np.arange(y_lo, y_hi + 0.5 * y_unit_step, y_unit_step))
    elif finite.size:
        y_min = math.floor(float(np.nanmin(finite)) / y_unit_step) * y_unit_step
        y_max = math.ceil(float(np.nanmax(finite)) / y_unit_step) * y_unit_step
        if y_max <= y_min:
            y_max = y_min + y_unit_step
        # small padding
        pad = y_unit_step
        ax.set_ylim(y_min - 0.15 * pad, y_max + 0.35 * pad)
        ticks = np.arange(y_min, y_max + 0.5 * y_unit_step, y_unit_step)
        ax.set_yticks(ticks)
    ax.yaxis.set_minor_locator(mticker.MultipleLocator(y_unit_step))
    ax.grid(True, which="major", axis="y", color="#bbbbbb", linewidth=0.6)
    ax.grid(True, which="major", axis="x", color="#888888", linewidth=0.8)
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    _save(pdf, fig)
    plt.close(fig)


def period_stats(frame: pd.DataFrame, columns: list[str], by_month: bool) -> pd.DataFrame:
    """Min / max / mean of period totals across years. columns[j] is array j; last is all-together."""
    n = len(columns) - 1
    if by_month:
        grouped = frame.groupby(["year", "month"])[columns].sum()
        out = pd.DataFrame(
            index=range(1, 13),
            columns=pd.MultiIndex.from_product([list(range(n + 1)), ["min", "max", "mean"]]),
            dtype=float,
        )
        for j, col in enumerate(columns):
            series = grouped[col]
            for m in range(1, 13):
                try:
                    vals = series.xs(m, level="month")
                except KeyError:
                    out.loc[m, (j, "min")] = float("nan")
                    out.loc[m, (j, "max")] = float("nan")
                    out.loc[m, (j, "mean")] = float("nan")
                    continue
                out.loc[m, (j, "min")] = float(vals.min())
                out.loc[m, (j, "max")] = float(vals.max())
                out.loc[m, (j, "mean")] = float(vals.mean())
        return out
    grouped = frame.groupby(["year"])[columns].sum()
    out = pd.DataFrame(
        index=[0],
        columns=pd.MultiIndex.from_product([list(range(n + 1)), ["min", "max", "mean"]]),
        dtype=float,
    )
    for j, col in enumerate(columns):
        vals = grouped[col]
        out.loc[0, (j, "min")] = float(vals.min())
        out.loc[0, (j, "max")] = float(vals.max())
        out.loc[0, (j, "mean")] = float(vals.mean())
    return out


def table_pages(
    pdf: PdfPages,
    monthly: pd.DataFrame,
    yearly: pd.DataFrame,
    pv_labels: list[str],
    location_title: str,
    peak_info: dict,
    title: str,
    monthly_fmt: str = "{:.2f}",
    yearly_fmt: str = "{:.1f}",
    max_cols_per_page: int = 6,
):
    """
    monthly / yearly indexed by period, columns MultiIndex (array, stat)
    stats are min / max / mean of that period's yield across years.
    """
    n_pv = len(pv_labels)
    # pages of PV columns; "All together" is always last column
    groups = []
    i = 0
    while i < n_pv:
        groups.append(list(range(i, min(i + max_cols_per_page, n_pv))))
        i += max_cols_per_page
    if not groups:
        groups = [[]]

    for g_idx, pv_ids in enumerate(groups):
        fig, ax = plt.subplots(figsize=(11.69, 8.27))
        ax.axis("off")
        ax.set_title(_t(title) + "\n" + location_title, fontsize=12, pad=8)

        col_ids = pv_ids + [n_pv]  # last is All together
        headers = [_t("Month / year"), ""] + [
            (pv_labels[j] if j < n_pv else _t("All") + "\n" + _t("together"))
            for j in col_ids
        ]

        rows = []
        # 12 months × 3 stats
        for m in range(1, 13):
            stats = [_t("Maximum"), _t("average"), _t("minimum")]
            keys = ["max", "mean", "min"]
            for s_i, (stat_name, key) in enumerate(zip(stats, keys)):
                label = _t(MONTH_NAMES[m - 1]) if s_i == 0 else ""
                row = [label, stat_name]
                for j in col_ids:
                    val = monthly.loc[m, (j, key)]
                    row.append("—" if val != val else _fmt(val, 2))
                rows.append(row)
        # yearly block
        rows.append(["", "", *([""] * len(col_ids))])
        for stat_name, key in zip([_t("Maximum"), _t("average"), _t("minimum")], ["max", "mean", "min"]):
            label = _t("All years") if stat_name == _t("Maximum") else ""
            row = [label, stat_name]
            for j in col_ids:
                val = yearly.loc[0, (j, key)]
                row.append("—" if val != val else _fmt(val, 1))
            rows.append(row)

        col_labels = headers
        table = ax.table(
            cellText=rows,
            colLabels=col_labels,
            loc="upper center",
            cellLoc="center",
        )
        table.auto_set_font_size(False)
        table.set_fontsize(6.4)
        table.scale(1.0, 1.16)

        for (r, c), cell in table.get_celld().items():
            cell.set_edgecolor("#cccccc")
            if r == 0:
                cell.set_facecolor("#3d5a80")
                cell.set_text_props(color="white", weight="bold", fontsize=6.0)
                cell.set_height(0.09)
            elif 1 <= r <= 36:
                cell.set_facecolor(("#d0d0d0", "#ececec", "#ffffff")[(r - 1) % 3])
            elif r >= 38:
                cell.set_facecolor(("#d0d0d0", "#ececec", "#ffffff")[(r - 38) % 3])
            if c == 0:
                cell.set_text_props(ha="left", fontsize=6.5)
                cell._loc = "left"
            if c == 1:
                cell.set_text_props(ha="left", fontsize=6.2, color="#444444")
                cell._loc = "left"

        peak_txt = (
            _t("Highest combined yield hour") + ": "
            + peak_info['time'].strftime('%Y-%m-%d %H:%M') + " UTC   "
            + _fmt(peak_info['watts'], 1) + " " + _t("W") + "  "
            + "(" + _fmt(peak_info['w_per_wp'], 3) + " " + _t("W/Wp") + ")   "
            + _t("of") + " " + _fmt(peak_info['total_wp'], 0) + " " + _t("Wp installed.") + "  "
            + _t("Use this hour to size battery charge power.")
        )
        fig.text(0.5, 0.035, peak_txt, ha="center", va="bottom", fontsize=8)
        if len(groups) > 1:
            fig.text(0.98, 0.015, _t("Table") + f" {g_idx + 1}/{len(groups)}", ha="right", fontsize=7, color="#666")
        fig.tight_layout(rect=(0.01, 0.06, 0.99, 0.96))
        _save(pdf, fig)
        plt.close(fig)



def record_pages(pdf, combined, arrays, total_wp, location_title):
    """Top 10 combined hours and how often the plant is near that record. Page 1."""
    if not arrays:
        fig, ax = plt.subplots(figsize=(11.69, 8.27))
        ax.axis("off")
        ax.set_title(_t("Records 1–10") + "\n" + location_title, fontsize=13, pad=8, loc="left")
        ax.text(0.5, 0.5, _t("No photovoltaic installed."), ha="center", va="center", fontsize=12)
        fig.tight_layout()
        _save(pdf, fig)
        plt.close(fig)
        return
    cols = [f"P{i}" for i in range(1, len(arrays) + 1)]
    top = combined.nlargest(10, "P_all")
    record = float(top["P_all"].iloc[0]) if len(top) else 0.0
    names = [a.get("label", f"PV {i}") for i, a in enumerate(arrays, 1)]
    # one page of contributions; split names if the table would be too tall
    chunk = 12
    for start in range(0, max(1, len(names)), chunk):
        part = names[start:start + chunk]
        fig, ax = plt.subplots(figsize=(11.69, 8.27))
        ax.axis("off")
        ax.set_title(
            _t("Records 1–10 contribution of each PV") + "\n" + location_title,
            fontsize=13, pad=8, loc="left",
        )
        headers = ["PV"] + [
            f"{pd.Timestamp(t).strftime('%Y-%m-%d')}\n{pd.Timestamp(t).strftime('%H:%M')}"
            for t in top["time"]
        ]
        body = []
        for n_i, name in enumerate(part):
            idx = start + n_i
            row = [name]
            for _, rec in top.iterrows():
                row.append(_fmt(float(rec[cols[idx]]), 0))
            body.append(row)
        if start + chunk >= len(names):
            watts = [_t("Watt at record")] + [_fmt(float(v), 0) for v in top["P_all"]]
            pct = [_t("Percent of installed peak")] + [
                _fmt(100.0 * float(v) / total_wp, 1) if total_wp else "—" for v in top["P_all"]
            ]
            body.append(watts)
            body.append(pct)
        table = ax.table(cellText=body, colLabels=headers, loc="upper center", cellLoc="center")
        table.auto_set_font_size(False)
        table.set_fontsize(6.4)
        table.scale(1.0, 1.35)
        for (r, c), cell in table.get_celld().items():
            cell.set_edgecolor("#ccc")
            if r == 0:
                cell.set_facecolor("#3d5a80")
                cell.set_text_props(color="white", weight="bold", fontsize=6.0)
                cell.set_height(0.09)
            if c == 0:
                cell.set_width(0.20)
                cell.set_text_props(ha="left", fontsize=6.2)
            else:
                cell.set_width(0.078)
            if r > len(part):
                cell.set_facecolor("#eef3f8")
        fig.text(
            0.04, 0.03,
            _t("Values are watts at that hour. Date and time are UTC. ") + 
            f"Record {_fmt(record, 0)} W  ({_fmt(100.0 * record / total_wp, 1)} % of {_fmt(total_wp, 0)} Wp)." if total_wp else f"Record {record:.0f} W. No PV installed."
            if total_wp else "",
            fontsize=8,
        )
        fig.tight_layout(rect=(0.02, 0.06, 0.98, 0.94))
        _save(pdf, fig)
        plt.close(fig)

    fig, ax = plt.subplots(figsize=(11.69, 8.27))
    ax.axis("off")
    ax.set_title(_t("Hours at or above a share of the record") + "\n" + location_title, fontsize=13, pad=8, loc="left")
    levels = [98, 96, 94, 92, 90]
    body = []
    for pct in levels:
        n_h = int((combined["P_all"] >= (pct / 100.0) * record).sum()) if record else 0
        body.append([f"{pct} %", _fmt(n_h, 0), _fmt(pct / 100.0 * record, 0) + " W"])
    table = ax.table(
        cellText=body,
        colLabels=[_t("Of the record"), _t("Hours"), _t("Watts")],
        loc="upper center",
        cellLoc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1.0, 1.6)
    for (r, c), cell in table.get_celld().items():
        cell.set_edgecolor("#ccc")
        if r == 0:
            cell.set_facecolor("#3d5a80")
            cell.set_text_props(color="white", weight="bold")
    fig.text(0.04, 0.04, _t("Record hour") + f" = {_fmt(record, 0)} " + _t("W combined."), fontsize=8)
    fig.tight_layout(rect=(0.15, 0.1, 0.85, 0.94))
    _save(pdf, fig)
    plt.close(fig)


def per_pv_records_page(pdf, combined, arrays, location_title):
    """Records of each photovoltaic field. Used for MPPT and inverter sizing."""
    if not arrays:
        fig, ax = plt.subplots(figsize=(11.69, 8.27))
        ax.axis("off")
        ax.set_title(_t("Records of each photovoltaic field") + "\n" + location_title, fontsize=13, pad=8, loc="left")
        ax.text(0.5, 0.5, _t("No photovoltaic installed."), ha="center", va="center", fontsize=12)
        fig.tight_layout()
        _save(pdf, fig)
        plt.close(fig)
        return
    names = [a.get("label", f"PV {i}") for i, a in enumerate(arrays, 1)]
    cols = [f"P{i}" for i in range(1, len(arrays) + 1)]
    fig, ax = plt.subplots(figsize=(11.69, 8.27))
    ax.axis("off")
    ax.set_title(
        _t("Records of each photovoltaic field") + "\n" + location_title,
        fontsize=13, pad=8, loc="left",
    )
    headers = [
        "PV", "Date", "Watts", "%",
        "h ≥98%", "h ≥96%", "h ≥94%", "h ≥92%", "h ≥90%",
    ]
    body = []
    i = 0
    while i < len(names):
        # merge bifacial face 2 into face 1
        series = combined[cols[i]].copy()
        wp = float(arrays[i].get("wp") or 0)
        name = names[i]
        if arrays[i].get("bifacial") and i + 1 < len(names):
            series = series + combined[cols[i + 1]]
            wp = wp  # Wp counted once
            name = names[i]  # keep the main name
            skip_next = True
        else:
            skip_next = False
        peak = float(series.max()) if len(series) else 0.0
        if peak <= 0:
            body.append([name, "—", "0", "—", "0", "0", "0", "0", "0"])
        else:
            peak_idx = series.idxmax()
            peak_time = combined.loc[peak_idx, "time"]
            date_str = pd.Timestamp(peak_time).strftime("%Y-%m-%d %H:%M")
            pct = 100.0 * peak / wp if wp else 0.0
            counts = []
            for thr in (0.98, 0.96, 0.94, 0.92, 0.90):
                counts.append(str(int((series >= thr * peak).sum())))
            body.append([name, date_str, _fmt(peak, 0), _fmt(pct, 1), *counts])
        i += 2 if skip_next else 1
    table = ax.table(cellText=body, colLabels=headers, loc="upper center", cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(7)
    table.scale(1.0, 1.4)
    for (r, c), cell in table.get_celld().items():
        cell.set_edgecolor("#ccc")
        if r == 0:
            cell.set_facecolor("#3d5a80")
            cell.set_text_props(color="white", weight="bold", fontsize=6.5)
        if c == 0:
            cell.set_width(0.22)
            cell.set_text_props(ha="left", fontsize=7)
        else:
            cell.set_width(0.085)
    fig.text(
        0.04, 0.03,
        _t("Watts is the highest hour of that field. % is of its own Wp. Hours are the count of hours at or above that share of its own record. Use this to size the MPPT or the inverter for that field."),
        fontsize=8,
    )
    fig.tight_layout(rect=(0.02, 0.06, 0.98, 0.94))
    _save(pdf, fig)
    plt.close(fig)


def shoulder_yield_page(pdf, combined, location_title, utc_offset=1):
    """Export hours. High price 06:00–09:00 and 17:00–20:00, low 09:00–15:00, medium the rest. Sell only above 0."""
    ts = combined["time"].dt.tz_convert("UTC")
    h = (ts.dt.hour + utc_offset) % 24
    day = ts.dt.floor("D")
    morning = combined["P_all"].where(h < 9, 0.0).groupby(day).sum() / 1000.0
    evening = combined["P_all"].where(h >= 15, 0.0).groupby(day).sum() / 1000.0
    doy = day.dt.dayofyear.groupby(day).first()
    fig, ax = plt.subplots(figsize=(11.69, 8.27))
    ax.scatter(doy, morning, s=8, c="#2E8B57", alpha=0.45, label="00:00–09:00", zorder=3)
    ax.scatter(doy, evening, s=8, c="#E0B000", alpha=0.45, label="15:00–24:00", zorder=2)
    ax.set_title(
        f"Yield outside 09:00–15:00 local (UTC{utc_offset:+d})\n{location_title}",
        fontsize=13, pad=10,
    )
    ax.set_ylabel(_t("kWh that day"), fontsize=11)
    ax.legend(frameon=False, loc="upper right")
    ax.set_xlim(1, 366)
    ax.set_xticks([1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335])
    ax.set_xticklabels([_t(m) for m in ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]])
    ax.set_xlabel(_t("Day of year"), fontsize=11)
    ax.grid(True, axis="y", color="#bbbbbb", linewidth=0.6)
    ax.grid(True, axis="x", color="#888888", linewidth=0.8)
    fig.text(
        0.06, 0.03,
        "Green: 00:00–09:00 winter time.  Yellow: 15:00–24:00 winter time.  "
        "These are the hours when feed-in is allowed.  09:00–15:00 is not on this chart.",
        fontsize=8,
    )
    fig.tight_layout(rect=(0.04, 0.08, 0.98, 0.96))
    _save(pdf, fig)
    plt.close(fig)


def _pv_rows(arrays):
    rows = []
    counted = 0.0
    for i, a in enumerate(arrays, 1):
        wall = ""
        if a.get("wall_d") is not None and a.get("wall_h") is not None:
            wall = f"{a['wall_d']:g} / {a['wall_h']:g}"
        face = "front"
        count = "yes"
        if a.get("bifacial"):
            face = "face 1"
        elif a.get("face") == 2:
            face = "face 2"
            count = "no"
        else:
            counted += a["wp"]
        if a.get("count_wp", True) and a.get("face") != 2:
            pass
        bi = ""
        if a.get("face") == 2:
            bi = f"{(a.get('bifaciality') or 0.8) * 100:g} %"
        if a.get("count_wp", True):
            counted_flag = a["wp"]
        else:
            counted_flag = 0
        rows.append([
            a.get("label", f"PV {i}"),
            f"{a['slope']:g}",
            f"{a['azimuth']:g}",
            _fmt(a["wp"], 0),
            wall or "—",
            _t(face),
            bi or "—",
            _t("yes") if a.get("count_wp", True) else _t("no"),
        ])
    return rows


def scenario_pages(pdf, location_title, object_name, arrays, demand, db, startyear, endyear, loss, peak_info, gap_dates=None, reopen_url=None, remark=""):
    """Scenario between the yield tables and the demand charts."""
    fig, ax = plt.subplots(figsize=(11.69, 8.27))
    ax.axis("off")
    who = object_name or "PV only"
    ax.set_title(_t("Scenario — PV") + "\n" + who + "    " + location_title, fontsize=13, pad=8, loc="left")
    headers = [_t("Name"), _t("Slope °"), _t("Azimuth °"), "Wp", _t("Wall d/h m"), _t("Face"), _t("Bifacial"), _t("Wp counted")]
    body = _pv_rows(arrays)
    total = sum(a["wp"] for a in arrays if a.get("count_wp", True))
    body.append([_t("Total counted"), "", "", _fmt(total, 0), "", "", "", ""])
    off = list(getattr(normalize_pv_list, "off", []) or [])
    if off:
        body.append(["Deselected", "", "", "", "", "", "", ""])
        for a in off:
            wall = "—"
            if a.get("wall_d") not in (None, "") and a.get("wall_h") not in (None, ""):
                wall = f"{a['wall_d']} / {a['wall_h']}"
            body.append([a.get("label", ""), f"{a.get('slope', '')}", f"{a.get('azimuth', '')}", f"{a.get('wp', '')}", wall, "", "", "no"])
    table = ax.table(cellText=body, colLabels=headers, loc="upper center", cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(7.5)
    table.scale(1.0, 1.25)
    for (r, c), cell in table.get_celld().items():
        cell.set_edgecolor("#ccc")
        if r == 0:
            cell.set_facecolor("#3d5a80")
            cell.set_text_props(color="white", weight="bold", fontsize=7.5)
        elif r == len(body):
            cell.set_facecolor("#eef3f8")
        if c == 0:
            cell.set_text_props(ha="left", fontsize=7.2)
            cell.set_width(0.22)
        else:
            cell.set_width(0.76 / 7)
    gap_dates = list(gap_dates or [])
    gap_line = (
        _t("Database gaps: {n} days with 0 yield while the sun reached the horizon at noon.").format(n=len(gap_dates))
        + (f"  {', '.join(str(d) for d in gap_dates)}." if gap_dates else "")
        + "  " + _t("Yield charts skip them. The simulation uses the lowest-yield day of that month.")
    )
    import textwrap
    note = (
        _t("Database") + f" {db}    {startyear}–{endyear}    " + _t("loss") + f" {loss:g} %    "
        + _t("azimuth 0 = south, 90 = west, −90 = east.") + "\n"
        + _t("Price bands and charts use UTC") + f"{round(float((demand or {}).get('lon') or 15)/15):+d} " + _t("from longitude (15° per hour), not the political time zone.") + "\n"
        + _t("Peak hour") + f" {peak_info['time'].strftime('%Y-%m-%d')} "
        + (peak_info['time'] + __import__('datetime').timedelta(hours=round(float((demand or {}).get('lon') or 15)/15))).strftime('%H:%M') + " " + _t("local") + " "
        + f"({peak_info['time'].strftime('%H:%M')} UTC)    "
        + _fmt(peak_info['watts'], 0) + " " + _t("W") + "    " + _fmt(peak_info['w_per_wp'], 3) + " " + _t("W/Wp of") + " " + _fmt(total, 0) + " " + _t("Wp") + ".\n"
        + "\n".join(textwrap.wrap(gap_line, 110)) + "\n"
        + "\n".join(textwrap.wrap(
            _t("Sell when the export price is above 0. At 0 cent export PV yield only, not the battery. Do not sell when the earning is negative. At 15:00 import/export scheduling."), 110))
    )
    if remark:
        wrapped = "\n".join(textwrap.wrap(f"{_t('Location:')} {remark}", 110))
        note = wrapped + "\n" + note
    fig.text(0.04, 0.08, note, fontsize=8, va="bottom")
    if reopen_url:
        link = fig.text(
            0.04, 0.045,
            "Click here to work on this simulation. If the click does nothing, copy the address below and paste it in the browser. PEGE energy sim server has to run.",
            fontsize=9, color="#003399", va="bottom",
        )
        link.set_url(reopen_url)
        fig.text(0.04, 0.02, reopen_url, fontsize=6, va="bottom", color="#445")
    # Logo on the right, above the lower text, linked to the project page.
    try:
        from matplotlib.offsetbox import OffsetImage, AnnotationBbox
        import urllib.request
        logo_path = Path(__file__).with_name("pege-logo.png")
        if not logo_path.is_file():
            urllib.request.urlretrieve("https://pege.org/pic/pege-trans.png", logo_path)
        if logo_path.is_file():
            img = plt.imread(str(logo_path))
            # Place at the right edge, vertically in the lower third.
            ax_img = fig.add_axes([0.72, 0.077, 0.25, 0.12])
            ax_img.imshow(img)
            ax_img.axis("off")
            # A text box the size of the logo carries the link.
            logo_link = fig.text(
                0.845, 0.137, "PEGE energy sim  ",
                fontsize=26, color="none", ha="center", va="center",
                url="https://www.pege.org/energy-sim/",
            )
            logo_link.set_url("https://www.pege.org/energy-sim/")
    except Exception:
        pass
    fig.tight_layout(rect=(0.03, 0.16, 0.98, 0.94))
    _save(pdf, fig)
    plt.close(fig)

    if not demand:
        return
    d = demand
    fig, ax = plt.subplots(figsize=(11.69, 8.27))
    ax.axis("off")
    ax.set_title(_t("Scenario — house, heat pump, battery") + "\n" + who + "    " + location_title, fontsize=13, pad=8, loc="left")
    caps = list(d.get("import_cap_kw") or [])
    pipes = list(d.get("dhw_pipe_c") or (8, 10, 12, 10))
    rows = [
        [_t("Building"), _t("Design out / in / heat"), f"{_fmt(d.get('t_out_design'), 1)} °C / {_fmt(d.get('t_in_design'), 1)} °C / {_fmt(d.get('q_design_w'), 0)} W"],
        ["", _t("Heat on / cool on / ground"), f"{_fmt(d.get('t_heat_on'), 1)} °C / {_fmt(d.get('t_cool_on'), 1)} °C / {_fmt(d.get('t_ground'), 1)} °C"],
        ["", _t("Curve / flow min / max"), f"{_fmt(d.get('slope'), 1)} / {_fmt(d.get('t_flow_min'), 1)} °C / {_fmt(d.get('t_flow_max'), 1)} °C"],
        ["", _t("Thermal mass of the house"), f"{_fmt(d.get('thermal_mass_kwh_k', 25), 1)} kWh/K"],
        ["", _t("Household only"), f"{_fmt(d.get('house_kwh_day'), 1)} kWh/day"],
        [_t("DHW"), _t("Litres at 40 °C / tank"), f"{_fmt(d.get('dhw_liters_40'), 0)} L    tank {_fmt(d.get('dhw_tank_c'), 1)} °C    {d.get('dhw_from', 8)}–{d.get('dhw_to', 18)} h"],
        ["", _t("Water out of the pipe Q1–Q4"), "  ".join(f"{_fmt(x, 0)} °C" for x in pipes)],
        [_t("Heat pump"), _t("Source"), _t("air and ground") if str(d.get("hp_source", "both")) == "both" else str(d.get("hp_source", "air and ground"))],
        ["", _t("COP −10/0/10 @ 35 °C"), _cop(d, 35)],
        ["", _t("COP −10/0/10 @ 50 °C"), _cop(d, 50)],
        ["", _t("kW −10/0/10 @ 35 °C"), _kw(d, 35)],
        ["", _t("kW −10/0/10 @ 50 °C"), _kw(d, 50)],
        [_t("Battery"), _t("kWh / C-rate / SOC min–max–start"), f"{_fmt(d.get('battery_kwh'), 1)} kWh    C {_fmt(d.get('c_rate', 0.5), 1)}    {_fmt(100*float(d.get('soc_min', 0.1)), 0)} / {_fmt(100*float(d.get('soc_max', 0.95)), 0)} / {_fmt(100*float(d.get('soc_start', 0.5)), 0)} %"],
        ["", _t("Nameplate"), str(d.get("nameplate") or "—")],
        ["", _t("Charge / discharge η"), f"{100*float(d.get('eta_charge', 0.95)):.0f} % / {100*float(d.get('eta_discharge', 0.9)):.0f} %"],
        [_t("Grid"), _t("Inverter / grid connection"), f"{_fmt(d.get('inverter_kw'), 1)} kW / {_fmt(d.get('grid_kw', d.get('export_kw', '')), 1)} kW"],
        ["", _t("Max export"), f"{_fmt(d.get('export_kw'), 1)} kW"],
        ["", _t("Import cap Jan–Jun kW"), "  ".join(f"{_t(m)} {_fmt(caps[i], 0)}" for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun"]))],
        ["", _t("Import cap Jul–Dec kW"), "  ".join(f"{_t(m)} {_fmt(caps[i+6], 0)}" for i, m in enumerate(["Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]))],
        [_t("Cars"), _t("Lunch DC / night AC"), f"{_fmt(d.get('lunch_kwh'), 1)} kWh @ {_fmt(d.get('dc_charger_kw'), 1)} kW    {_t('night')} {_fmt(d.get('night_kwh'), 1)} kWh @ {_fmt(d.get('ac_charger_kw'), 1)} kW"],
    ]
    table = ax.table(cellText=rows, colLabels=[_t("Chapter"), _t("Parameter"), _t("Value")], loc="upper center", cellLoc="left")
    table.auto_set_font_size(False)
    table.set_fontsize(7.4)
    table.scale(1.0, 1.28)
    for (r, c), cell in table.get_celld().items():
        cell.set_edgecolor("#ccc")
        if r == 0:
            cell.set_facecolor("#3d5a80")
            cell.set_text_props(color="white", weight="bold", fontsize=8)
        elif rows[r - 1][0]:
            cell.set_facecolor("#eef3f8")
    fig.text(0.04, 0.03, _t("Clock is winter time UTC+1. Emergency import is an hour above the monthly kW cap."), fontsize=8)
    fig.tight_layout(rect=(0.03, 0.06, 0.98, 0.94))
    _save(pdf, fig)
    plt.close(fig)


def _cop(d, flow):
    m = d.get("hp_cop") or {}
    return "   ".join(_fmt(_mv(m, s, flow), 1) for s in (-10, 0, 10))


def _kw(d, flow):
    m = d.get("hp_kw") or {}
    return "   ".join(_fmt(_mv(m, s, flow), 1) for s in (-10, 0, 10))


def _mv(m, src, flow):
    if (src, flow) in m:
        return float(m[(src, flow)])
    return float(m.get(f"{src}/{flow}", 0))


def _caps(caps):
    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    if not caps:
        return "—"
    return "  ".join(f"{n} {float(c):g}" for n, c in zip(names, caps))


def summary_page(
    pdf: PdfPages,
    location_title: str,
    lat: float,
    lon: float,
    arrays: list[dict],
    db: str,
    startyear: int,
    endyear: int,
    loss: float,
    peak_info: dict,
    daily: pd.DataFrame,
    reopen_url: str | None = None,
):
    fig, ax = plt.subplots(figsize=(11.69, 8.27))
    ax.axis("off")
    ax.set_title(_t("PVGIS combined PV report"), fontsize=16, pad=12, loc="left")

    lines = [
        f"Location: {location_title}",
        f"Coordinates: {fmt_coord(lat)} °N,  {fmt_coord(lon)} °E",
        f"Radiation database: {db}",
        f"Period: {startyear} – {endyear}   |   System loss used in PVGIS: {loss:g} %",
        f"Horizon shading: on   |   Technology: crystalline silicon   |   Mounting: fixed, free-standing",
        "",
        "Arrays (downloaded at 1 kWp, then scaled to installed Wp):",
    ]
    total_wp = sum(a["wp"] for a in arrays if a.get("count_wp", True))
    for i, a in enumerate(arrays, 1):
        extra = ""
        if a.get("wall_d") is not None and a.get("wall_h") is not None:
            extra = f"   wall {a['wall_d']} m away, {a['wall_h']} m high (width 10×distance)"
        role = ""
        if a.get("bifacial"):
            pct = (a.get("bifaciality") or 0.8) * 100
            role = f"   bifacial face 1 (nameplate counted once, rear {pct:g} %)"
        elif a.get("face") == 2:
            pct = (a.get("bifaciality") or 0.8) * 100
            role = f"   bifacial face 2 / junction-box side ({pct:g} % of front Wp, not added to nameplate)"
        lines.append(
            f"    {a.get('label', f'PV {i}')}:  slope {a['slope']:g}°   azimuth {a['azimuth']:g}° "
            f"(0=south, 90=west, −90=east)   {a['wp']:g} Wp{extra}{role}"
        )
    lines.append(f"    Total installed: {total_wp:g} Wp  (bifacial pair = one Wp)")
    lines.append("")
    lines.append("Peak combined hour (all arrays added each hour):")
    lines.append(
        f"    {peak_info['time'].strftime('%Y-%m-%d %H:%M')} UTC    "
        f"{peak_info['watts']:.2f} W    {peak_info['w_per_wp']:.4f} W/Wp"
    )
    for i, w in enumerate(peak_info["per_array_w"], 1):
        label = arrays[i - 1].get("label", f"PV {i}")
        lines.append(f"        {label} at that hour: {w:.2f} W")
    lines.append("")
    yld = daily["wh_per_wp"]
    lines.append("Daily specific yield of all arrays together (Wh/Wp = kWh/kWp):")
    lines.append(
        f"    min {yld.min():.2f}    mean {yld.mean():.2f}    "
        f"max {yld.max():.2f}    days {len(yld)}"
    )
    tmin, tmax = daily["t_min"].min(), daily["t_max"].max()
    lines.append(
        f"Temperature range over the period:  {tmin:.1f} °C  …  {tmax:.1f} °C"
    )
    lines.append("")
    lines.append("Charts: each dot is one calendar day. Years  overlay the same Jan–Dec axis.")
    lines.append("Yellow = daily yield of the combined plant per installed watt-peak.")
    lines.append("Source: European Commission JRC PVGIS 5.3  https://re.jrc.ec.europa.eu/pvg_tools/en/")

    ax.text(0.02, 0.96, "\n".join(lines), va="top", ha="left", fontsize=10,
            family="DejaVu Sans", transform=ax.transAxes, linespacing=1.35)
    if reopen_url:
        fig.text(
            0.02, 0.035,
            "Click to reopen these inputs in the form  (server must be running)",
            fontsize=9, color="#003399", url=reopen_url, ha="left",
        )
        fig.text(0.02, 0.015, reopen_url, fontsize=6.5, color="#445566", url=reopen_url, ha="left")
    fig.tight_layout(rect=(0.01, 0.06, 0.99, 0.98))
    _save(pdf, fig)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
def build_report(
    name: str,
    lat: float,
    lon: float,
    pv_list: list,
    startyear: int,
    endyear: int,
    loss: float,
    raddatabase: str,
    usehorizon: bool,
    outdir: Path,
    log=print,
    reopen_url: str | None = None,
    form_values: dict | None = None,
    result_folder: Path | None = None,
    object_name: str | None = None,
    demand: dict | None = None,
    lang: str = "english",
) -> Path:
    set_language(lang or "english")
    global _CURRENT_LANG
    _CURRENT_LANG = lang or "english"
    log(f"PDF-LANG-MARKER {_CURRENT_LANG} {_fmt(1234567.89, 2)}")
    log(f"PDF-LANG-MARKER {_CURRENT_LANG} {_fmt(1234567.89, 2)}")
    if not pv_list:
        log("No PV. Demand and grid only.")
        pv_list = []

    folder = location_folder(outdir, name, lat, lon)
    log(f"Location folder: {folder}")
    databases = choose_database(lat, lon, raddatabase)
    log(f"Database candidates: {databases}")

    arrays = []
    used_db = databases[0]
    frames = []
    terrain_cache: list[float] | None | str = "unset"
    for idx, spec in enumerate(normalize_pv_list(pv_list), 1):
        slope, azimuth, wp = spec["slope"], spec["azimuth"], spec["wp"]
        wall_d, wall_h = spec.get("wall_d"), spec.get("wall_h")
        wall_txt = f"  wall d={wall_d} m h={wall_h} m" if wall_d is not None and wall_h is not None else ""
        log(f"{spec['label']}: slope={slope:g}°  az={azimuth:g}°  {wp:g} Wp{wall_txt}")
        dest = resolve_hourly_cache(
            folder, slope, azimuth, startyear, endyear, wall_d, wall_h,
        )
        scode = obstacle_slope_code(wall_d, wall_h)
        if scode:
            wall_txt = f"{wall_txt}  file _{scode}"
        userhorizon = None
        use_h = usehorizon
        if wall_d is not None and wall_h is not None:
            wall = wall_horizon_profile(azimuth, wall_d, wall_h)
            if usehorizon:
                if terrain_cache == "unset":
                    terrain_cache = fetch_terrain_horizon(lat, lon, n=len(wall), log=log)
                userhorizon = merge_horizons(wall, terrain_cache if isinstance(terrain_cache, list) else None)
            else:
                userhorizon = wall
            use_h = True
            log(f"  userhorizon wall+{'terrain' if usehorizon else 'only'}  max={max(userhorizon):.1f}°")
        df, db = download_hourly(
            lat, lon, slope, azimuth, dest, startyear, endyear, loss, databases, use_h,
            log=log, userhorizon=userhorizon,
        )
        used_db = db if db != "cached" else used_db
        part = df[["time", "P"]].rename(columns={"P": f"P{idx}"})
        part[f"P{idx}"] = part[f"P{idx}"] * (wp / 1000.0)  # scale 1 kWp → actual Wp
        frames.append(part)
        arrays.append({**spec, "df": df})

    if frames:
        combined = frames[0]
        for part in frames[1:]:
            combined = combined.merge(part, on="time", how="outer")
        p_cols = [f"P{i}" for i in range(1, len(arrays) + 1)]
        combined[p_cols] = combined[p_cols].fillna(0.0)
        combined["P_all"] = combined[p_cols].sum(axis=1)
        tdf = arrays[0]["df"][["time", "T2m"]].copy()
        combined = combined.merge(tdf, on="time", how="left")
    else:
        weather = None
        for cached in sorted(folder.glob("*.csv")):
            try:
                got = pd.read_csv(cached, parse_dates=["time"])
            except Exception:
                continue
            if "T2m" in got.columns and "time" in got.columns and len(got):
                weather = got[["time", "T2m"]].copy()
                log(f"No PV. Temperature from cached {cached.name}.")
                break
        if weather is None:
            log("No PV and no cached weather. Downloading a flat 1 kWp array for temperature.")
            dest = folder / "flat_0_0_weather.csv"
            got, db = download_hourly(
                lat, lon, 0, 0, dest, startyear, endyear, loss, databases, usehorizon, log=log,
            )
            weather = got[["time", "T2m"]].copy()
            log(f"Temperature from flat array, database {db}.")
        weather["time"] = pd.to_datetime(weather["time"], utc=True)
        combined = weather.copy()
        combined["P_all"] = 0.0
    combined["date"] = combined["time"].dt.tz_convert("UTC").dt.date
    combined["year"] = combined["time"].dt.year
    combined["month"] = combined["time"].dt.month
    # Exactly 0 W while the sun reaches the horizon at noon is a missing PVGIS day.
    # Polar night (sun below the horizon at noon) is real and is kept.
    hwt_gap = (combined["time"].dt.tz_convert("UTC").dt.hour + 1) % 24
    daylight = combined["P_all"].where((hwt_gap >= 8) & (hwt_gap < 16), 0.0)
    day_pv = daylight.groupby(combined["date"]).sum()

    def _noon_elev(day) -> float:
        doy = int(day.timetuple().tm_yday)
        dec = math.radians(23.44 * math.sin(math.radians(360.0 * (doy - 81) / 365.0)))
        return math.degrees(math.asin(
            math.sin(math.radians(lat)) * math.sin(dec)
            + math.cos(math.radians(lat)) * math.cos(dec)
        ))

    gap_dates = [] if not frames else sorted(d for d, wh in day_pv.items() if wh <= 0 and _noon_elev(d) >= 0.0)
    sim_combined = combined.copy()
    p_cols_gap = [c for c in combined.columns if c == "P_all" or (c.startswith("P") and c[1:].isdigit())]
    if gap_dates:
        log(
            f"Database gaps: {len(gap_dates)} days with 0 W while solar noon "
            f"reached the horizon. {', '.join(str(d) for d in gap_dates)}"
        )
        gap_set = set(gap_dates)
        for gap in gap_dates:
            donors = [
                d for d, wh in day_pv.items()
                if d.month == gap.month and d not in gap_set and wh > 0
            ]
            if not donors:
                log(f"  {gap}: no donor day in this month, left as 0")
                continue
            donor = min(donors, key=lambda d: float(day_pv[d]))
            src = combined[combined["date"] == donor]
            by_hour = {}
            for _, row in src.iterrows():
                by_hour[int(row["time"].hour)] = row
            idx = sim_combined.index[sim_combined["date"] == gap]
            for i in idx:
                hour = int(sim_combined.at[i, "time"].hour)
                src_row = by_hour.get(hour)
                if src_row is None:
                    continue
                for col in p_cols_gap + ["T2m"]:
                    if col in sim_combined.columns:
                        sim_combined.at[i, col] = src_row[col]
            log(
                f"  simulation {gap} uses {donor} "
                f"({float(day_pv[donor]) / 1000.0:.2f} kWh, lowest {gap.strftime('%B')})"
            )
        combined = combined[~combined["date"].isin(gap_dates)].reset_index(drop=True)
        sim_combined = sim_combined[~sim_combined["date"].isin([])].reset_index(drop=True)
    else:
        log("Database gaps: 0 days with the sun at the horizon and 0 W")

    total_wp = float(sum(a["wp"] for a in arrays if a.get("count_wp", True)))

    daily = combined.groupby("date", sort=True).agg(
        P_all=("P_all", "sum"),          # Wh for that day (W × 1 h)
        t_max=("T2m", "max"),
        t_min=("T2m", "min"),
        t_mean=("T2m", "mean"),
        year=("year", "first"),
        month=("month", "first"),
    ).reset_index()
    daily["date"] = pd.to_datetime(daily["date"])
    daily["wh_per_wp"] = daily["P_all"] / total_wp if total_wp else 0.0
    ts = combined["time"].dt.tz_convert("UTC")
    utc_offset = int(round(float((demand or {}).get("lon") or 15) / 15))
    hwt = (ts.dt.hour + utc_offset) % 24
    morning_wh = combined["P_all"].where(hwt < 9, 0.0).groupby(combined["date"]).sum()
    evening_wh = combined["P_all"].where(hwt >= 15, 0.0).groupby(combined["date"]).sum()
    daily["wh_per_wp_0009"] = morning_wh.reindex(daily["date"].dt.date).to_numpy() / total_wp if total_wp else 0.0
    daily["wh_per_wp_1524"] = evening_wh.reindex(daily["date"].dt.date).to_numpy() / total_wp if total_wp else 0.0
    daily["t_range"] = daily["t_max"] - daily["t_min"]
    daily["doy"] = day_axis(daily["date"])

    # Per-module daily Wh/Wp (bifacial pair = one module, Wp once)
    groups = stat_groups(arrays)
    dates = daily["date"].dt.date
    for g_i, g in enumerate(groups, 1):
        energy = None
        for idx in g["indices"]:
            part = combined.groupby("date")[f"P{idx + 1}"].sum()
            energy = part if energy is None else energy.add(part, fill_value=0.0)
        daily[f"wh_per_wp_{g_i}"] = (energy / g["wp"]).reindex(dates).to_numpy()
        daily[f"kwh_{g_i}"] = (energy / 1000.0).reindex(dates).to_numpy()

    # Peak hour
    peak_row = combined.loc[combined["P_all"].idxmax()]
    peak_info = {
        "time": peak_row["time"].to_pydatetime().replace(tzinfo=None) if hasattr(peak_row["time"], "to_pydatetime")
        else pd.Timestamp(peak_row["time"]).to_pydatetime().replace(tzinfo=None),
        "time_local_note": "UTC",
        "watts": float(peak_row["P_all"]),
        "w_per_wp": float(peak_row["P_all"]) / total_wp if total_wp else 0.0,
        "total_wp": total_wp,
        "per_array_w": [float(peak_row[f"P{i}"]) for i in range(1, len(arrays) + 1)],
    }
    log(
        f"Peak hour: {peak_info['time']} UTC  "
        f"{peak_info['watts']:.1f} W  ({peak_info['w_per_wp']:.4f} W/Wp)"
    )

    daily["kwh_all"] = daily["P_all"] / 1000.0

    spec_cols = [f"wh_per_wp_{i}" for i in range(1, len(groups) + 1)] + ["wh_per_wp"]
    kwh_cols = [f"kwh_{i}" for i in range(1, len(groups) + 1)] + ["kwh_all"]
    monthly = period_stats(daily, spec_cols, by_month=True)
    yearly = period_stats(daily, spec_cols, by_month=False)
    monthly_kwh = period_stats(daily, kwh_cols, by_month=True)
    yearly_kwh = period_stats(daily, kwh_cols, by_month=False)

    pv_labels = [g["header"] for g in groups]

    location_title = f"{sanitize_name(name)}   {fmt_coord(lat)}, {fmt_coord(lon)}"
    stamp = datetime.now().strftime("%Y-%m-%d %H%M")
    loc = location_stem(name, lat, lon, cache_dir=Path(outdir))
    pdf_name = f"{stamp} - {loc}.pdf"
    simname = ""
    if form_values:
        simname = str(form_values.get("simname") or "").strip()
    if simname:
        pdf_name = f"{stamp} - {sanitize_name(simname)} - {loc}.pdf"
    if result_folder is not None:
        result_folder = Path(result_folder)
        result_folder.mkdir(parents=True, exist_ok=True)
        pdf_path = result_folder / pdf_name
    else:
        pdf_path = folder / pdf_name
    log(f"Writing PDF: {pdf_path}")

    with PdfPages(pdf_path) as pdf:
        global _page_counter; _page_counter = 0
        remark = ""
        try:
            remark = location_remark(lat, lon, cache_dir=outdir)
        except Exception:
            remark = ""
        scenario_pages(
            pdf, location_title, object_name, arrays, demand, used_db,
            startyear, endyear, loss, peak_info, gap_dates, reopen_url, remark,
        )
        record_pages(pdf, combined, arrays, total_wp, location_title)
        per_pv_records_page(pdf, combined, arrays, location_title)
        scatter_page(
            pdf, daily["doy"].to_numpy(), daily["wh_per_wp"].to_numpy(),
            _t("Yield distribution for each day  —  Wh / Wp  of all PV together"),
            "Wh / Wp",
            face="#E0B000",
            edge="#FFF4A3",
            y_unit_step=1.0,
            location_title=location_title,
        )
        scatter_page(
            pdf, daily["doy"].to_numpy(), daily["wh_per_wp_0009"].to_numpy(),
            _t("Yield distribution for each day  —  Wh / Wp  of all PV together  —  00:00–09:00"),
            "Wh / Wp",
            face="#E0B000",
            edge="#FFF4A3",
            y_unit_step=0.2,
            location_title=location_title,
        )
        scatter_page(
            pdf, daily["doy"].to_numpy(), daily["wh_per_wp_1524"].to_numpy(),
            _t("Yield distribution for each day  —  Wh / Wp  of all PV together  —  15:00–23:59"),
            "Wh / Wp",
            face="#E0B000",
            edge="#FFF4A3",
            y_unit_step=0.2,
            location_title=location_title,
        )
        # Share of day's yield inside / outside 09:00–15:00 winter time
        midday_wh = combined["P_all"].where((hwt >= 9) & (hwt < 15), 0.0).groupby(combined["date"]).sum()
        outside_wh = combined["P_all"].where((hwt < 9) | (hwt >= 15), 0.0).groupby(combined["date"]).sum()
        total_day = combined["P_all"].groupby(combined["date"]).sum().replace(0, np.nan)
        share_mid = (midday_wh / total_day).reindex(daily["date"].dt.date).fillna(0).to_numpy() * 100
        share_out = (outside_wh / total_day).reindex(daily["date"].dt.date).fillna(0).to_numpy() * 100
        scatter_page(
            pdf, daily["doy"].to_numpy(), share_mid,
            _t("Share of the day’s yield in 09:00–15:00 winter time"),
            "%",
            face="#5C6BC0",
            edge="#C5CAE9",
            y_unit_step=5.0,
            location_title=location_title,
            y_from_top=(0, 100),
        )
        scatter_page(
            pdf, daily["doy"].to_numpy(), share_out,
            _t("Share of the day’s yield outside 09:00–15:00 winter time"),
            "%",
            face="#00897B",
            edge="#B2DFDB",
            y_unit_step=5.0,
            location_title=location_title,
            y_from_top=(0, 100),
        )
        scatter_page(
            pdf, daily["doy"].to_numpy(), daily["t_max"].to_numpy(),
            _t("Highest temperature of each day"),
            "°C",
            face="#C62828",
            edge="#EF9A9A",
            y_unit_step=1.0,
            location_title=location_title,
        )
        scatter_page(
            pdf, daily["doy"].to_numpy(), daily["t_mean"].to_numpy(),
            _t("Average temperature of each day"),
            "°C",
            face="#9E9E9E",
            edge="#E0E0E0",
            y_unit_step=1.0,
            location_title=location_title,
        )
        scatter_page(
            pdf, daily["doy"].to_numpy(), daily["t_min"].to_numpy(),
            _t("Lowest temperature of each day"),
            "°C",
            face="#1565C0",
            edge="#90CAF9",
            y_unit_step=1.0,
            location_title=location_title,
        )
        scatter_page(
            pdf, daily["doy"].to_numpy(), daily["t_range"].to_numpy(),
            _t("Difference between highest and lowest temperature of each day"),
            "°C",
            face="#616161",
            edge="#BDBDBD",
            y_unit_step=1.0,
            location_title=location_title,
        )
        table_pages(
            pdf, monthly, yearly, pv_labels, location_title, peak_info,
            title=_t("Monthly and yearly specific yield  (kWh/kWp = Wh/Wp)"),
            monthly_fmt="{:.2f}",
            yearly_fmt="{:.1f}",
        )
        table_pages(
            pdf, monthly_kwh, yearly_kwh, pv_labels, location_title, peak_info,
            title=_t("Monthly and yearly total yield  (kWh)"),
            monthly_fmt="{:.1f}",
            yearly_fmt="{:.0f}",
        )
        if demand:
            from pege_energy_sim_object_house import simulate  # local: add_demand_pages, outcome_tables, write_emergency_log, write_import_log
            log("Running demand simulation…")
            def _stop():
                return bool(getattr(log, "stop", None) and log.stop())
            sim = simulate(sim_combined, demand, log=log, stop=_stop)
            elog = pdf_path.with_name(pdf_path.stem + " emergency.txt")
            n_emerg = write_emergency_log(elog, sim["hourly"])
            if n_emerg:
                log(f"Emergency log: {elog}  ({n_emerg} hours)")
            else:
                log("No emergency imports, no emergency file")
            add_demand_pages(pdf, sim, location_title)
            hourly = sim["hourly"]
            outcome = {
                "export_kwh": float(hourly["export_kwh"].sum()),
                "top5_yield": sim.get("top5_yield") or [],
                "import_kwh": float(hourly["import_kwh"].sum()),
                "own_kwh": float(hourly["own_kwh"].sum()),
                "wasted_kwh": float(hourly["wasted_kwh"].sum()),
                "heat_kwh": float(hourly["heat_kwh"].sum()),
                "cool_kwh": float(hourly["cool_kwh"].sum()),
                "dhw_kwh": float(hourly["dhw_kwh"].sum()),
                "direct_kwh": float(hourly["direct_kwh"].sum()),
                "pages": outcome_tables(hourly, float((demand or {}).get("battery_kwh") or 0), demand),
            }
        else:
            outcome = {}

        d = pdf.infodict()
        d["Title"] = f"{object_name} - {simname} - {sanitize_name(name)}"
        d["Author"] = "PEGE energy sim - Roland Mösl - founder@pege.org - https://github.com/pegefounder/energy-sim - https://www.pege.org/energy-sim/"
        d["Subject"] = reopen_url or f"{lat},{lon} {startyear}-{endyear}"
        pv_names = ", ".join(str(a.get("label") or "") for a in arrays if a.get("label"))
        d["Keywords"] = f"{object_name} - {simname} - {sanitize_name(name)} - {pv_names}"

    sidecar = {
        "software_version": LOCAL_VERSION,
        "pdf_language": lang,
        "form_values": form_values or {},
        "reopen_url": reopen_url,
        "name": name,
        "lat": lat,
        "lon": lon,
        "startyear": startyear,
        "endyear": endyear,
        "loss": loss,
        "raddatabase": raddatabase,
        "usehorizon": usehorizon,
        "object": object_name,
        "simname": simname,
        "demand": demand,
        "outcome": outcome,
        "location_stem": location_stem(name, lat, lon, cache_dir=Path(outdir)),
        "arrays": [
            {k: a[k] for k in ("label", "slope", "azimuth", "wp", "wall_d", "wall_h", "bifacial", "bifaciality", "face") if k in a}
            for a in arrays
        ],
    }
    json_path = pdf_path.with_suffix(".json")
    json_path.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"Wrote parameters: {json_path}")

    if reopen_url:
        try:
            from pypdf import PdfReader, PdfWriter
            from pypdf.annotations import Link
            from pypdf.generic import Fit
            reader = PdfReader(str(pdf_path))
            writer = PdfWriter()
            writer.append(reader)
            # bottom strip of page 1, A4 landscape points
            writer.add_annotation(0, Link(
                rect=(20, 12, 520, 36),
                url=reopen_url,
            ))
            with pdf_path.open("wb") as fh:
                writer.write(fh)
        except Exception as exc:
            pass
    log(f"Done: {pdf_path}")
    return pdf_path



def parse_pv_arg(text: str) -> tuple:
    """name:slope,azimuth,Wp   or   slope,azimuth,Wp   or   name,slope,azimuth,Wp"""
    text = text.strip()
    label = ""
    rest = text
    if ":" in text:
        label, rest = text.split(":", 1)
        label = label.strip()
        parts = [p.strip() for p in rest.split(",")]
    else:
        parts = [p.strip() for p in text.split(",")]
        if len(parts) == 4:
            label, parts = parts[0], parts[1:]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(
            'Each --pv must be slope,azimuth,Wp or "Name:slope,azimuth,Wp"'
        )
    slope, azimuth, wp = map(float, parts)
    if wp <= 0:
        raise argparse.ArgumentTypeError("Wp must be positive")
    return (label, slope, azimuth, wp) if label else (slope, azimuth, wp)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Download PVGIS hourly data for several orientations and build a PDF report."
    )
    p.add_argument("--name", default=None, help="Location name")
    p.add_argument("--lat", type=float, default=None)
    p.add_argument("--lon", type=float, default=None)
    p.add_argument(
        "--pv", action="append", type=parse_pv_arg, default=None,
        help='Name:slope,azimuth,Wp or slope,azimuth,Wp (repeatable). 0=south, 90=west, -90=east',
    )
    p.add_argument("--startyear", type=int, default=None)
    p.add_argument("--endyear", type=int, default=None)
    p.add_argument("--loss", type=float, default=None, help="PVGIS system loss percent (default 14)")
    p.add_argument("--raddatabase", default=None, help="auto | PVGIS-SARAH3 | PVGIS-NSRDB | PVGIS-ERA5")
    p.add_argument("--no-horizon", action="store_true")
    p.add_argument("--outdir", default=None, help="Parent directory for the location folder")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    name = args.name if args.name is not None else CONFIG["name"]
    lat = args.lat if args.lat is not None else CONFIG["lat"]
    lon = args.lon if args.lon is not None else CONFIG["lon"]
    pv = args.pv if args.pv else list(CONFIG["pv"])
    startyear = args.startyear if args.startyear is not None else CONFIG["startyear"]
    endyear = args.endyear if args.endyear is not None else CONFIG["endyear"]
    loss = args.loss if args.loss is not None else CONFIG["loss"]
    raddatabase = args.raddatabase if args.raddatabase is not None else CONFIG["raddatabase"]
    usehorizon = not args.no_horizon
    if args.outdir:
        outdir = Path(args.outdir)
    elif CONFIG["outdir"]:
        outdir = Path(CONFIG["outdir"])
    else:
        outdir = Path.cwd()

    print(f"{datetime.now():%Y-%m-%d %H:%M}  PEGE energy sim")
    print(f"  {name}  {lat}, {lon}  {len(pv)} array(s)  {startyear}-{endyear}")
    build_report(
        name=name,
        lat=lat,
        lon=lon,
        pv_list=pv,
        startyear=startyear,
        endyear=endyear,
        loss=loss,
        raddatabase=raddatabase,
        usehorizon=usehorizon,
        outdir=outdir,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())


# i18n
_LANG_DIR = Path(__file__).with_name("languages")
_CACHE: dict[str, dict[str, str]] = {}
_CODE = {
    "german": "de", "de": "de",
    "spanish": "es", "es": "es",
    "french": "fr", "fr": "fr",
    "italian": "it", "it": "it",
    "polish": "pl", "pl": "pl",
    "czech": "cs", "cs": "cs",
    "slovak": "sk", "sk": "sk",
    "hungarian": "hu", "hu": "hu",
    "romanian": "ro", "ro": "ro",
    "slovenian": "sl", "sl": "sl",
    "croatian": "hr", "hr": "hr",
    "portugese": "pt", "portuguese": "pt", "pt": "pt",
}

def set_language(lang: str | None) -> None:
    """Set the language used by _t and _fmt for the rest of this process."""
    global _CURRENT_LANG
    _CURRENT_LANG = (lang or "english").lower().strip() or "english"


def _load(lang: str) -> dict[str, str]:
    lang = _CODE.get(lang, lang)
    if lang in _CACHE:
        return _CACHE[lang]
    path = _LANG_DIR / f"{lang}.json"
    if not path.is_file():
        _CACHE[lang] = {}
        return _CACHE[lang]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    _CACHE[lang] = {str(k): str(v) for k, v in data.items()}
    return _CACHE[lang]


def _t(text: str, lang: str | None = None) -> str:
    """Return the translation of text. English (or missing) returns the original."""
    use = (lang or _CURRENT_LANG or "english").lower()
    if not use or use == "english":
        return text
    return _load(use).get(text, text)


def _fmt(n, digits: int = 0) -> str:
    """Format a number with the thousands separator usual for the current language."""
    if n is None:
        return ""
    try:
        val = float(n)
    except Exception:
        return str(n)
    if digits == 0:
        s = f"{val:,.0f}"
    else:
        s = f"{val:,.{digits}f}"
    # German-style (and most European languages): . thousands, , decimal
    if _CURRENT_LANG in {
        "german", "de", "spanish", "es", "portugese", "pt", "french", "fr",
        "italian", "it", "polish", "pl", "czech", "cs", "slovak", "sk",
        "hungarian", "hu", "romanian", "ro", "slovenian", "sl", "croatian", "hr",
    }:
        s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return s


# Report helpers
def write_emergency_log(path, hourly: pd.DataFrame) -> int:
    """One block per emergency hour: the 72 hours before it, then the hour itself."""
    hits = hourly.index[hourly["emerg_kw"] > 1e-6].tolist()
    if not hits:
        return 0
    lines = [
        "Emergency import debug",
        "An emergency is an hour whose grid import is above that month's max import kW.",
        "Each block is the 72 hours before that hour, then the hour. Time is UTC.",
        "Columns: YYYY-MM-DD HH:MM  WT  PV  load  SOC  import  cap  export  emerg  lunch_short",
        "",
    ]
    for i in hits:
        start = max(0, i - 72)
        when = pd.Timestamp(hourly.at[i, "time"])
        lines.append(
            f"=== emergency {when:%Y-%m-%d %H:%M}  "
            f"import {hourly.at[i, 'import_kwh']:.2f} kW  "
            f"cap {hourly.at[i, 'cap_kw']:.2f} kW  "
            f"over {hourly.at[i, 'emerg_kw']:.2f} kW ==="
        )
        lines.append(
            f"{'YYYY-MM-DD HH:MM':16} {'WT':>3} {'PV':>8} {'load':>8} {'SOC':>8} "
            f"{'import':>8} {'cap':>6} {'export':>8} {'emerg':>7} {'lunch':>7}"
        )
        for k in range(start, i + 1):
            t = pd.Timestamp(hourly.at[k, "time"])
            mark = " <<<" if k == i else ""
            lines.append(
                f"{t:%Y-%m-%d %H:%M} {int(hourly.at[k, 'hour_wt']):3d} "
                f"{hourly.at[k, 'pv']:8.2f} {hourly.at[k, 'own_kwh']:8.2f} {hourly.at[k, 'soc']:8.1f} "
                f"{hourly.at[k, 'import_kwh']:8.2f} {hourly.at[k, 'cap_kw']:6.1f} "
                f"{hourly.at[k, 'export_kwh']:8.2f} {hourly.at[k, 'emerg_kw']:7.2f} "
                f"{hourly.at[k, 'lunch_emerg']:7.2f}{mark}"
            )
        lines.append("")
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    return len(hits)


def write_import_log(path, hourly: pd.DataFrame) -> int:
    """One block per import hour: the 120 hours before it, then the hour itself."""
    hits = hourly.index[hourly["import_kwh"] > 1e-6].tolist()
    if not hits:
        return 0
    lines = [
        "Import debug",
        "Any grid import, also inside the monthly cap.",
        "Each block is the 120 hours before that hour, then the hour. Time is UTC.",
        "Columns: YYYY-MM-DD HH:MM  WT  PV  load  demand  SOC  import  export",
        "",
    ]
    has_demand = "demand_kwh" in hourly.columns
    for i in hits:
        start = max(0, i - 120)
        when = pd.Timestamp(hourly.at[i, "time"])
        lines.append(
            f"=== import {when:%Y-%m-%d %H:%M}  "
            f"WT {int(hourly.at[i, 'hour_wt']):02d}  "
            f"import {hourly.at[i, 'import_kwh']:.2f} kWh ==="
        )
        lines.append(
            f"{'YYYY-MM-DD HH:MM':16} {'WT':>3} {'PV':>8} {'load':>8} {'demand':>8} "
            f"{'SOC':>8} {'import':>8} {'export':>8}"
        )
        for k in range(start, i + 1):
            t = pd.Timestamp(hourly.at[k, "time"])
            mark = " <<<" if k == i else ""
            dem = hourly.at[k, "demand_kwh"] if has_demand else 0.0
            lines.append(
                f"{t:%Y-%m-%d %H:%M} {int(hourly.at[k, 'hour_wt']):3d} "
                f"{hourly.at[k, 'pv']:8.2f} {hourly.at[k, 'own_kwh']:8.2f} {dem:8.2f} "
                f"{hourly.at[k, 'soc']:8.1f} {hourly.at[k, 'import_kwh']:8.2f} "
                f"{hourly.at[k, 'export_kwh']:8.2f}{mark}"
            )
        lines.append("")
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    return len(hits)


def _money_columns(hourly, daily, ym, demand):
    """Euro columns. Peak fee is monthly and is added to that month's import."""
    rate = float((demand or {}).get("peak_eur_kw_month") or 0.0)
    fixed = float((demand or {}).get("fixed_ct") or 0.0)
    if "import_eur" not in hourly.columns:
        hourly = hourly.copy()
        hourly["import_eur"] = 0.0
        hourly["export_eur"] = 0.0
    day_eur = hourly.groupby(["year", "month", "doy"], as_index=False).agg(
        import_eur=("import_eur", "sum"),
        export_eur=("export_eur", "sum"),
    )
    daily = daily.merge(day_eur, on=["year", "month", "doy"], how="left")
    daily["import_eur"] = daily["import_eur"].fillna(0.0)
    daily["export_eur"] = daily["export_eur"].fillna(0.0)
    daily["peak_fee_eur"] = 0.0
    daily["import_incl_eur"] = daily["import_eur"]
    daily["balance_eur"] = daily["export_eur"] - daily["import_eur"]
    ym = ym.merge(
        daily.groupby(["year", "month"], as_index=False)[["import_eur", "export_eur"]].sum(),
        on=["year", "month"], how="left",
    )
    peak_imp = hourly.groupby(["year", "month"], as_index=False).agg(peak_kw=("import_kwh", "max"))
    ym = ym.merge(peak_imp, on=["year", "month"], how="left")
    ym["peak_fee_eur"] = ym["peak_kw"].fillna(0.0) * rate
    ym["import_incl_eur"] = ym["import_eur"].fillna(0.0) + ym["peak_fee_eur"]
    ym["balance_eur"] = ym["export_eur"].fillna(0.0) - ym["import_incl_eur"]
    if fixed > 0 and "own_kwh" in hourly.columns:
        daily["fixed_eur"] = daily["own_kwh"] * fixed / 100.0
        own_peak = hourly.groupby(["year", "month"], as_index=False).agg(own_peak=("own_kwh", "max"))
        ym = ym.merge(
            daily.groupby(["year", "month"], as_index=False)[["fixed_eur"]].sum(),
            on=["year", "month"], how="left",
        )
        ym = ym.merge(own_peak, on=["year", "month"], how="left")
        ym["fixed_eur"] = ym["fixed_eur"].fillna(0.0) + ym["own_peak"].fillna(0.0) * rate
    return daily, ym


def outcome_tables(hourly: pd.DataFrame, batt: float, demand: dict | None = None) -> dict:
    """Pages 21–25 as JSON: month and day maximum, average, minimum."""
    daily = hourly.groupby(["year", "month", "doy"], as_index=False).agg(
        export_kwh=("export_kwh", "sum"),
        export_high=("export_high", "sum"),
        export_med=("export_med", "sum"),
        export_low=("export_low", "sum"),
        import_kwh=("import_kwh", "sum"),
        own_kwh=("own_kwh", "sum"),
        heat_kwh=("heat_kwh", "sum"),
        cool_kwh=("cool_kwh", "sum"),
        dhw_kwh=("dhw_kwh", "sum"),
        direct_kwh=("direct_kwh", "sum"),
        wasted_kwh=("wasted_kwh", "sum"),
        discharge_kwh=("discharge_kwh", "sum"),
    )
    daily["total_kwh"] = daily["own_kwh"]
    denom = max(float(batt or 0), 1e-6)
    daily["cycles"] = daily["discharge_kwh"] / denom
    ym = daily.groupby(["year", "month"], as_index=False)[
        ["export_kwh", "export_high", "export_med", "export_low", "import_kwh", "total_kwh",
         "heat_kwh", "cool_kwh", "dhw_kwh", "direct_kwh", "wasted_kwh", "discharge_kwh"]
    ].sum()
    ym["cycles"] = ym["discharge_kwh"] / denom
    daily, ym = _money_columns(hourly, daily, ym, demand)
    peaks = hourly.groupby(["year", "month", "doy"], as_index=False).agg(
        charge_c=("charge_c", "max"), discharge_c=("discharge_c", "max"),
    )
    ym_c = peaks.groupby(["year", "month"], as_index=False)[["charge_c", "discharge_c"]].max()
    money_keys = ["export_eur", "import_eur", "peak_fee_eur", "import_incl_eur", "balance_eur"]
    money_labels = ["Export", "Import", "Peak grid fee", "Import incl. peak fee", "Balance"]
    if "fixed_eur" in daily.columns:
        money_keys.append("fixed_eur")
        money_labels.append("No PV, no battery, fixed tariff")
    pages = {
        "Energy in kWh": _chapter_rows(daily, ym, ["export_kwh", "import_kwh", "total_kwh"],
                                ["Export to grid", "Import from grid", "Total energy used for house"]),
        "Energy by price": _chapter_rows(daily, ym, money_keys, money_labels),
        "Export in kWh by price hour": _chapter_rows(daily, ym, ["export_kwh", "export_high", "export_med", "export_low"],
                                         ["Export total", "Export during high price hours", "Export during medium price hours", "Export during low price hours"]),
        "Heat pump": _chapter_rows(daily, ym, ["heat_kwh", "cool_kwh", "dhw_kwh", "direct_kwh"],
                                   ["Heating", "Cooling", "DHW", "Direct electric"]),
        "Battery": _chapter_rows(daily, ym, ["wasted_kwh", "cycles"], ["Wasted unused", "Full cycles"])
        + _chapter_rows(peaks, ym_c, ["charge_c", "discharge_c"], ["Charge C", "Discharge C"]),
    }
    return pages
    if series is None or len(series) == 0:
        return {"min": 0.0, "max": 0.0, "avg": 0.0}
    return {
        "min": float(series.min()),
        "max": float(series.max()),
        "avg": float(series.mean()),
    }



def _mma(series):
    if series is None or len(series) == 0:
        return {"min": 0.0, "max": 0.0, "avg": 0.0}
    return {"min": float(series.min()), "max": float(series.max()), "avg": float(series.mean())}


def _chapter_rows(daily, year_month, keys, labels):
    """Two blocks of 3 lines: month totals, then best/average/worst day."""
    kinds = (("max", _t("Maximum"), "#d0d0d0"), ("avg", _t("average"), "#ececec"), ("min", _t("minimum"), "#ffffff"))
    rows = []
    for key, label in zip(keys, labels):
        rows.append({"title": label, "head": True})
        money = "€" in label
        for kind, title, color in kinds:
            rec = {"title": f"  month {title}"}
            for m, name in enumerate(MONTHS, 1):
                part = year_month[year_month["month"] == m][key]
                rec[name] = _mma(part)[kind]
            rec["All"] = _mma(year_month.groupby("year")[key].sum())[kind]
            rows.append(rec)
        for kind, title, color in kinds:
            rec = {"title": f"  day {title}"}
            for m, name in enumerate(MONTHS, 1):
                part = daily[daily["month"] == m][key]
                rec[name] = _mma(part)[kind]
            rec["All"] = _mma(daily[key])[kind]
            rows.append(rec)
    return rows


def _draw_stat_page(pdf, title, note, rows, location_title):
    fig, ax = plt.subplots(figsize=(11.69, 8.27))
    ax.axis("off")
    ax.set_title(f"{title}\n{location_title}", loc="left", fontsize=12)
    cols = list(MONTHS) + ["All"]
    headers = [""] + cols
    body = []
    for rec in rows:
        line = [rec["title"]]
        title_l = (rec.get("title") or "").lower()
        is_head = rec.get("head") or (not title_l.startswith("  "))
        is_c = "c" in title_l and ("charge" in title_l or "discharge" in title_l or "cycle" in title_l)
        is_eur = "price" in title_l or "balance" in title_l or "fee" in title_l
        is_month = "month" in title_l
        for c in cols:
            val = rec.get(c, "")
            if is_head:
                line.append("")
            elif is_c:
                line.append(_fmt(val, 2))
            elif is_eur:
                line.append(_fmt(val, 1))
            elif is_month:
                line.append(_fmt(val, 0))
            else:
                line.append(_fmt(val, 1))
        body.append(line)
    table = ax.table(cellText=body, colLabels=headers, loc="upper center", cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(6.0)
    table.scale(1.0, 1.15)
    n_cols = 1 + len(cols)
    name_w = 0.22
    other_w = (0.96 - name_w) / max(1, n_cols - 1)
    for (r, c), cell in table.get_celld().items():
        cell.set_edgecolor("#ccc")
        cell.set_width(name_w if c == 0 else other_w)
        if r == 0:
            cell.set_facecolor("#3d5a80")
            cell.set_text_props(color="white", weight="bold", fontsize=6.2)
        else:
            rec = rows[r - 1]
            title_l = (rec.get("title") or "").lower()
            is_head = rec.get("head") or (not title_l.startswith("  "))
            if is_head:
                color = "#1d3557"
            elif "maximum" in title_l or "max" in title_l:
                color = "#d0d0d0"
            elif "durchschnitt" in title_l or "average" in title_l or "avg" in title_l:
                color = "#ececec"
            elif "minimum" in title_l or "min" in title_l:
                color = "#ffffff"
            else:
                color = "#d0d0d0"
            cell.set_facecolor(color)
            if is_head:
                cell.set_text_props(color="white", weight="bold", fontsize=6.4)
            elif c == 0:
                cell.set_text_props(ha="left", fontsize=6.2)
    fig.tight_layout(rect=(0.02, 0.08, 0.99, 0.96))
    fig.text(0.06, 0.02, note, fontsize=7.5, va="bottom")
    pdf.savefig(fig, dpi=160)
    plt.close(fig)


def _style_year_axis(ax) -> None:
    ax.set_xlim(1, 366)
    ax.set_xticks(MONTH_STARTS)
    ax.set_xticklabels(["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])
    ax.set_xlabel(_t("Day of year"), fontsize=11)
    ax.grid(True, which="major", axis="y", color="#bbbbbb", linewidth=0.6)
    ax.grid(True, which="major", axis="x", color="#888888", linewidth=0.8)
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def add_demand_pages(pdf, result: dict, location_title: str) -> None:
    hourly = result["hourly"]
    batt = float((result.get("demand") or {}).get("battery_kwh") or 160)
    grid = float((result.get("demand") or {}).get("grid_kw") or (result.get("demand") or {}).get("export_kw") or 17)
    if grid <= 0:
        grid = 17.0
    daily_h = hourly.groupby(["year", "doy"], as_index=False).agg(
        exp_kwh=("export_kwh", "sum"),
        imp_kwh=("import_kwh", "sum"),
        emerg=("emerg_kw", "max"),
    )
    daily_h["exp_h"] = daily_h["exp_kwh"] / grid
    daily_h["imp_h"] = daily_h["imp_kwh"] / grid
    n_emerg_hours = int((hourly["emerg_kw"] > 1e-6).sum())
    n_emerg_days = int((daily_h["emerg"] > 1e-6).sum())

    from pege_energy_sim_common import scatter_page
    scatter_page(
        pdf,
        daily_h["doy"].to_numpy(),
        daily_h["exp_h"].to_numpy(),
        _t("Export energy as equivalent hours  (kWh / grid kW)"),
        _t("Equivalent hours"),
        face="#2E8B57",
        edge="#C8F5D4",
        y_unit_step=0.5,
        location_title=location_title,
    )
    scatter_page(
        pdf,
        daily_h["doy"].to_numpy(),
        daily_h["imp_h"].to_numpy(),
        _t("Import energy as equivalent hours  (kWh / grid kW)"),
        _t("Equivalent hours"),
        face="#C0392B",
        edge="#F8C9C4",
        y_unit_step=0.5,
        location_title=location_title,
    )

    fig, ax = plt.subplots(figsize=(11.69, 8.27))
    hit = daily_h[daily_h["emerg"] > 1e-6]
    if n_emerg_hours == 0:
        ax.text(
            0.5, 0.55, _t("0 emergency imports"),
            ha="center", va="center", fontsize=22, color="#2E8B57",
            transform=ax.transAxes,
        )
        ax.set_ylim(0, 1)
        ax.set_yticks([])
    else:
        ax.scatter(
            hit["doy"].to_numpy(), hit["emerg"].to_numpy(),
            s=14, facecolors="#6C3483", edgecolors="#D2B4DE",
            linewidths=0.45, alpha=0.78, zorder=3,
        )
        ymax = max(1.0, float(hit["emerg"].max()))
        ax.set_ylim(-0.15, np.ceil(ymax) + 0.35)
        ax.set_yticks(range(0, int(np.ceil(ymax)) + 1))
        ax.set_ylabel(_t("Highest emergency import that day  (kW)"), fontsize=11)
    ax.set_title(
        f"{_t('Emergency import hours')}: {n_emerg_hours}    "
        f"{_t('days with an emergency')}: {n_emerg_days}\n{location_title}",
        fontsize=13, pad=10,
    )
    _style_year_axis(ax)
    fig.tight_layout(rect=(0.04, 0.16, 0.99, 0.96))
    fig.text(
        0.06, 0.04,
        _t("Emergency = this hour’s grid import is above the month’s max import kW. 2 kW means up to 48 kWh in 24 hours. More than 2 kW in an hour is the higher grid fee.\nImport inside that cap is not an emergency. Preferred cheap hours are 00:00–06:00 and 09:00–15:00 winter time."),
        fontsize=8, va="bottom", color="#333",
    )
    pdf.savefig(fig, dpi=160)
    plt.close(fig)

    daily = hourly.groupby(["year", "month", "doy"], as_index=False).agg(
        export_kwh=("export_kwh", "sum"),
        export_high=("export_high", "sum"),
        export_med=("export_med", "sum"),
        export_low=("export_low", "sum"),
        import_kwh=("import_kwh", "sum"),
        pv_kwh=("pv", "sum"),
        wasted_kwh=("wasted_kwh", "sum"),
        heat_kwh=("heat_kwh", "sum"),
        cool_kwh=("cool_kwh", "sum"),
        dhw_kwh=("dhw_kwh", "sum"),
        direct_kwh=("direct_kwh", "sum"),
        own_kwh=("own_kwh", "sum"),
        discharge_kwh=("discharge_kwh", "sum"),
    )
    daily["pv_own_kwh"] = (daily["pv_kwh"] - daily["export_kwh"] - daily["wasted_kwh"]).clip(lower=0)
    daily["total_kwh"] = daily["own_kwh"]
    ym = daily.groupby(["year", "month"], as_index=False)[
        ["export_kwh", "export_high", "export_med", "export_low", "import_kwh", "pv_own_kwh", "total_kwh", "own_kwh",
         "heat_kwh", "cool_kwh", "dhw_kwh", "direct_kwh", "wasted_kwh", "discharge_kwh"]
    ].sum()
    _draw_stat_page(
        pdf, _t("Energy in kWh"),
        _t("Month rows: kWh in that month.  Day rows: best / average / worst day.\nTotal energy used for house = household + car + heating + cooling + DHW + direct electric.\nPV not exported and not marked wasted is still in the battery, or could not leave because the grid was already full. It is not house use."),
        _chapter_rows(daily, ym,
                      ["export_kwh", "import_kwh", "total_kwh"],
                      [_t("Export to grid"), _t("Import from grid"), _t("Total energy used for house")]),
        location_title,
    )
    rate = float((result.get("demand") or {}).get("peak_eur_kw_month") or 0.0)
    fixed = float((result.get("demand") or {}).get("fixed_ct") or 0.0)
    daily, ym = _money_columns(hourly, daily, ym, result.get("demand") or {})
    money_keys = ["export_eur", "import_eur", "peak_fee_eur", "import_incl_eur", "balance_eur"]
    money_labels = [_t("Export"), _t("Import"), _t("Peak grid fee"), _t("Import incl. peak fee"), _t("Balance")]
    if fixed > 0:
        money_keys.append("fixed_eur")
        money_labels.append(_t("No PV, no battery, fixed tariff"))
    _draw_stat_page(
        pdf, _t("Energy by price"),
        _t("Currency in Your units. Import and export use the cent/kWh of that hour. Peak fee {rate:g} / kW / month is the highest import hour of the month (hourly stand-in for the 15-minute peak) and is added to that month’s import. Day rows have no peak fee. Balance = export − import incl. peak fee.\nSell only when export is above 0. At 0 cent, export PV yield only, not the battery. Do not sell when the earning is negative.").format(rate=rate)
        + (f"  Fixed tariff {fixed:g} cent/kWh is the bill with no PV and no battery, plus the same peak fee on the hourly house load." if fixed else ""),
        _chapter_rows(daily, ym, money_keys, money_labels),
        location_title,
    )
    _draw_stat_page(
        pdf, _t("Export in kWh during price hours"),
        _t("kWh exported while that price band was active. Not the money. Winter time. High 06:00–09:00 and 17:00–20:00, sold first. Low 09:00–15:00, sold last. Medium is the rest."),
        _chapter_rows(daily, ym,
                      ["export_kwh", "export_high", "export_med", "export_low"],
                      [_t("Export total"), _t("Export during high price hours"), _t("Export during medium price hours"), _t("Export during low price hours")]),
        location_title,
    )
    _draw_stat_page(
        pdf, _t("Heat pump"),
        _t("Heating, cooling and DHW are electricity.  Direct electric is the part the heat pump could not cover."),
        _chapter_rows(daily, ym,
                      ["heat_kwh", "cool_kwh", "dhw_kwh", "direct_kwh"],
                      [_t("Heating"), _t("Cooling"), _t("DHW"), _t("Direct electric")]),
        location_title,
    )
    denom = max(batt, 1e-6)
    daily["cycles"] = daily["discharge_kwh"] / denom
    ym["cycles"] = ym["discharge_kwh"] / denom
    peaks = hourly.groupby(["year", "month", "doy"], as_index=False).agg(
        charge_c=("charge_c", "max"),
        discharge_c=("discharge_c", "max"),
    )
    ym_c = peaks.groupby(["year", "month"], as_index=False)[["charge_c", "discharge_c"]].max()
    batt_rows = _chapter_rows(daily, ym, ["wasted_kwh", "cycles"], [_t("Wasted"), _t("Full cycles")])
    batt_rows += _chapter_rows(peaks, ym_c, ["charge_c", "discharge_c"], [_t("Charge C"), _t("Discharge C")])
    _draw_stat_page(
        pdf, _t("Battery"),
        _t("Wasted = PV left when the export price is negative and the battery is full.  Full cycles = discharge kWh / {batt:g} kWh.  Battery export only into hours whose export price is above 0, highest price first. At 0 cent only PV yield leaves, not the battery. Negative price is not sold.").format(batt=batt),
        batt_rows,
        location_title,
    )
    soc_day = hourly.groupby(["year", "doy"], as_index=False).agg(
        soc_max=("soc", "max"),
        soc_min=("soc", "min"),
    )
    scatter_page(
        pdf,
        daily["doy"].to_numpy(),
        daily["cycles"].to_numpy(),
        _t("Battery cycles per day  (discharge / {batt:g} kWh)").format(batt=batt),
        _t("Full cycles"),
        face="#1d4e89",
        edge="#c5d8f0",
        y_unit_step=0.05,
        location_title=location_title,
    )
    scatter_page(
        pdf,
        soc_day["doy"].to_numpy(),
        (soc_day["soc_max"] / denom * 100.0).to_numpy(),
        _t("Battery max charge state per day  ({batt:g} kWh = 100 %)").format(batt=batt),
        _t("% of battery"),
        face="#1a7a4c",
        edge="#c8efd4",
        y_unit_step=5.0,
        location_title=location_title,
    )
    scatter_page(
        pdf,
        soc_day["doy"].to_numpy(),
        (soc_day["soc_min"] / denom * 100.0).to_numpy(),
        _t("Battery min charge state per day  ({batt:g} kWh = 100 %)").format(batt=batt),
        _t("% of battery"),
        face="#a15c07",
        edge="#f6ddb0",
        y_unit_step=5.0,
        location_title=location_title,
    )


# Demand — form and thermal helpers
SRC_NODES = (-10.0, 0.0, 10.0)
FLOW_NODES = (35.0, 50.0)



def default_prices() -> dict[str, float]:
    """Cent/kWh. Spot example: low export can be negative, so that hour is not sold."""
    return {
        "px_su_wd_imp_hi": 40, "px_su_wd_imp_mid": 25, "px_su_wd_imp_lo": 18,
        "px_su_wd_exp_hi": 22, "px_su_wd_exp_mid": 7, "px_su_wd_exp_lo": 3,
        "px_su_we_imp_hi": 30, "px_su_we_imp_mid": 22, "px_su_we_imp_lo": 12,
        "px_su_we_exp_hi": 12, "px_su_we_exp_mid": 4, "px_su_we_exp_lo": -3,
        "px_wi_wd_imp_hi": 40, "px_wi_wd_imp_mid": 27, "px_wi_wd_imp_lo": 24,
        "px_wi_wd_exp_hi": 22, "px_wi_wd_exp_mid": 9, "px_wi_wd_exp_lo": 6,
        "px_wi_we_imp_hi": 30, "px_wi_we_imp_mid": 24, "px_wi_we_imp_lo": 21,
        "px_wi_we_exp_hi": 12, "px_wi_we_exp_mid": 6, "px_wi_we_exp_lo": 3,
    }



DEFAULTS = {
    "t_out_design": -10.0,
    "t_in_design": 20.0,
    "q_design_w": 8000.0,
    "t_heat_on": 22.0,
    "t_cool_on": 25.0,
    "thermal_mass_kwh_k": 25.0,
    "t_ground": 10.0,
    "slope": 0.4,
    "t_flow_min": 25.0,
    "t_flow_max": 55.0,
    "dhw_liters_40": 200.0,
    "dhw_tank_c": 60.0,
    "dhw_pipe_c": (8.0, 10.0, 12.0, 10.0),  # Q1..Q4
    "dhw_from": 8,
    "dhw_to": 18,
    "house_kwh_day": 8.0,
    "eta_pv_batt": 0.97,
    "eta_pv_ac": 0.96,
    "eta_pv_dc": 0.97,
    "eta_batt_ac": 0.95,
    "eta_batt_dc": 0.97,
    "eta_ac_dc": 0.92,
    "battery_kwh": 160.0,
    "c_rate": 0.5,
    "soc_min": 0.10,
    "soc_max": 0.95,
    "soc_start": 0.50,
    "inverter_kw": 34.0,
    "grid_kw": 17.0,
    "dc_charger_kw": 30.0,
    "ac_charger_kw": 11.0,
    "export_kw": 17.0,
    "hp_source": "both",
    "lunch_kwh": 30.0,
    "night_kwh": 40.0,
    "import_cap_kw": [2.0] * 12,
    "peak_eur_kw_month": 6.33,
    "fixed_ct": 0.0,
    # preferred windows in winter time
    "import_a": (0, 6),
    "import_b": (9, 15),
    "no_export": (9, 15),
    "export_from": 16,
    # COP / max heat kW at source -10, 0, 10 × flow 35, 50
    "hp_cop": {
        (-10, 35): 2.4, (-10, 50): 1.9,
        (0, 35): 3.4, (0, 50): 2.6,
        (10, 35): 4.5, (10, 50): 3.3,
    },
    "hp_kw": {
        (-10, 35): 8.0, (-10, 50): 7.0,
        (0, 35): 10.0, (0, 50): 9.0,
        (10, 35): 12.0, (10, 50): 10.0,
    },
}



# ---------------------------------------------------------------------------
# Locked design decisions
# ---------------------------------------------------------------------------
# Design heat: three numbers T_out, T_in, Q_W  →  UA = Q_W / (T_in - T_out)
# Operating band: heat if T_air < T_heat_on, cool if T_air > T_cool_on,
#                 do nothing in between (e.g. 22 °C … 25 °C).
# Indoor during heat = T_heat_on, during cool = T_cool_on.
# Efficiencies: charge and discharge separate.
# Grid power is unlimited. Monthly import kW is a *design target*.
#   Import above the target, or outside preferred windows, = emergency.
#   Goal of design: few emergencies, each < 10 kW, over all PVGIS years.
# Source: each hour pick air or ground from temperature
#   heating: T_src = max(T_air, T_ground)
#   cooling: T_src = min(T_air, T_ground)
# Cooling map: derived from the 12 heating-map fields (see cop_cool).
# Lunch DC charge that does not fit in the house battery → emergency import
#   on AC (grid → battery / house) and then DC if still needed; remainder
#   is still flagged emergency.
# Inverter short: house > heat pump > night AC charge > export.
#   Unmet AC after inverter + battery is grid import (emergency if it
#   violates the monthly target or the preferred windows).
# DC lunch charger does not pass the inverter.
# No underground seasonal store in this version.
# Same demand profile every day. Bivalence is computed, not typed.
# ---------------------------------------------------------------------------

MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)

# 2 seasons × 2 day types × import/export × high/medium/low = 24 cent/kWh fields.
# Summer = April–September, winter = October–March. Weekend = Sat–Sun, winter time.
# High 06:00–09:00 and 17:00–20:00, low 09:00–15:00, medium the rest.

def ua_w_per_k(t_out: float, t_in: float, q_w: float) -> float:
    d = t_in - t_out
    if d <= 0:
        raise ValueError("Design indoor temperature must be above outdoor")
    if q_w <= 0:
        return 0.0
    return q_w / d


def flow_temp(t_air: float, t_in: float, slope: float, t_min: float, t_max: float) -> float:
    tw = t_in + slope * (t_in - t_air)
    return max(t_min, min(t_max, tw))


def source_temp(t_air: float, t_ground: float, cooling: bool, source: str = "both") -> float:
    src = (source or "both").lower()
    if src == "air":
        return t_air
    if src == "ground":
        return t_ground
    if cooling:
        return min(t_air, t_ground)
    return max(t_air, t_ground)


def _bilinear(table: dict, t_src: float, t_flow: float) -> float:
    s0, s1, s2 = SRC_NODES
    f0, f1 = FLOW_NODES
    ts = max(t_src, s0)
    if ts > s2 + 15:
        ts = s2 + 15
    tf = min(max(t_flow, f0), f1)
    if ts <= s1:
        a, b, wa = s0, s1, (ts - s0) / (s1 - s0)
    else:
        a, b, wa = s1, s2, (ts - s1) / (s2 - s1)
        if wa > 1.0:
            wa = 1.0 + min(wa - 1.0, 1.5)  # mild extra COP above +10 °C
    wb = (tf - f0) / (f1 - f0)
    q00 = table[(int(a) if a in (-10, 0, 10) else a, 35)]
    # keys are ints in DEFAULTS
    def at(src, flow):
        return table[(int(src), int(flow))]

    v0 = at(a, 35) * (1 - wb) + at(a, 50) * wb
    v1 = at(b, 35) * (1 - wb) + at(b, 50) * wb
    return v0 * (1 - wa) + v1 * wa


def hp_heating(t_src: float, t_flow: float, cop_map: dict, kw_map: dict) -> tuple[float, float]:
    return _bilinear(cop_map, t_src, t_flow), _bilinear(kw_map, t_src, t_flow)


def hp_cooling(t_src: float, cop_map: dict, kw_map: dict) -> tuple[float, float]:
    """Useful assumption: chiller ≈ heating map at 35 °C flow, COP × 0.9."""
    cop, kw = hp_heating(t_src, 35.0, cop_map, kw_map)
    return cop * 0.9, kw


def building_mode(t_air: float, t_heat_on: float, t_cool_on: float) -> str:
    if t_air < t_heat_on:
        return "heat"
    if t_air > t_cool_on:
        return "cool"
    return "off"


def building_load_w(t_air: float, ua: float, t_heat_on: float, t_cool_on: float) -> tuple[str, float]:
    mode = building_mode(t_air, t_heat_on, t_cool_on)
    if mode == "heat":
        return mode, ua * (t_heat_on - t_air)
    if mode == "cool":
        return mode, ua * (t_air - t_cool_on)
    return mode, 0.0


# 1 litre × 1 K × 4.186 kJ = 4.186/3600 kWh
WH_PER_L_K = 4.186 / 3.6  # Wh per litre-kelvin


def clamp_tank_c(t: float) -> float:
    return min(65.0, max(40.0, float(t)))


def pipe_temp_for_month(month: int, pipe_q) -> float:
    q = (int(month) - 1) // 3
    vals = list(pipe_q)
    if q < 0 or q > 3:
        q = 0
    return float(vals[q])


def dhw_day(liters_40: float, tank_c: float, pipe_c: float) -> dict:
    """200 L at 40 °C from 60 °C tank and 10 °C pipe → 120 L from tank.

    Energy is liters_40 × c × (40 − pipe), same as
    v_tank × c × (tank − pipe). Produced 08:00–18:00 winter time.
    If the heat pump is already at capacity for space heat, the rest
    of that hour is direct electric.
    """
    tank_c = clamp_tank_c(tank_c)
    liters_40 = max(0.0, float(liters_40))
    dt_use = 40.0 - float(pipe_c)
    dt_tank = tank_c - float(pipe_c)
    if liters_40 <= 0:
        return {
            "liters_40": 0.0, "liters_tank": 0.0, "kwh_th": 0.0,
            "tank_c": tank_c, "pipe_c": pipe_c, "frac_tank": 0.0,
        }
    if dt_tank <= 0.05:
        # tank not hotter than pipe — all electric from pipe to 40 °C
        kwh = liters_40 * WH_PER_L_K * max(dt_use, 0.0) / 1000.0
        return {
            "liters_40": liters_40, "liters_tank": liters_40,
            "kwh_th": kwh, "tank_c": tank_c, "pipe_c": pipe_c, "frac_tank": 1.0,
        }
    frac = dt_use / dt_tank
    v_tank = liters_40 * frac
    kwh = v_tank * WH_PER_L_K * dt_tank / 1000.0
    return {
        "liters_40": liters_40,
        "liters_tank": v_tank,
        "kwh_th": kwh,
        "tank_c": tank_c,
        "pipe_c": pipe_c,
        "frac_tank": frac,
    }


# ---------------------------------------------------------------------------
# Form field names (shared with pvgis_server)
# ---------------------------------------------------------------------------
import html as _html


def _esc(v) -> str:
    return _html.escape(str(v))



def demand_form_defaults() -> dict[str, str]:
    d = DEFAULTS
    out = {
        "tout": d["t_out_design"],
        "tin": d["t_in_design"],
        "qw": d["q_design_w"],
        "theat": d["t_heat_on"],
        "tcool": d["t_cool_on"],
        "tground": d["t_ground"],
        "hslope": d["slope"],
        "flowmin": d["t_flow_min"],
        "flowmax": d["t_flow_max"],
        "dhw_l": d["dhw_liters_40"],
        "dhw_tank": d["dhw_tank_c"],
        "dhw_p1": d["dhw_pipe_c"][0],
        "dhw_p2": d["dhw_pipe_c"][1],
        "dhw_p3": d["dhw_pipe_c"][2],
        "dhw_p4": d["dhw_pipe_c"][3],
        "house": d["house_kwh_day"],
        "tmass": d["thermal_mass_kwh_k"],
        "epvb": int(d.get("eta_pv_batt", 0.97) * 100),
        "epva": int(d.get("eta_pv_ac", 0.96) * 100),
        "epvd": int(d.get("eta_pv_dc", 0.97) * 100),
        "ebac": int(d.get("eta_batt_ac", 0.95) * 100),
        "ebdc": int(d.get("eta_batt_dc", 0.97) * 100),
        "eacd": int(d.get("eta_ac_dc", 0.92) * 100),
        "batt": d["battery_kwh"],
        "crate": d["c_rate"],
        "socmin": int(d["soc_min"] * 100),
        "socplan": 20,
        "socplan": int(d.get("soc_plan_min", 0.20) * 100),
        "socmax": int(d["soc_max"] * 100),
        "soc0": int(d["soc_start"] * 100),
        "inv": d["inverter_kw"],
        "grid": d["grid_kw"],
        "hpsrc": d["hp_source"],
        "dc": d["dc_charger_kw"],
        "ac": d["ac_charger_kw"],
        "expkw": d["export_kw"],
        "lunch": d["lunch_kwh"],
        "night": d["night_kwh"],
        "predsec": 0.95,
        "fillhi": 1.2,
        "filllo": 0.6,
    }
    for i, cap in enumerate(d["import_cap_kw"], 1):
        out[f"imp_{i}"] = cap
    out["peak_eur"] = d.get("peak_eur_kw_month", 6.33)
    out["fixed_ct"] = d.get("fixed_ct", 0.0)
    for key, val in default_prices().items():
        out[key] = val
    for src in (-10, 0, 10):
        for flow in (35, 50):
            out[f"cop_{src}_{flow}"] = d["hp_cop"][(src, flow)]
            out[f"kw_{src}_{flow}"] = d["hp_kw"][(src, flow)]
    return {k: str(v) for k, v in out.items()}


def demand_form_html(values: dict | None = None) -> str:
    """Fieldsets appended to the PVGIS form. Heating-curve field is hslope
    so it does not clash with PV slope1…slope25."""
    base = demand_form_defaults()
    if values:
        for k, val in values.items():
            if val is not None and str(val) != "":
                base[k] = str(val)
    g = lambda n: _esc(base.get(n, ""))
    months = "".join(
        f"<div class='field tiny'><label>{m}</label>"
        f"<input name='imp_{i}' value='{g(f'imp_{i}')}' inputmode='decimal'></div>"
        for i, m in enumerate(MONTHS, 1)
    )
    hp_rows = []
    for src in (-10, 0, 10):
        hp_rows.append(
            "<tr>"
            f"<td class='n'>{src} °C</td>"
            f"<td><input name='cop_{src}_35' value='{g(f'cop_{src}_35')}'></td>"
            f"<td><input name='kw_{src}_35' value='{g(f'kw_{src}_35')}'></td>"
            f"<td><input name='cop_{src}_50' value='{g(f'cop_{src}_50')}'></td>"
            f"<td><input name='kw_{src}_50' value='{g(f'kw_{src}_50')}'></td>"
            "</tr>"
        )
    def px(key):
        return (
            f"<input name='{key}' value='{g(key)}' inputmode='decimal'>"
        )
    def six(season, day, direction):
        return "".join(f"<td>{px(f'px_{season}_{day}_{direction}_{b}')}</td>" for b in ("hi", "mid", "lo"))
    price_html = f"""
<fieldset><legend>Grid targets and prices</legend>
<p class="hint">
  Monthly import kW is a design target. Grid is unlimited; overflow = emergency import.
  Currency in Your units. No currency sign is drawn. Prices are cent per kWh. Summer April–September, winter October–March.
  Workday Monday–Friday, weekend Saturday–Sunday, clock is winter time.
  High 06:00–09:00 and 17:00–20:00, low 09:00–15:00, medium the rest.
  Example: spot 2 cent → export 2 − 2 handling − 1 Ausgleichsenergie = −1,
  import 2 + 2 handling + 10 grid = 14.
  Do not sell when export is negative. At 0 cent, export PV yield only, not the battery.
  Peak fee is per kW per month of the highest import hour (hourly stand-in for the 15-minute peak).
  Burgenland 76 / 12 = 6.33. It is added to that month’s import.
  Fixed tariff > 0: no export, import only at this price. 0 = use the spot table.
</p>
<div class="row">{months}</div>
<div class="row">
  <div class="field small"><label>Peak fee / kW / month</label><input name="peak_eur" value="{g('peak_eur')}"></div>
  <div class="field small"><label>Fixed tariff cent/kWh</label><input name="fixed_ct" value="{g('fixed_ct')}" placeholder="0"></div>
  <span class="hint" style="align-self:end;margin:0 0 8px 8px">>0 = no export, import only at this price. 0 = spot table.</span>
</div>
<table>
  <thead><tr><th>Season</th><th>Day</th><th>Flow</th><th>High</th><th>Medium</th><th>Low</th></tr></thead>
  <tbody>
    <tr><td>Summer</td><td>Workday</td><td>Import</td>{six('su','wd','imp')}</tr>
    <tr><td>Summer</td><td>Workday</td><td>Export</td>{six('su','wd','exp')}</tr>
    <tr><td>Summer</td><td>Weekend</td><td>Import</td>{six('su','we','imp')}</tr>
    <tr><td>Summer</td><td>Weekend</td><td>Export</td>{six('su','we','exp')}</tr>
    <tr><td>Winter</td><td>Workday</td><td>Import</td>{six('wi','wd','imp')}</tr>
    <tr><td>Winter</td><td>Workday</td><td>Export</td>{six('wi','wd','exp')}</tr>
    <tr><td>Winter</td><td>Weekend</td><td>Import</td>{six('wi','we','imp')}</tr>
    <tr><td>Winter</td><td>Weekend</td><td>Export</td>{six('wi','we','exp')}</tr>
  </tbody>
</table>
</fieldset>
"""
    return f"""
<fieldset><legend>Demand — building</legend>
<p class="hint">
  UA = design W / (indoor − outdoor). Heat-on / cool-on are the indoor band (e.g. 22–26 °C).
  Mass kWh/K is the house thermal capacity — indoor temperature moves slowly.
  Surplus PV that would be wasted at too-low spot market prices runs the heat pump toward the middle of the temperature band.
</p>
<table class="inline-fields">
  <tr>
    <th>Household only kWh/d</th>
    <th>Out °C</th><th>In °C</th><th>Heat W</th>
    <th>Heat on</th><th>Cool on</th>
  </tr>
  <tr>
    <td><input name="house" value="{g('house')}"></td>
    <td><input name="tout" value="{g('tout')}"></td>
    <td><input name="tin" value="{g('tin')}"></td>
    <td><input name="qw" value="{g('qw')}"></td>
    <td><input name="theat" value="{g('theat')}"></td>
    <td><input name="tcool" value="{g('tcool')}"></td>
  </tr>
  <tr>
    <th>Mass kWh/K</th><th>Curve</th><th>Min flow</th><th>Ground °C</th><th>Max flow</th>
  </tr>
  <tr>
    <td><input name="tmass" value="{g('tmass')}"></td>
    <td><input name="hslope" value="{g('hslope')}"></td>
    <td><input name="flowmin" value="{g('flowmin')}"></td>
    <td><input name="tground" value="{g('tground')}"></td>
    <td><input name="flowmax" value="{g('flowmax')}"></td>
  </tr>
</table>
</fieldset>

<fieldset><legend>Demand — DHW</legend>
<p class="hint">
  Litres per day at 40 °C tap. Tank 40–65 °C. Water out of the pipe, per quarter.
  Example: 200 L at 40 °C, tank 60 °C, pipe 10 °C → 120 L from the tank
  (40 % pipe + 60 % tank). Produced 08:00–18:00 winter time.
  If the heat pump is already full with room heat, DHW is direct electric.
</p>
<table class="inline-fields">
  <tr>
    <th>L/day @ 40 °C</th><th>Tank °C</th>
    <th>Water out of the pipe Q1 °C</th><th>Q2 °C</th><th>Q3 °C</th><th>Q4 °C</th>
  </tr>
  <tr>
    <td><input name="dhw_l" value="{g('dhw_l')}"></td>
    <td><input name="dhw_tank" value="{g('dhw_tank')}"></td>
    <td><input name="dhw_p1" value="{g('dhw_p1')}"></td>
    <td><input name="dhw_p2" value="{g('dhw_p2')}"></td>
    <td><input name="dhw_p3" value="{g('dhw_p3')}"></td>
    <td><input name="dhw_p4" value="{g('dhw_p4')}"></td>
  </tr>
</table>
</fieldset>

<fieldset><legend>Demand — heat pump map</legend>
<p class="hint">
  Source: air, ground, or air and ground (heat uses the warmer, cool the colder).
  COP and max heat kW at −10 / 0 / +10 °C vs flow 35 °C and 50 °C. Cooling = map at 35 °C, COP × 0.9.
</p>
<div class="row">
  <div class="field" style="width:220px"><label>Heat-pump source</label>
    <select name="hpsrc">
      <option value="both"{' selected' if base.get('hpsrc','both')=='both' else ''}>air and ground</option>
      <option value="air"{' selected' if base.get('hpsrc')=='air' else ''}>air</option>
      <option value="ground"{' selected' if base.get('hpsrc')=='ground' else ''}>ground</option>
    </select>
  </div>
</div>
<table>
  <thead><tr><th>Source</th><th>COP @ 35 °C</th><th>kW @ 35 °C</th><th>COP @ 50 °C</th><th>kW @ 50 °C</th></tr></thead>
  <tbody>{''.join(hp_rows)}</tbody>
</table>
</fieldset>

<fieldset><legend>Demand — battery and cars</legend>
<div class="row">
  <div class="field small"><label>Battery kWh</label><input name="batt" value="{g('batt')}"></div>
  <div class="field small"><label>Charge C-rate</label><input name="crate" value="{g('crate')}"></div>
  <div class="field small"><label>SOC min %</label><input name="socmin" value="{g('socmin')}"></div>
  <div class="field small"><label>Plan SOC min %</label><input name="socplan" value="{g('socplan')}"></div>
  <div class="field small"><label>SOC max %</label><input name="socmax" value="{g('socmax')}"></div>
  <div class="field small"><label>Start SOC %</label><input name="soc0" value="{g('soc0')}"></div>
  <div class="field" style="width:420px"><label>Nameplate</label><input name="nameplate" value="{_esc(g('nameplate'))}" placeholder="11 x 51.2 V x 314 Ah"></div>
</div>
<div class="row">
  <div class="field small"><label>Inverter AC kW</label><input name="inv" value="{g('inv')}"></div>
  <div class="field small"><label>Grid connection kW</label><input name="grid" value="{g('grid')}"></div>
  <div class="field small"><label>DC charger kW</label><input name="dc" value="{g('dc')}"></div>
  <div class="field small"><label>Night AC charger kW</label><input name="ac" value="{g('ac')}"></div>
  <div class="field small"><label>Max export kW</label><input name="expkw" value="{g('expkw')}"></div>
  <div class="field small"><label>Lunch 12–13 kWh DC</label><input name="lunch" value="{g('lunch')}"></div>
  <div class="field small"><label>Night 20–07 kWh AC</label><input name="night" value="{g('night')}"></div>
</div>
</fieldset>
<fieldset><legend>Efficiencies</legend>
<p class="hint">One number per path. Arrows are the same both ways. AC ↔ DC is the link between an AC side and a DC side. Percent.</p>
<div class="row">
  <div class="field small"><label>PV → battery</label><input name="epvb" value="{g('epvb')}"></div>
  <div class="field small"><label>PV → AC</label><input name="epva" value="{g('epva')}"></div>
  <div class="field small"><label>PV → DC</label><input name="epvd" value="{g('epvd')}"></div>
  <div class="field small"><label>battery ↔ AC</label><input name="ebac" value="{g('ebac')}"></div>
  <div class="field small"><label>battery ↔ DC</label><input name="ebdc" value="{g('ebdc')}"></div>
  <div class="field small"><label>AC ↔ DC</label><input name="eacd" value="{g('eacd')}"></div>
</div>
</fieldset>
{price_html}
"""


def parse_demand_fields(fields: dict) -> dict:
    def first(name, default=""):
        raw = fields.get(name)
        if isinstance(raw, list):
            raw = raw[0] if raw else ""
        if raw is None:
            raw = default
        return str(raw).strip().replace(",", ".")

    def f(name, default):
        raw = first(name, str(default))
        return float(raw) if raw else float(default)

    def pct(name, default):
        x = f(name, default * 100)
        return x / 100.0 if x > 1.5 else x

    # accept old "slope" from demand_server or new "hslope"
    hs = first("hslope") or first("slope")
    slope = float(hs) if hs else DEFAULTS["slope"]
    q = f("qw", DEFAULTS["q_design_w"])
    tout = f("tout", DEFAULTS["t_out_design"])
    tin = f("tin", DEFAULTS["t_in_design"])
    cop, kw = {}, {}
    for src in (-10, 0, 10):
        for flow in (35, 50):
            cop[(src, flow)] = f(f"cop_{src}_{flow}", DEFAULTS["hp_cop"][(src, flow)])
            kw[(src, flow)] = f(f"kw_{src}_{flow}", DEFAULTS["hp_kw"][(src, flow)])
    demand = {
        "t_out_design": tout,
        "t_in_design": tin,
        "q_design_w": q,
        "ua_w_per_k": ua_w_per_k(tout, tin, q),
        "t_heat_on": f("theat", 22),
        "t_cool_on": f("tcool", 25),
        "t_ground": f("tground", 10),
        "slope": slope,
        "t_flow_min": f("flowmin", 25),
        "t_flow_max": f("flowmax", 55),
        "dhw_liters_40": f("dhw_l", 200),
        "dhw_tank_c": clamp_tank_c(f("dhw_tank", 60)),
        "dhw_pipe_c": (
            f("dhw_p1", 8), f("dhw_p2", 10), f("dhw_p3", 12), f("dhw_p4", 10),
        ),
        "dhw_from": 8,
        "dhw_to": 18,
        "house_kwh_day": f("house", 8),
        "thermal_mass_kwh_k": f("tmass", 25),
        "eta_pv_batt": pct("epvb", 0.97),
        "eta_pv_ac": pct("epva", 0.96),
        "eta_pv_dc": pct("epvd", 0.97),
        "eta_batt_ac": pct("ebac", 0.95),
        "eta_batt_dc": pct("ebdc", 0.97),
        "eta_ac_dc": pct("eacd", 0.92),
        "battery_kwh": f("batt", 160),
        "c_rate": f("crate", 0.5),
        "soc_min": pct("socmin", 0.10),
        "soc_plan_min": pct("socplan", 0.20),
        "soc_max": pct("socmax", 0.95),
        "soc_start": pct("soc0", 0.50),
        "nameplate": first("nameplate"),
        "pred_security": f("predsec", 0.95),
        "fill_upper": f("fillhi", 1.2),
        "fill_lower": f("filllo", 0.6),
        "inverter_kw": f("inv", 34),
        "grid_kw": f("grid", 17),
        "dc_charger_kw": f("dc", 30),
        "ac_charger_kw": f("ac", 11),
        "export_kw": f("expkw", f("grid", 17)),
        "hp_source": (first("hpsrc") or "both").lower(),
        "lunch_kwh": f("lunch", 30),
        "night_kwh": f("night", 40),
        "import_cap_kw": [f(f"imp_{i}", 2.0) for i in range(1, 13)],
        "peak_eur_kw_month": f("peak_eur", 6.33),
        "fixed_ct": f("fixed_ct", 0.0),
        "prices": {key: f(key, default_prices()[key]) for key in default_prices()},
        "hp_cop": {f"{a}/{b}": cop[(a, b)] for a, b in cop},
        "hp_kw": {f"{a}/{b}": kw[(a, b)] for a, b in kw},
        "lon": f("lon", 15),
    }
    return demand


def demand_to_form_values(demand: dict) -> dict[str, str]:
    if not demand:
        return demand_form_defaults()
    # already flat form keys?
    if "tout" in demand or "qw" in demand:
        return {k: str(v) for k, v in demand.items() if v is not None}
    out = demand_form_defaults()
    map_back = {
        "t_out_design": "tout", "t_in_design": "tin", "q_design_w": "qw",
        "t_heat_on": "theat", "t_cool_on": "tcool", "t_ground": "tground",
        "slope": "hslope", "t_flow_min": "flowmin", "t_flow_max": "flowmax",
        "dhw_liters_40": "dhw_l", "dhw_tank_c": "dhw_tank",
        "house_kwh_day": "house", "thermal_mass_kwh_k": "tmass",
        "battery_kwh": "batt", "c_rate": "crate", "inverter_kw": "inv",
        "grid_kw": "grid", "hp_source": "hpsrc",
        "dc_charger_kw": "dc", "ac_charger_kw": "ac", "export_kw": "expkw",
        "lunch_kwh": "lunch", "night_kwh": "night",
        "nameplate": "nameplate",
    }
    for src, dst in map_back.items():
        if src in demand and demand[src] is not None:
            out[dst] = str(demand[src])
    pipes = demand.get("dhw_pipe_c") or ()
    for i, val in enumerate(pipes, 1):
        out[f"dhw_p{i}"] = str(val)
    for key, dst in (("eta_pv_batt", "epvb"), ("eta_pv_ac", "epva"), ("eta_pv_dc", "epvd"),
                     ("eta_batt_ac", "ebac"), ("eta_batt_dc", "ebdc"), ("eta_ac_dc", "eacd"),
                     ("soc_min", "socmin"), ("soc_plan_min", "socplan"), ("soc_max", "socmax"), ("soc_start", "soc0")):
        if key in demand and demand[key] is not None:
            x = float(demand[key])
            out[dst] = str(int(round(x * 100)) if x <= 1.5 else int(round(x)))
    caps = demand.get("import_cap_kw") or []
    for i, cap in enumerate(caps, 1):
        out[f"imp_{i}"] = str(cap)
    if demand.get("peak_eur_kw_month") is not None:
        out["peak_eur"] = str(demand["peak_eur_kw_month"])
    if demand.get("fixed_ct") is not None:
        out["fixed_ct"] = str(demand["fixed_ct"])
    for key, val in (demand.get("prices") or {}).items():
        out[str(key)] = str(val)
    for table, prefix in (("hp_cop", "cop"), ("hp_kw", "kw")):
        raw = demand.get(table) or {}
        for k, val in raw.items():
            if isinstance(k, tuple):
                src, flow = k
            else:
                src, flow = str(k).split("/")
            out[f"{prefix}_{src}_{flow}"] = str(val)
    return out
