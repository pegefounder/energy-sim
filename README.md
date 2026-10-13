# PEGE energy sim

Simulation with PVGIS data for photovoltaic. BESS battery, export and import of
electricity by spot market prices, EMS to avoid high peak-usage grid fees,
heating, cooling and DHW by heat pump, charging cars, and off-grid and
weak-grid systems with a generator and power to methanol. All for the house
improvement up to the energy-intensive industry.

https://github.com/pegefounder/energy-sim
https://www.pege.org/energy-sim/

First public page: https://www.pege.org/energy-sim/

Currency is in your units. The PDF draws no currency sign.

## Description

Simulation with PVGIS data for photovoltaic. BESS battery, export and import of
electricity by spot market prices, EMS to avoid high peak-usage grid fees,
heating, cooling and DHW by heat pump, charging cars, and off-grid and
weak-grid systems with a generator and power to methanol. All for the house
improvement up to the energy-intensive industry.

## 2026-10-10 Version 0.0

Only for objects with PV, a battery, a heat pump, and car charging.
Interaction with the outside world by a spot market prices model and limits
to import and export electricity.

## Targets for Version 1.0

Define multiple objects and their quantity. This will be called a community
simulation. For example:

- 5000 GEMINI next Generation houses
- A fast charging station
- A power to methanol plant with a tank and a CCPP — Combined Cycle Power Plant
- Agricultural areas with peak demand for electric tractors
- An only electric-powered cement factory

We will implement in these simulations different industries.

## Files

- `pege_energy_sim_server_uses_default_browser_as_gui.py` — form, default browser as GUI
- `pege_energy_sim_report.py` — PVGIS download and PDF
- `pege_energy_sim_demand.py` — house, heat pump, prices
- `pege_energy_sim_demand_sim.py` — hourly battery and grid run
- `pege_energy_sim_gui.py` — desktop window, no browser

## Start

```bash
pip install matplotlib numpy pandas requests
python pege_energy_sim_server_uses_default_browser_as_gui.py
```

The default browser is the GUI. Stop the server with Ctrl+C.
Default address: http://127.0.0.1:8765/

## Folder layout

Rename the old `PVGIS` project folder to `PEGE-energy-sim`.
Under the output parent folder:

- `locations/` — weather CSV cache, one folder per place, and `.pvgis_country_cache.json`
- `objects/<object>/` — object JSON and the PDF/JSON of each run
- `compares/` — compare PDFs

Move existing location folders into `locations/`. A folder still sitting
in the parent is used, not copied.

## What a run does

1. Downloads missing PVGIS hourly series (cached per orientation).
2. Builds the yield PDF.
3. Runs the house: battery, heat pump, DHW, cars, grid.
4. Sells only when the export price is above 0. At 0, only PV yield leaves, not the battery. A negative price is not sold.
5. Writes the PDF and a JSON sidecar next to it.

## Compare

Pick two stored simulations. Each PDF/JSON pair is one entry, without a file ending.
The compare PDF lists what differs, then each statistics chapter.
Light green: run 1 is better. Light red: run 1 is worse.
More export and a higher balance are better. More import, peak fee, house use, waste and cycles are worse.

## License

GNU General Public License v3.0. See LICENSE.
Copyright (c) 2026 Roland Mösl founder@pege.org

A published improvement of this program must stay under GPL-3.0.
A private simulation on your own machine does not have to be published.
