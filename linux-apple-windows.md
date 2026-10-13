# PEGE energy sim — install and start

The program is a local server. The form opens in the default browser. That browser window is the GUI.

Source: https://github.com/pegefounder/energy-sim
Site: https://www.pege.org/energy-sim/

Copyright (c) 2026 Roland Mösl founder@pege.org
License: GPL-3.0

## Common

`pege-energy-sim.7z` on pege.org holds the program, example locations, objects and compares.

Extract it into a folder of your choice. The server uses the folder it is started in. Locations, objects and compares are created next to it.

Python 3.11 or newer is required, plus:

```
pip install numpy pandas matplotlib requests
```

If these packages are missing, the program does not open the form. A message names the missing packages and shows the install line. Install them, then start again.

Start file: `pege_energy_sim_server_uses_default_browser_as_gui.py`

The terminal says to look at the default browser. The form is in that window.

Stop the server with Ctrl+C. Start a new version only after the old one has stopped. If the port is busy, the old server is still running.

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
