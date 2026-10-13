# PEGE energy sim

https://github.com/pegefounder/energy-sim
https://www.pege.org/energy-sim/

## Description

Simulation with PVGIS data for photovoltaic. BESS battery, export and import of
electricity by spot market prices, EMS to avoid high peak-usage grid fees,
heating, cooling and DHW by heat pump, charging cars, and off-grid and
weak-grid systems with a generator and power to methanol. All for the house
improvement up to the energy-intensive industry.

## Code and examples

This repository holds the program only.

Examples — locations, objects and compares — are too large for GitHub.
They are at https://pege.org/energy-sim/ for download.

On start, the program reads this file and tells you if a newer version is listed here.

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

See Linux, Apple, Windows or Android below.

The default browser is the GUI. Stop the server with Ctrl+C.
Default address: http://127.0.0.1:8765/

## Folder layout

Create a folder `PEGE-energy-sim` or whatever You like.
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
Light green: B is better. Light red: B is worse. Difference is B − A.
More export and a higher balance are better. More import, peak fee, house use, waste and cycles are worse.

## License

GNU General Public License v3.0. See LICENSE.
Copyright (c) 2026 Roland Mösl founder@pege.org

A published improvement of this program must stay under GPL-3.0.
A private simulation on your own machine does not have to be published.

Support: https://www.paypal.biz/pegefounder

## Linux

Install the archive tool and Python once:

```
sudo apt install p7zip-full python3 python3-pip
7z x pege-energy-sim.7z
cd PEGE-energy-sim
pip3 install numpy pandas matplotlib requests
```

Make the server file executable: right-click it, Properties, Permissions, Allow executing file as program. Then right-click again and choose Run as a program.

Or from a terminal in the extracted folder:

```
python3 pege_energy_sim_server_uses_default_browser_as_gui.py
```

## Apple

7z is not built in. Install The Unarchiver from the App Store, or in Terminal:

```
brew install p7zip
7z x pege-energy-sim.7z
```

Install Python 3.11 or newer from https://www.python.org/ if `python3` is missing. Then:

```
cd PEGE-energy-sim
pip3 install numpy pandas matplotlib requests
python3 pege_energy_sim_server_uses_default_browser_as_gui.py
```

## Windows

7z is not built in. Install 7-Zip from https://www.7-zip.org/ then right-click the archive and choose 7-Zip, Extract here.

Install Python 3.11 or newer from https://www.python.org/ and enable "Add python.exe to PATH". Then in a command window in the extracted folder:

```
py -m pip install numpy pandas matplotlib requests
py pege_energy_sim_server_uses_default_browser_as_gui.py
```

Or right-click the server file, Open with, Python.

## Android

Yes, on a tablet, through Termux.

Install Termux from F-Droid. In Termux:

```
pkg update
pkg install python
pip install numpy pandas matplotlib requests
```

Copy the extracted folder onto the tablet, `cd` into it, then:

```
python pege_energy_sim_server_uses_default_browser_as_gui.py
```

Open the tablet browser at the address the terminal prints, usually http://127.0.0.1:8765/

A home-screen icon is possible with Termux:Widget, also from F-Droid.

In the extracted folder make a file `start.sh`:

```
#!/data/data/com.termux/files/usr/bin/bash
cd "$(dirname "$0")"
python pege_energy_sim_server_uses_default_browser_as_gui.py
```

Then `chmod +x start.sh`. Add a Termux:Widget shortcut on the home screen and point it at that file. One tap starts the server. Open the browser at the printed address.
