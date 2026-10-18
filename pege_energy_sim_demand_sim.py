#!/usr/bin/env python3

"""
PEGE energy sim — hourly demand, battery and grid. Open source.
Copyright (c) 2026 Roland Mösl founder@pege.org
Licensed under the GNU General Public License v3.0. See LICENSE.


https://github.com/pegefounder/energy-sim
https://www.pege.org/energy-sim/
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

from pege_energy_sim_demand import (
    MONTHS,
    building_load_w,
    clamp_tank_c,
    dhw_day,
    flow_temp,
    hp_cooling,
    hp_heating,
    pipe_temp_for_month,
    price_cent,
    source_temp,
)

MONTH_STARTS = [1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335]


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


def _price_band(h) -> str:
    """Summer price bands, clock in winter time. High hours are sold first."""
    h = int(h)
    if 6 <= h < 9 or 17 <= h < 20:
        return "high"
    if 9 <= h < 15:
        return "low"
    return "medium"


def _doy(ts) -> int:
    """1–366 axis. Leap day keeps its own number; later days shift by one."""
    return int(ts.timetuple().tm_yday)


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


# Old model, kept if the 15:00 target-SOC rule fails.
# Export was first blocked 09:00-15:00. That checkbox is obsolete.
# Then 24 prices: summer/winter, workday/weekend, import/export, high/medium/low.
# Sell only above 0 cent, dearest hour first. At 0 cent only PV leaves.
# Negative price is not sold. Emergency is import above the month kW cap.
# The retired import rule charged on a dark 72 h forecast, low price then medium.

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
    export_0915 = bool(demand.get("export_0915"))
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
                    return (0 if _price_band(hwt[k]) == "low" else 1, k)
                slots = [k for k in range(i, j) if _price_band(hwt[k]) != "high"]
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
            imp_slots = [k for k in range(i, h24) if _price_band(int(hwt[k])) != "high"]
            imp_slots.sort(key=lambda k: (0 if _price_band(int(hwt[k])) == "low" else 1, int(hwt[k]) >= 20, k))
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
        band_imp = _price_band(hwt[i])
        preferred = band_imp != "high"
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
                if _price_band(int(hwt[k])) == "low":
                    low_left += 1
            want = float(plan_imp[i])
            if exportable < 8.0:
                # Keep a reserve so the next hour is not served from an empty battery.
                target = min(soc_max, soc_min + min(need, 40.0))
                want = max(want, max(0.0, target - soc))
                if band_imp == "medium":
                    want = max(0.0, want - low_left * cap)
            headroom = max(0.0, cap - grid_imp)
            room = max(0.0, soc_max - soc)
            take = min(want, headroom, room / max(eta_batt_ac, 0.2), max(0.0, c_kw - charge))
            if take > 1e-9:
                soc += take * eta_batt_ac
                grid_imp += take
                charge += take * eta_batt_ac
        if px > 0:
            band_now = _price_band(hwt[i])
            # Low and medium hours store PV if the battery has room.
            # High hours, and a full battery, export up to the export limit.
            room_store = max(0.0, soc_max - soc)
            if band_now == "high" or room_store < 0.05:
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

        band = _price_band(hwt[i])
        recs.append({
            "time": times.iloc[i],
            "year": years[i],
            "month": months[i],
            "doy": doys[i],
            "hour_wt": hwt[i],
            "pv": float(pv[i]),
            "import_kwh": grid_imp,
            "export_kwh": grid_exp,
            "export_high": grid_exp if band == "high" else 0.0,
            "export_med": grid_exp if band == "medium" else 0.0,
            "export_low": grid_exp if band == "low" else 0.0,
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
    kinds = (("max", "Maximum", "#d0d0d0"), ("avg", "average", "#ececec"), ("min", "minimum", "#ffffff"))
    rows = []
    for key, label in zip(keys, labels):
        rows.append({"title": label, "color": "#1d3557", "head": True})
        money = "€" in label
        for kind, title, color in kinds:
            rec = {"title": f"  month {title}", "color": color, "fmt": "c" if ("C" in label or "cycle" in label.lower()) else ("eur" if money else "month")}
            for m, name in enumerate(MONTHS, 1):
                part = year_month[year_month["month"] == m][key]
                rec[name] = _mma(part)[kind]
            rec["All"] = _mma(year_month.groupby("year")[key].sum())[kind]
            rows.append(rec)
        for kind, title, color in kinds:
            rec = {"title": f"  day {title}", "color": color, "fmt": "c" if ("C" in label or "cycle" in label.lower()) else ("eur" if money else "day")}
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
        for c in cols:
            val = rec.get(c, "")
            if rec.get("head"):
                line.append("")
            elif " C " in rec["title"] or rec["title"].startswith("  day") and "C " in rec["title"]:
                line.append(f"{val:.2f}")
            elif rec.get("fmt") == "c":
                line.append(f"{val:.2f}")
            elif rec.get("fmt") == "eur":
                line.append(f"{val:.1f}")
            elif rec.get("fmt") == "month":
                line.append(f"{val:.0f}")
            else:
                line.append(f"{val:.1f}")
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
            cell.set_facecolor(rec.get("color", "#ffffff"))
            if rec.get("head"):
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
    ax.set_xlabel("Day of year", fontsize=11)
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

    from pege_energy_sim_report import scatter_page
    scatter_page(
        pdf,
        daily_h["doy"].to_numpy(),
        daily_h["exp_h"].to_numpy(),
        "Export energy as equivalent hours  (kWh / grid kW)",
        "Equivalent hours",
        face="#2E8B57",
        edge="#C8F5D4",
        y_unit_step=0.5,
        location_title=location_title,
    )
    scatter_page(
        pdf,
        daily_h["doy"].to_numpy(),
        daily_h["imp_h"].to_numpy(),
        "Import energy as equivalent hours  (kWh / grid kW)",
        "Equivalent hours",
        face="#C0392B",
        edge="#F8C9C4",
        y_unit_step=0.5,
        location_title=location_title,
    )

    fig, ax = plt.subplots(figsize=(11.69, 8.27))
    hit = daily_h[daily_h["emerg"] > 1e-6]
    if n_emerg_hours == 0:
        ax.text(
            0.5, 0.55, "0 emergency imports",
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
        ax.set_ylabel("Highest emergency import that day  (kW)", fontsize=11)
    ax.set_title(
        f"Emergency import hours: {n_emerg_hours}    "
        f"days with an emergency: {n_emerg_days}\n{location_title}",
        fontsize=13, pad=10,
    )
    _style_year_axis(ax)
    fig.tight_layout(rect=(0.04, 0.16, 0.99, 0.96))
    fig.text(
        0.06, 0.04,
        "Emergency = this hour’s grid import is above the month’s max import kW. "
        "2 kW means up to 48 kWh in 24 hours. More than 2 kW in an hour is the higher grid fee.\n"
        "Import inside that cap is not an emergency. Preferred cheap hours are 00:00–06:00 and 09:00–15:00 winter time.",
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
        pdf, "Energy in kWh",
        "Month rows: kWh in that month.  Day rows: best / average / worst day.\n"
        "Total energy used for house = household + car + heating + cooling + DHW + direct electric.\n"
        "PV not exported and not marked wasted is still in the battery, or could not leave because the grid was already full. It is not house use.",
        _chapter_rows(daily, ym,
                      ["export_kwh", "import_kwh", "total_kwh"],
                      ["Export to grid", "Import from grid", "Total energy used for house"]),
        location_title,
    )
    rate = float((result.get("demand") or {}).get("peak_eur_kw_month") or 0.0)
    fixed = float((result.get("demand") or {}).get("fixed_ct") or 0.0)
    daily, ym = _money_columns(hourly, daily, ym, result.get("demand") or {})
    money_keys = ["export_eur", "import_eur", "peak_fee_eur", "import_incl_eur", "balance_eur"]
    money_labels = ["Export", "Import", "Peak grid fee", "Import incl. peak fee", "Balance"]
    if fixed > 0:
        money_keys.append("fixed_eur")
        money_labels.append("No PV, no battery, fixed tariff")
    _draw_stat_page(
        pdf, "Energy by price",
        "Currency in Your units. Import and export use the cent/kWh of that hour. "
        f"Peak fee {rate:g} / kW / month is the highest import hour of the month "
        "(hourly stand-in for the 15-minute peak) and is added to that month’s import. "
        "Day rows have no peak fee. Balance = export − import incl. peak fee.\n"
        "Sell only when export is above 0. At 0 cent, export PV yield only, not the battery. "
        "Do not sell when the earning is negative."
        + (f"  Fixed tariff {fixed:g} cent/kWh is the bill with no PV and no battery, plus the same peak fee on the hourly house load." if fixed else ""),
        _chapter_rows(daily, ym, money_keys, money_labels),
        location_title,
    )
    _draw_stat_page(
        pdf, "Export in kWh during price hours",
        "kWh exported while that price band was active. Not the money. "
        "Winter time. High 06:00–09:00 and 17:00–20:00, sold first. "
        "Low 09:00–15:00, sold last. Medium is the rest.",
        _chapter_rows(daily, ym,
                      ["export_kwh", "export_high", "export_med", "export_low"],
                      ["Export total", "Export during high price hours", "Export during medium price hours", "Export during low price hours"]),
        location_title,
    )
    _draw_stat_page(
        pdf, "Heat pump",
        "Heating, cooling and DHW are electricity.  "
        "Direct electric is the part the heat pump could not cover.",
        _chapter_rows(daily, ym,
                      ["heat_kwh", "cool_kwh", "dhw_kwh", "direct_kwh"],
                      ["Heating", "Cooling", "DHW", "Direct electric"]),
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
    batt_rows = _chapter_rows(daily, ym, ["wasted_kwh", "cycles"], ["Wasted", "Full cycles"])
    batt_rows += _chapter_rows(peaks, ym_c, ["charge_c", "discharge_c"], ["Charge C", "Discharge C"])
    _draw_stat_page(
        pdf, "Battery",
        f"Wasted = PV left when the export price is negative and the battery is full.  "
        f"Full cycles = discharge kWh / {batt:g} kWh.  "
        "Battery export only into hours whose export price is above 0, highest price first. "
        "At 0 cent only PV yield leaves, not the battery. Negative price is not sold.",
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
        f"Battery cycles per day  (discharge / {batt:g} kWh)",
        "Full cycles",
        face="#1d4e89",
        edge="#c5d8f0",
        y_unit_step=0.05,
        location_title=location_title,
    )
    scatter_page(
        pdf,
        soc_day["doy"].to_numpy(),
        (soc_day["soc_max"] / denom * 100.0).to_numpy(),
        f"Battery max charge state per day  ({batt:g} kWh = 100 %)",
        "% of battery",
        face="#1a7a4c",
        edge="#c8efd4",
        y_unit_step=5.0,
        location_title=location_title,
    )
    scatter_page(
        pdf,
        soc_day["doy"].to_numpy(),
        (soc_day["soc_min"] / denom * 100.0).to_numpy(),
        f"Battery min charge state per day  ({batt:g} kWh = 100 %)",
        "% of battery",
        face="#a15c07",
        edge="#f6ddb0",
        y_unit_step=5.0,
        location_title=location_title,
    )
