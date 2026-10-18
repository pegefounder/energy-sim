#!/usr/bin/env python3
"""
Household + car + heat-pump demand model — parameters and physics.
Copyright (c) 2026 Roland Mösl founder@pege.org
Licensed under the GNU General Public License v3.0. See LICENSE.

Time base for all clock windows: winter time at the site (CET = UTC+1),
all year. Austria grid-fee off-peak Apr–Sep 10:00–16:00 summer time
= 09:00–15:00 winter time.

https://github.com/pegefounder/energy-sim
https://www.pege.org/energy-sim/
"""

from __future__ import annotations

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


def price_band(hour_wt: int) -> str:
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

SRC_NODES = (-10.0, 0.0, 10.0)
FLOW_NODES = (35.0, 50.0)


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
