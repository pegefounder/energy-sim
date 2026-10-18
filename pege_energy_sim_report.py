#!/usr/bin/env python3
"""
PEGE energy sim — open-source hourly simulation on PVGIS weather.
Copyright (c) 2026 Roland Mösl founder@pege.org
Licensed under the GNU General Public License v3.0. See LICENSE.

Photovoltaic yield, battery, spot-market import and export, a peak-demand
grid fee, heat pump (space heat, cooling, DHW) and car charging.
Create objects and locations, then compare two runs.

https://github.com/pegefounder/energy-sim
https://www.pege.org/energy-sim/

PVGIS multi-orientation hourly analysis and PDF report.

Downloads hourly PVGIS seriescalc data (2005–2023, 1 kWp) per orientation,
caches files per location, combines arrays hour by hour, and writes a PDF
with daily scatter charts (yield + temperatures) and monthly/yearly statistics.

Example:
  python pvgis_multi_pv.py \\
      --name "Krems" --lat 48.400 --lon 15.989 \\
      --pv "Roof south:30,0,2000" --pv "Fence east:90,-90,1500"

  python pvgis_multi_pv.py          # uses the CONFIG block below

Orientation: slope_deg,azimuth_deg,Wp
  azimuth: 0 = south, 90 = west, -90 = east, 180 = north  (PVGIS convention)
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.backends.backend_pdf import PdfPages
import numpy as np
import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Edit this block when you run the script without command-line arguments.
# ---------------------------------------------------------------------------
CONFIG = {
    "name": "Krems",
    "lat": 48.400,
    "lon": 15.989,
    # Each item: (name, slope_deg, azimuth_deg, Wp)  or  (slope, azimuth, Wp)
    # azimuth: 0 = south, 90 = west, -90 = east
    "pv": [
        ("Roof south", 30, 0, 2000),
        ("Fence east", 90, -90, 1500),
        ("Fence west", 90, 90, 1275),
    ],
    "startyear": 2005,
    "endyear": 2023,
    "loss": 14.0,
    "raddatabase": "auto",  # auto | PVGIS-SARAH3 | PVGIS-NSRDB | PVGIS-ERA5
    "usehorizon": True,
    "outdir": None,  # None = current working directory
}

PVGIS_URL = "https://re.jrc.ec.europa.eu/api/v5_3/seriescalc"
REQUEST_TIMEOUT = 300

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
# Non-leap month starts used as vertical grid (day-of-year)
MONTH_STARTS = [1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335]
MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
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
                "header": f"{a['label']}\n{a['slope']:g}°/{a['azimuth']:g}°\n{a['wp']:g} Wp",
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
    ax.set_title(f"{title}\n{location_title}", fontsize=13, pad=10)
    ax.set_ylabel(ylabel, fontsize=11)
    ax.set_xlim(1, 366)
    ax.set_xticks(MONTH_STARTS)
    ax.set_xticklabels(MONTH_LABELS)
    ax.set_xlabel("Day of year", fontsize=11)

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
        ax.set_title(f"{title}\n{location_title}", fontsize=12, pad=8)

        col_ids = pv_ids + [n_pv]  # last is All together
        headers = ["Month / year", ""] + [
            (pv_labels[j] if j < n_pv else "All\ntogether")
            for j in col_ids
        ]

        rows = []
        # 12 months × 3 stats
        for m in range(1, 13):
            stats = ["Maximum", "average", "minimum"]
            keys = ["max", "mean", "min"]
            for s_i, (stat_name, key) in enumerate(zip(stats, keys)):
                label = MONTH_NAMES[m - 1] if s_i == 0 else ""
                row = [label, stat_name]
                for j in col_ids:
                    val = monthly.loc[m, (j, key)]
                    row.append("—" if val != val else monthly_fmt.format(val))
                rows.append(row)
        # yearly block
        rows.append(["", "", *([""] * len(col_ids))])
        for stat_name, key in zip(["Maximum", "average", "minimum"], ["max", "mean", "min"]):
            label = "All years" if stat_name == "Maximum" else ""
            row = [label, stat_name]
            for j in col_ids:
                val = yearly.loc[0, (j, key)]
                row.append("—" if val != val else yearly_fmt.format(val))
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
            f"Highest combined yield hour: "
            f"{peak_info['time'].strftime('%Y-%m-%d %H:%M')} UTC   "
            f"{peak_info['watts']:.1f} W  "
            f"({peak_info['w_per_wp']:.3f} W/Wp)   "
            f"of {peak_info['total_wp']:.0f} Wp installed.  "
            f"Use this hour to size battery charge power."
        )
        fig.text(0.5, 0.035, peak_txt, ha="center", va="bottom", fontsize=8)
        if len(groups) > 1:
            fig.text(0.98, 0.015, f"Table {g_idx + 1}/{len(groups)}", ha="right", fontsize=7, color="#666")
        fig.tight_layout(rect=(0.01, 0.06, 0.99, 0.96))
        _save(pdf, fig)
        plt.close(fig)



def record_pages(pdf, combined, arrays, total_wp, location_title):
    """Top 10 combined hours and how often the plant is near that record. Page 1."""
    if not arrays:
        fig, ax = plt.subplots(figsize=(11.69, 8.27))
        ax.axis("off")
        ax.set_title(f"Records 1–10\n{location_title}", fontsize=13, pad=8, loc="left")
        ax.text(0.5, 0.5, "No photovoltaic installed.", ha="center", va="center", fontsize=12)
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
            f"Records 1–10  —  contribution of each PV\n{location_title}",
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
                row.append(f"{float(rec[cols[idx]]):.0f}")
            body.append(row)
        if start + chunk >= len(names):
            watts = ["Watt at record"] + [f"{float(v):.0f}" for v in top["P_all"]]
            pct = ["Percent of installed peak"] + [
                f"{100.0 * float(v) / total_wp:.1f}" if total_wp else "—" for v in top["P_all"]
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
            "Values are watts at that hour. Date and time are UTC. "
            f"Record {record:.0f} W  ({100.0 * record / total_wp:.1f} % of {total_wp:.0f} Wp)." if total_wp else f"Record {record:.0f} W. No PV installed."
            if total_wp else "",
            fontsize=8,
        )
        fig.tight_layout(rect=(0.02, 0.06, 0.98, 0.94))
        _save(pdf, fig)
        plt.close(fig)

    fig, ax = plt.subplots(figsize=(11.69, 8.27))
    ax.axis("off")
    ax.set_title(f"Hours at or above a share of the record\n{location_title}", fontsize=13, pad=8, loc="left")
    levels = [98, 96, 94, 92, 90]
    body = []
    for pct in levels:
        n_h = int((combined["P_all"] >= (pct / 100.0) * record).sum()) if record else 0
        body.append([f"{pct} %", f"{n_h}", f"{pct / 100.0 * record:.0f} W"])
    table = ax.table(
        cellText=body,
        colLabels=["Of the record", "Hours", "Watts"],
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
    fig.text(0.04, 0.04, f"Record hour = {record:.0f} W combined.", fontsize=8)
    fig.tight_layout(rect=(0.15, 0.1, 0.85, 0.94))
    _save(pdf, fig)
    plt.close(fig)


def per_pv_records_page(pdf, combined, arrays, location_title):
    """Records of each photovoltaic field. Used for MPPT and inverter sizing."""
    if not arrays:
        fig, ax = plt.subplots(figsize=(11.69, 8.27))
        ax.axis("off")
        ax.set_title(f"Records of each photovoltaic field\n{location_title}", fontsize=13, pad=8, loc="left")
        ax.text(0.5, 0.5, "No photovoltaic installed.", ha="center", va="center", fontsize=12)
        fig.tight_layout()
        _save(pdf, fig)
        plt.close(fig)
        return
    names = [a.get("label", f"PV {i}") for i, a in enumerate(arrays, 1)]
    cols = [f"P{i}" for i in range(1, len(arrays) + 1)]
    fig, ax = plt.subplots(figsize=(11.69, 8.27))
    ax.axis("off")
    ax.set_title(
        f"Records of each photovoltaic field\n{location_title}",
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
            body.append([name, date_str, f"{peak:.0f}", f"{pct:.1f}", *counts])
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
        "Watts is the highest hour of that field. % is of its own Wp. "
        "Hours are the count of hours at or above that share of its own record. "
        "Use this to size the MPPT or the inverter for that field.",
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
    ax.set_ylabel("kWh that day", fontsize=11)
    ax.legend(frameon=False, loc="upper right")
    ax.set_xlim(1, 366)
    ax.set_xticks([1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335])
    ax.set_xticklabels(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])
    ax.set_xlabel("Day of year", fontsize=11)
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
            f"{a['wp']:g}",
            wall or "—",
            face,
            bi or "—",
            "yes" if a.get("count_wp", True) else "no",
        ])
    return rows


def scenario_pages(pdf, location_title, object_name, arrays, demand, db, startyear, endyear, loss, peak_info, gap_dates=None, reopen_url=None, remark=""):
    """Scenario between the yield tables and the demand charts."""
    fig, ax = plt.subplots(figsize=(11.69, 8.27))
    ax.axis("off")
    who = object_name or "PV only"
    ax.set_title(f"Scenario — PV\n{who}    {location_title}", fontsize=13, pad=8, loc="left")
    headers = ["Name", "Slope °", "Azimuth °", "Wp", "Wall d/h m", "Face", "Bifacial", "Wp counted"]
    body = _pv_rows(arrays)
    total = sum(a["wp"] for a in arrays if a.get("count_wp", True))
    body.append(["Total counted", "", "", f"{total:g}", "", "", "", ""])
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
        f"Database gaps: {len(gap_dates)} days with 0 yield while the sun reached the horizon at noon."
        + (f"  {', '.join(str(d) for d in gap_dates)}." if gap_dates else "")
        + "  Yield charts skip them. The simulation uses the lowest-yield day of that month."
    )
    import textwrap
    note = (
        f"Database {db}    {startyear}–{endyear}    loss {loss:g} %    "
        f"azimuth 0 = south, 90 = west, −90 = east.\n"
        f"Price bands and charts use UTC{round(float((demand or {}).get('lon') or 15)/15):+d} from longitude (15° per hour), not the political time zone.\n"
        f"Peak hour {peak_info['time'].strftime('%Y-%m-%d')} "
        f"{(peak_info['time'] + __import__('datetime').timedelta(hours=round(float((demand or {}).get('lon') or 15)/15))).strftime('%H:%M')} local "
        f"({peak_info['time'].strftime('%H:%M')} UTC)    "
        f"{peak_info['watts']:.0f} W    {peak_info['w_per_wp']:.3f} W/Wp of {total:g} Wp.\n"
        + "\n".join(textwrap.wrap(gap_line, 110)) + "\n"
        + "\n".join(textwrap.wrap(
            "Sell when the export price is above 0. At 0 cent export PV yield only, not the battery. "
            "Do not sell when the earning is negative. At 15:00 winter time, battery export is only "
            "the kWh that would not fit in the next 72 h.", 110))
    )
    if remark:
        wrapped = "\n".join(textwrap.wrap(f"Location: {remark}", 110))
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
    ax.set_title(f"Scenario — house, heat pump, battery\n{who}    {location_title}", fontsize=13, pad=8, loc="left")
    caps = list(d.get("import_cap_kw") or [])
    pipes = list(d.get("dhw_pipe_c") or (8, 10, 12, 10))
    rows = [
        ["Building", "Design out / in / heat", f"{d.get('t_out_design', '')} °C / {d.get('t_in_design', '')} °C / {d.get('q_design_w', '')} W"],
        ["", "Heat on / cool on / ground", f"{d.get('t_heat_on', '')} °C / {d.get('t_cool_on', '')} °C / {d.get('t_ground', '')} °C"],
        ["", "Curve / flow min / max", f"{d.get('slope', '')} / {d.get('t_flow_min', '')} °C / {d.get('t_flow_max', '')} °C"],
        ["", "Thermal mass of the house", f"{d.get('thermal_mass_kwh_k', 25)} kWh/K"],
        ["", "Household only", f"{d.get('house_kwh_day', '')} kWh/day"],
        ["DHW", "Litres at 40 °C / tank", f"{d.get('dhw_liters_40', '')} L    tank {d.get('dhw_tank_c', '')} °C    {d.get('dhw_from', 8)}–{d.get('dhw_to', 18)} h"],
        ["", "Water out of the pipe Q1–Q4", "  ".join(f"{x:g} °C" for x in pipes)],
        ["Heat pump", "Source", "air and ground" if str(d.get("hp_source", "both")) == "both" else str(d.get("hp_source", "air and ground"))],
        ["", "COP −10/0/10 @ 35 °C", _cop(d, 35)],
        ["", "COP −10/0/10 @ 50 °C", _cop(d, 50)],
        ["", "kW −10/0/10 @ 35 °C", _kw(d, 35)],
        ["", "kW −10/0/10 @ 50 °C", _kw(d, 50)],
        ["Battery", "kWh / C-rate / SOC min–max–start", f"{d.get('battery_kwh', '')} kWh    C {d.get('c_rate', 0.5)}    {100*float(d.get('soc_min', 0.1)):.0f} / {100*float(d.get('soc_max', 0.95)):.0f} / {100*float(d.get('soc_start', 0.5)):.0f} %"],
        ["", "Nameplate", str(d.get("nameplate") or "—")],
        ["", "Charge / discharge η", f"{100*float(d.get('eta_charge', 0.95)):.0f} % / {100*float(d.get('eta_discharge', 0.9)):.0f} %"],
        ["Grid", "Inverter / grid connection", f"{d.get('inverter_kw', '')} kW / {d.get('grid_kw', d.get('export_kw', ''))} kW"],
        ["", "Max export", f"{d.get('export_kw', '')} kW"],
        ["", "Import cap Jan–Jun kW", "  ".join(f"{m} {caps[i]:g}" for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun"]))],
        ["", "Import cap Jul–Dec kW", "  ".join(f"{m} {caps[i+6]:g}" for i, m in enumerate(["Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]))],
        ["Cars", "Lunch DC / night AC", f"{d.get('lunch_kwh', '')} kWh @ {d.get('dc_charger_kw', '')} kW    night {d.get('night_kwh', '')} kWh @ {d.get('ac_charger_kw', '')} kW"],
    ]
    table = ax.table(cellText=rows, colLabels=["Chapter", "Parameter", "Value"], loc="upper center", cellLoc="left")
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
    fig.text(0.04, 0.03, "Clock is winter time UTC+1. Emergency import is an hour above the monthly kW cap.", fontsize=8)
    fig.tight_layout(rect=(0.03, 0.06, 0.98, 0.94))
    _save(pdf, fig)
    plt.close(fig)


def _cop(d, flow):
    m = d.get("hp_cop") or {}
    return "   ".join(f"{_mv(m, s, flow):g}" for s in (-10, 0, 10))


def _kw(d, flow):
    m = d.get("hp_kw") or {}
    return "   ".join(f"{_mv(m, s, flow):g}" for s in (-10, 0, 10))


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
    ax.set_title("PVGIS combined PV report", fontsize=16, pad=12, loc="left")

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
) -> Path:
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
            "Yield distribution for each day  —  Wh / Wp  of all PV together",
            "Wh / Wp",
            face="#E0B000",
            edge="#FFF4A3",
            y_unit_step=1.0,
            location_title=location_title,
        )
        scatter_page(
            pdf, daily["doy"].to_numpy(), daily["wh_per_wp_0009"].to_numpy(),
            "Yield distribution for each day  —  Wh / Wp  of all PV together  —  00:00–09:00",
            "Wh / Wp",
            face="#E0B000",
            edge="#FFF4A3",
            y_unit_step=0.2,
            location_title=location_title,
        )
        scatter_page(
            pdf, daily["doy"].to_numpy(), daily["wh_per_wp_1524"].to_numpy(),
            "Yield distribution for each day  —  Wh / Wp  of all PV together  —  15:00–23:59",
            "Wh / Wp",
            face="#E0B000",
            edge="#FFF4A3",
            y_unit_step=0.2,
            location_title=location_title,
        )
        midday = combined["P_all"].where((hwt >= 9) & (hwt < 15), 0.0).groupby(combined["date"]).sum()
        other = combined["P_all"].where((hwt < 9) | (hwt >= 15), 0.0).groupby(combined["date"]).sum()
        total_day = (midday + other).replace(0, np.nan)
        daily["pct_0915"] = (100.0 * midday / total_day).reindex(daily["date"].dt.date).to_numpy()
        daily["pct_other"] = (100.0 * other / total_day).reindex(daily["date"].dt.date).to_numpy()
        scatter_page(
            pdf, daily["doy"].to_numpy(), daily["pct_0915"].to_numpy(),
            "Share of the day’s yield in 09:00–15:00 winter time",
            "% of that day",
            face="#E0B000",
            edge="#FFF4A3",
            y_unit_step=10.0,
            location_title=location_title,
            y_from_top=(0, 100),
        )
        scatter_page(
            pdf, daily["doy"].to_numpy(), daily["pct_other"].to_numpy(),
            "Share of the day’s yield outside 09:00–15:00 winter time",
            "% of that day",
            face="#9E9E9E",
            edge="#E0E0E0",
            y_unit_step=10.0,
            location_title=location_title,
            y_from_top=(0, 100),
        )
        scatter_page(
            pdf, daily["doy"].to_numpy(), daily["t_max"].to_numpy(),
            "Highest temperature of each day",
            "°C",
            face="#C62828",
            edge="#EF9A9A",
            y_unit_step=1.0,
            location_title=location_title,
        )
        scatter_page(
            pdf, daily["doy"].to_numpy(), daily["t_mean"].to_numpy(),
            "Average temperature of each day",
            "°C",
            face="#9E9E9E",
            edge="#E0E0E0",
            y_unit_step=1.0,
            location_title=location_title,
        )
        scatter_page(
            pdf, daily["doy"].to_numpy(), daily["t_min"].to_numpy(),
            "Lowest temperature of each day",
            "°C",
            face="#1565C0",
            edge="#90CAF9",
            y_unit_step=1.0,
            location_title=location_title,
        )
        scatter_page(
            pdf, daily["doy"].to_numpy(), daily["t_range"].to_numpy(),
            "Difference between highest and lowest temperature of each day",
            "°C",
            face="#616161",
            edge="#BDBDBD",
            y_unit_step=1.0,
            location_title=location_title,
        )
        table_pages(
            pdf, monthly, yearly, pv_labels, location_title, peak_info,
            title="Monthly and yearly specific yield  (kWh/kWp = Wh/Wp)",
            monthly_fmt="{:.2f}",
            yearly_fmt="{:.1f}",
        )
        table_pages(
            pdf, monthly_kwh, yearly_kwh, pv_labels, location_title, peak_info,
            title="Monthly and yearly total yield  (kWh)",
            monthly_fmt="{:.1f}",
            yearly_fmt="{:.0f}",
        )
        if demand:
            from pege_energy_sim_demand_sim import add_demand_pages, outcome_tables, simulate, write_emergency_log, write_import_log
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
        "software_version": "0.1",
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


def _load_sidecar(path: Path) -> dict:
    path = Path(path)
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    side = path.with_suffix(".json")
    if not side.is_file():
        raise FileNotFoundError(f"No JSON beside {path.name}")
    return json.loads(side.read_text(encoding="utf-8"))


def _cfg_rows(data: dict) -> list[tuple[str, str]]:
    fv = data.get("form_values") or {}
    rows = [
        ("Object", str(data.get("object") or fv.get("object") or "")),
        ("Simulation", str(data.get("simname") or fv.get("simname") or "")),
        ("Location", str(data.get("name") or "")),
        ("Latitude", str(data.get("lat") or "")),
        ("Longitude", str(data.get("lon") or "")),
        ("Years", f"{data.get('startyear', '')}–{data.get('endyear', '')}"),
        ("Loss %", str(data.get("loss") or "")),
        ("Database", str(data.get("raddatabase") or "")),
    ]
    for i, a in enumerate(data.get("arrays") or [], 1):
        rows.append((
            f"PV {i}",
            f"{a.get('label')}  {a.get('slope')}°/{a.get('azimuth')}°  {a.get('wp')} Wp  "
            f"wall {a.get('wall_d')}/{a.get('wall_h')}",
        ))
    dem = data.get("demand") or {}
    for item in (
        ("q_design_w", "Design heat W"), ("t_out_design", "Design outdoor °C"),
        ("t_in_design", "Design indoor °C"), ("slope", "Heat curve slope"),
        ("t_flow_min", "Min flow °C"), ("t_flow_max", "Max flow °C"),
        "t_heat_on", "t_cool_on", "t_ground", "house_kwh_day",
        "thermal_mass_kwh_k", "battery_kwh", "c_rate", "soc_min", "soc_plan_min",
        "soc_max", "grid_kw", "inverter_kw", ("export_kw", "Max export kW"), "lunch_kwh", "night_kwh",
        "dhw_liters_40", "dhw_tank_c", "peak_eur_kw_month", "fixed_ct", "hp_source",
    ):
        key, label = item if isinstance(item, tuple) else (item, item)
        if key in dem:
            rows.append((label, str(dem.get(key))))
    caps = dem.get("import_cap_kw") or []
    if caps:
        rows.append(("import cap kW", " ".join(f"{float(c):g}" for c in caps)))
    for key in sorted((dem.get("prices") or {})):
        rows.append((key, str(dem["prices"][key])))
    return rows


def _diff_better(title: str, diff: float):
    if abs(diff) < 0.05:
        return None
    t = title.lower()
    more_better = ("export" in t or "balance" in t) and "import" not in t
    return diff > 0 if more_better else diff < 0


def compare_reports(pdf_a: Path, pdf_b: Path, out: Path | None = None,
                    label_a: str = "A", label_b: str = "B", log=None) -> Path:
    """Write a compare PDF: differences first, then each statistics chapter.

    Difference is B − A. Light green when that change is better, light red
    when it is worse. More export and a higher balance are better. More
    import, peak fee, house use, waste and cycles are worse.
    """
    import textwrap
    ja = _load_sidecar(pdf_a)
    jb = _load_sidecar(pdf_b)
    pdf_a, pdf_b = Path(pdf_a), Path(pdf_b)
    out = Path(out) if out else pdf_a.with_name(f"{pdf_a.stem} vs {pdf_b.stem}.pdf")
    out.parent.mkdir(parents=True, exist_ok=True)
    ra, rb = _cfg_rows(ja), _cfg_rows(jb)
    keys, seen = [], set()
    for key, _ in ra + rb:
        if key not in seen:
            seen.add(key)
            keys.append(key)
    ma, mb = dict(ra), dict(rb)
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "All"]

    def wrap(value, width):
        return "\n".join(textwrap.wrap(str(value), width) or ["—"])

    with PdfPages(out) as pdf:
        diffs = [(k, ma.get(k, "—"), mb.get(k, "—")) for k in keys if str(ma.get(k, "—")) != str(mb.get(k, "—"))]
        same = [k for k in keys if str(ma.get(k, "—")) == str(mb.get(k, "—"))]
        # Page 1+: only what differs, wrapped, so the columns stay readable.
        chunk = diffs or [("—", "No differences", "No differences")]
        step = 12
        for i in range(0, len(chunk), step):
            if log:
                log(f"Difference page {i // step + 1}")
            part = chunk[i:i + step]
            fig, ax = plt.subplots(figsize=(11.69, 8.27))
            ax.axis("off")
            ax.set_title("Compare — what differs\nA and B are named under the table", fontsize=13, loc="left")
            body = [[wrap(k, 22), wrap(a, 36), wrap(b, 36)] for k, a, b in part]
            table = ax.table(cellText=body, colLabels=["Parameter", "A", "B"], loc="upper center", cellLoc="left")
            table.auto_set_font_size(False)
            table.set_fontsize(8)
            table.scale(1.0, 1.8)
            for (r, c), cell in table.get_celld().items():
                cell.set_edgecolor("#cccccc")
                cell.set_width(0.24 if c == 0 else 0.36)
                if r == 0:
                    cell.set_facecolor("#1d3557")
                    cell.set_text_props(color="white", weight="bold", fontsize=8)
                else:
                    cell.set_facecolor("#fff6d8")
                    cell.set_text_props(fontsize=8, va="center")
            fig.text(0.04, 0.06, "A  " + label_a + "\nB  " + label_b, fontsize=7, va="bottom")
            fig.text(0.04, 0.02, "Same on both: " + ", ".join(same[:18]) + (" …" if len(same) > 18 else ""), fontsize=7, color="#444")
            fig.tight_layout(rect=(0.03, 0.12, 0.98, 0.94))
            pdf.savefig(fig, dpi=140)
            plt.close(fig)

        pages_a = (ja.get("outcome") or {}).get("pages") or {}
        pages_b = (jb.get("outcome") or {}).get("pages") or {}
        order = ["Energy in kWh", "Energy", "Energy by price", "Export by price", "Heat pump", "Battery"]
        titles = [t for t in order if t in pages_a or t in pages_b]
        for extra in list(pages_a) + list(pages_b):
            if extra not in titles:
                titles.append(extra)
        if "Energy in kWh" in titles and "Energy" in titles:
            titles = [t for t in titles if t != "Energy"]
        for title in titles:
            if log:
                log(f"Chapter {title}")
            rows_a = pages_a.get(title) or []
            rows_b = pages_b.get(title) or []
            # Row titles repeat under each heading ("month Maximum" is not unique).
            def keyed(rows):
                section = ""
                out = {}
                for rec in rows:
                    label = rec.get("title") or ""
                    if rec.get("head"):
                        section = label
                        out[(section, "")] = rec
                    else:
                        out[(section, label)] = rec
                return out
            by_b = keyed(rows_b)
            body, marks = [], []
            section = ""
            for rec in rows_a:
                name = (rec.get("title") or "").replace("€", "").strip()
                if rec.get("head"):
                    section = rec.get("title") or ""
                    body.append([name] + [""] * 13)
                    marks.append("head")
                    continue
                other = by_b.get((section, rec.get("title")), {})
                line1, line2, line3 = [name + "   A"], [name + "   B"], ["B − A"]
                better_cells = [None]
                for col in months:
                    va, vb = rec.get(col), other.get(col)
                    if va is None or vb is None:
                        line1.append("—"); line2.append("—"); line3.append("—")
                        better_cells.append(None)
                        continue
                    diff = float(vb) - float(va)
                    fmt = "{:.2f}" if rec.get("fmt") == "c" else ("{:.1f}" if rec.get("fmt") in {"eur", "day"} or "€" in (rec.get("title") or "") else "{:.0f}")
                    line1.append(fmt.format(float(va)))
                    line2.append(fmt.format(float(vb)))
                    line3.append(f"{diff:+.1f}")
                    better_cells.append(_diff_better(section + " " + name, diff))
                body.extend((line1, line2, line3))
                marks.extend(("a", "b", better_cells))
            if not body:
                body = [["No statistics in this JSON. Run both PDFs again."] + [""] * 13]
                marks = ["head"]
            # One block (heading plus its rows) stays on one page.
            blocks = []
            cur_b, cur_m = [], []
            for row, mark in zip(body, marks):
                if mark == "head" and cur_b:
                    blocks.append((cur_b, cur_m))
                    cur_b, cur_m = [], []
                cur_b.append(row)
                cur_m.append(mark)
            if cur_b:
                blocks.append((cur_b, cur_m))
            pages = []
            part, part_marks = [], []
            for blk, mk in blocks:
                if part and len(part) + len(blk) > 16:
                    pages.append((part, part_marks))
                    part, part_marks = [], []
                part.extend(blk)
                part_marks.extend(mk)
            if part:
                pages.append((part, part_marks))
            for part, part_marks in pages:
                fig, ax = plt.subplots(figsize=(11.69, 8.27))
                ax.axis("off")
                ax.set_title(f"Compare {title}\nLight green: B is better.  Light red: B is worse.  Difference = B − A.", fontsize=11, loc="left")
                table = ax.table(cellText=part, colLabels=[""] + months, loc="upper center", cellLoc="center")
                table.auto_set_font_size(False)
                table.set_fontsize(7)
                table.scale(1.0, 1.35)
                for (r, c), cell in table.get_celld().items():
                    cell.set_edgecolor("#dddddd")
                    cell.set_width(0.18 if c == 0 else 0.062)
                    if r == 0:
                        cell.set_facecolor("#1d3557")
                        cell.set_text_props(color="white", weight="bold", fontsize=7)
                        continue
                    mark = part_marks[r - 1]
                    if mark == "head":
                        cell.set_facecolor("#1d3557")
                        cell.set_text_props(color="white", weight="bold", fontsize=7)
                    elif mark == "a":
                        cell.set_facecolor("#f7f7f7")
                    elif mark == "b":
                        cell.set_facecolor("#ffffff")
                    else:
                        better = mark[c] if c < len(mark) else None
                        if better is True:
                            cell.set_facecolor("#c8efd4")
                        elif better is False:
                            cell.set_facecolor("#f6c9c9")
                        else:
                            cell.set_facecolor("#eeeeee")
                    if c == 0:
                        cell.set_text_props(ha="left", fontsize=6.5)
                fig.text(0.04, 0.03, "A  " + label_a + "\nB  " + label_b, fontsize=7, va="bottom")
                fig.tight_layout(rect=(0.02, 0.08, 0.99, 0.92))
                pdf.savefig(fig, dpi=140)
                plt.close(fig)
    return out



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
