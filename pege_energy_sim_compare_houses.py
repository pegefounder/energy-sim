#!/usr/bin/env python3
"""Compare two house simulations."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

from pege_energy_sim_common import data_root
from pege_energy_sim_common import _t, _fmt

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
        (_t("Object"), str(data.get("object") or fv.get("object") or "")),
        (_t("Simulation"), str(data.get("simname") or fv.get("simname") or "")),
        (_t("Location"), str(data.get("name") or "")),
        (_t("Latitude"), str(data.get("lat") or "")),
        (_t("Longitude"), str(data.get("lon") or "")),
        (_t("Years"), f"{data.get('startyear', '')}–{data.get('endyear', '')}"),
        (_t("Loss %"), str(data.get("loss") or "")),
        (_t("Database"), str(data.get("raddatabase") or "")),
    ]
    for i, a in enumerate(data.get("arrays") or [], 1):
        rows.append((
            f"PV {i}",
            f"{a.get('label')}  {_fmt(a.get('slope'), 0)}°/{_fmt(a.get('azimuth'), 0)}°  {_fmt(a.get('wp'), 0)} Wp  "
            f"wall {a.get('wall_d')}/{a.get('wall_h')}",
        ))
    dem = data.get("demand") or {}
    for item in (
        ("q_design_w", _t("Design heat W")), ("t_out_design", _t("Design outdoor °C")),
        ("t_in_design", _t("Design indoor °C")), ("slope", _t("Heat curve slope")),
        ("t_flow_min", _t("Min flow °C")), ("t_flow_max", _t("Max flow °C")),
        "t_heat_on", "t_cool_on", "t_ground", "house_kwh_day",
        "thermal_mass_kwh_k", "battery_kwh", "c_rate", "soc_min", "soc_plan_min",
        "soc_max", "grid_kw", "inverter_kw", ("export_kw", _t("Max export kW")), "lunch_kwh", "night_kwh",
        "dhw_liters_40", "dhw_tank_c", "peak_eur_kw_month", "fixed_ct", "hp_source",
    ):
        key, label = item if isinstance(item, tuple) else (item, item)
        if key in dem:
            val = dem.get(key)
            try:
                val = _fmt(float(val), 1 if isinstance(val, float) and val != int(val) else 0)
            except Exception:
                val = str(val)
            rows.append((label, val))
    caps = dem.get("import_cap_kw") or []
    if caps:
        rows.append((_t("import cap kW"), " ".join(_fmt(float(c), 0) for c in caps)))
    for key in sorted((dem.get("prices") or {})):
        rows.append((key, str(dem["prices"][key])))
    for table, prefix in (("hp_cop", "COP"), ("hp_kw", "kW")):
        for point, val in sorted((dem.get(table) or {}).items()):
            rows.append((f"{prefix} {point}", str(val)))
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
    month_keys = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "All"]
    months = [_t(m) for m in month_keys]

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
            ax.set_title(_t("Compare — what differs") + "\n" + _t("A and B are named under the table"), fontsize=13, loc="left")
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
            fig.text(0.04, 0.02, _t("Same on both") + ": " + ", ".join(same[:18]) + (" …" if len(same) > 18 else ""), fontsize=7, color="#444")
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
            def normalize(t):
                s = (t or "").strip().lower()
                for de, en in (("monat", "month"), ("tag", "day"), ("durchschnitt", "average")):
                    s = s.replace(de, en)
                return s
            def keyed(rows):
                section = ""
                out = {}
                for rec in rows:
                    if not rec:
                        continue
                    label = normalize(rec.get("title") or "")
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
                if not rec:
                    continue
                raw_title = (rec.get("title") or "").replace("€", "").strip()
                name = _t(raw_title)
                norm = normalize(raw_title)
                if rec.get("head"):
                    section = norm
                    body.append([name] + [""] * 13)
                    marks.append("head")
                    continue
                other = by_b.get((section, norm)) or {}
                line1, line2, line3 = [name + "   A"], [name + "   B"], ["B − A"]
                better_cells = [None]
                for col in month_keys:
                    va, vb = rec.get(col), other.get(col)
                    if va is None or vb is None:
                        line1.append("—"); line2.append("—"); line3.append("—")
                        better_cells.append(None)
                        continue
                    try:
                        diff = float(vb) - float(va)
                    except Exception:
                        line1.append("—"); line2.append("—"); line3.append("—")
                        better_cells.append(None)
                        continue
                    line1.append(_fmt(float(va), 1 if rec.get("fmt") in {"eur", "day", "c"} or "€" in (rec.get("title") or "") else 0))
                    line2.append(_fmt(float(vb), 1 if rec.get("fmt") in {"eur", "day", "c"} or "€" in (rec.get("title") or "") else 0))
                    line3.append(("+" if diff >= 0 else "") + _fmt(diff, 1))
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
                ax.set_title(_t("Compare") + " " + _t(title) + "\n" + _t("Light green: B is better.  Light red: B is worse.  Difference = B − A."), fontsize=11, loc="left")
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
                        cell.set_height(0.045)
                        continue
                    mark = part_marks[r - 1]
                    if mark == "head":
                        cell.set_facecolor("#1d3557")
                        cell.set_text_props(color="white", weight="bold", fontsize=7)
                        cell.set_height(0.045)
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
    if log:
        log(f"Wrote {out}")
    return out


