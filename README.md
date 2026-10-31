# Sailboat Ground Station Monitor

A desktop app for monitoring the serial output of the STM32 joystick controller used
in the autonomous sailboat project. It shows live sail/rudder gauges, two top-down boat
schematics (your **commanded** angles vs. the boat's **actual** reported angles), the
full XBee telemetry panel, and a raw serial log — with automatic COM-port detection and
ST-Link auto-connect.

> **Platform:** Windows. The app talks to the controller over its ST-Link Virtual COM
> port.

---

## What you need

1. **Python** (installed correctly — see below).
2. Two Python packages: **`customtkinter`** and **`pyserial`** (listed in `requirements.txt`, installed in one step below).
3. This project's files (`sailboat_monitor.py`).

If you already have Python on your PATH, skip to the [Quick start](#quick-start-if-you-already-have-python).

---

## Step 1 — Install Python (do this carefully)

This is the step people get wrong. Follow it exactly.

1. Open this page in your browser:
   **<https://www.python.org/downloads/windows/>**

   > Don't use the Microsoft Store version, and don't just type `python` into the
   > terminal (that opens the Store). Use the official installer from the page above.

2. Under **"Stable Releases"**, find the newest version at the top, then click
   **"Windows installer (64-bit)"**. This downloads a file named something like
   `python-3.x.x-amd64.exe`.

3. **Double-click the downloaded `.exe`** to start the installer.

4. **⚠️ MOST IMPORTANT STEP:** On the very first screen, at the **bottom**, tick the
   checkbox:

   > ☑ **Add python.exe to PATH**
   > *(older installers call this "Add Python 3.x to PATH")*

   If you miss this, Windows won't know where Python is and nothing below will work.

5. Now click the big **"Install Now"** button and wait for it to finish.

6. (Optional but recommended) If, at the end, you see a button called
   **"Disable path length limit"**, click it, then close the installer.

### Check that it worked

1. Press the **Windows key**, type `cmd`, and open **Command Prompt**.
   *(Open a **new** window — one that was already open won't know about the new PATH.)*
2. Type this and press Enter:

   ```
   python --version
   ```

   You should see something like `Python 3.x.x`. 

3. Type this and press Enter:

   ```
   pip --version
   ```

   You should see a `pip 2x.x ...` line. 

If both commands print a version, Python is installed correctly. If you get
*"'python' is not recognized..."*, PATH wasn't ticked — see [Troubleshooting](#troubleshooting).

---

## Step 2 — Get the project files

**Option A (easiest):**
1. On this repo's GitHub page, click the green **`< > Code`** button.
2. Choose **"Download ZIP"**.
3. Right-click the downloaded ZIP → **Extract All...** → pick a folder you'll remember
   (e.g. `C:\Sailboat`).

**Option B (if you use Git):**
```
git clone <this-repo-url>
```

Either way, you should end up with a folder containing all the project files
(see below). Keep them in the same folder — they import each other.

### Project files

The code is split into modules by feature:

| File | What it does |
|------|--------------|
| `sailboat_monitor.py` | Main app and window layout. Run this one (or `windbound_web.py`, below). |
| `connection.py` | Serial connect/disconnect, port scanning, queue pump, telemetry dispatch. |
| `map_view.py` | 3D view + GPS map window, tile server management, offline tile downloads. |
| `route.py` | The waypoint list (`WaypointStore`), draft and safe-depth limits, depth along each leg. Shared by both UIs. |
| `lake_depth.py` | Lake-inventory depth overlays and their depth grids. Shared by both UIs. |
| `tile_cache.py` | The SQLite map-tile cache in `map_downloads/`. Shared by both UIs. |
| `parsing.py` | Reads the controller and telemetry serial lines, formats values. |
| `serial_io.py` | Detects ST-Link ports, reads the port in a background thread. |
| `widgets.py` | 2D gauges, top-down boat view, wind rose, trend plot. |
| `boat3d.py` | 3D boat view (matplotlib). |
| `recording.py` | Saves telemetry to CSV and exports the GPS track as GPX. |
| `waypoints.py` | Waypoint list shared by the map and the side panel. |
| `theme.py` | Light/dark color helpers shared by the widgets. |
| `windbound_web.py` | Starts the web UI (the second option). |
| `station.py` | The web UI's ground-station core: serial link, telemetry, recording, sessions, route. |
| `web_server.py` | The web UI's local server on 127.0.0.1. |
| `simulator.py` | Finds a running boat simulator, or starts one from windbound-firmware, for the web UI's simulator tabs. |
| `web/` | The web UI's page, scripts, styles and fonts. |

---

## Step 3 — Install the required packages

1. Open **Command Prompt** again.
2. Go to the folder where you put the files (the one that contains `requirements.txt`).
   For example:

   ```
   cd C:\Sailboat
   ```

3. Install everything the app needs with one command:

   ```
   pip install -r requirements.txt
   ```

   *(If `pip` isn't recognized, use `python -m pip install -r requirements.txt` instead.)*

Wait for it to say it installed successfully. You only have to do this once.

---

## Step 4 — Run the app

You should still be in the project folder from Step 3. (If you closed the window, open
Command Prompt and `cd` back into it, e.g. `cd C:\Sailboat`.) Then start the app:

```
python sailboat_monitor.py
```

The window should open. (You *can* also just double-click `sailboat_monitor.py`, but
running it from Command Prompt the first time is better — if anything goes wrong, you'll
see the error message instead of a window that silently never appears.)

---

## Using it

1. **Plug in the controller** (the STM32 board, via its ST-Link USB). With
   **"Auto-connect..."** ticked, the app grabs the ST-Link COM port automatically. Or
   pick it from the **COM Port** dropdown and click **Connect**.
2. Make sure **Baud** matches your debug UART (default is **115200**). If the log shows
   garbled characters, the baud is wrong.
3. You'll see:
   - **Sail / Rudder gauges** — your joystick commands.
   - **Commanded** boat tile — where the sail *should* be (modeled).
   - **Actual** boat tile — where the boat *reports* the sail is (from telemetry).
   - **Sailboat Telemetry** panel — the full XBee data.
   - **Raw Serial Log** — everything coming over the port.
4. Use the **Dark Mode** switch top-right to toggle light/dark.

---

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `'python' is not recognized...` | The "Add python.exe to PATH" box wasn't ticked. Re-run the installer, choose **Modify**, and make sure PATH is added — or reinstall and tick the box. Then open a **new** Command Prompt. |
| `'pip' is not recognized...` | Use `python -m pip install customtkinter pyserial` instead. |
| `ModuleNotFoundError: No module named 'customtkinter'` (or `serial`) | You skipped Step 3. From the project folder, run `pip install -r requirements.txt`. |
| Window never opens / closes instantly | Run it from Command Prompt (Step 4) to see the actual error. |
| No COM ports in the dropdown | Check the USB cable, that the ST-Link drivers are installed, and click **Refresh**. |
| Log shows garbled text | Wrong **Baud** rate — set it to match your controller's debug UART. |
| Typing `python` opens the Microsoft Store | You installed the Store stub instead of the real Python. Install from <https://www.python.org/downloads/windows/> as in Step 1. |

---

## The web UI (a second way to run it)

`windbound_web.py` runs the same ground station with a different interface: a
Vercel-style screen in your browser, with Live, Route, Logs, Sessions and
Settings tabs, a command menu (**Ctrl+K**, **⌘K** on a Mac) and a light and a
dark theme. It needs nothing beyond `requirements.txt`. From the project folder:

```
python windbound_web.py
```

It opens as its own window when Microsoft Edge or Google Chrome is installed,
otherwise in your default browser, at <http://127.0.0.1:8765>. Press **Ctrl+C**
in the Command Prompt to stop it; that closes the port and finishes any
recording. Run one UI at a time: both want the same COM port.

| Option | What it does |
| --- | --- |
| `--connect COM5` | Open this port at start. Also takes a pyserial URL such as `socket://127.0.0.1:5760`. |
| `--baud 115200` | Baud for `--connect`. |
| `--port 8765` | First web port to try; the next free one is used if it's busy. |
| `--simulator [DIR]` | Also run the boat simulator and connect to it (see below). `DIR` is a windbound-firmware checkout; by default the one next to this folder, or `WINDBOUND_FIRMWARE_DIR`. |
| `--browser` | Open a normal browser tab instead of an app window. |
| `--no-open` | Don't open anything; just print the address. |

What's on each screen:

- **Live**: the boat drawn from your sticks (dashed) and from the last packet
  (solid), the gap between them, heading, wind, the link's health with every
  packet of the last minute, a two-minute trend and the route at a glance.
- **Route**: the map with the lake's depth contours and the route coloured by
  depth (blue 2.5 m or deeper, yellow 2.0-2.5 m, red under the 2.0 m draft).
  Right-click to add a waypoint or ask the depth, drag markers to move them,
  drag rows to reorder, then **Send to boat**. As in the desktop app, sending
  logs the payload as a TX line; the radio link isn't wired yet.
- **Logs**: every line from the controller, newest first. Filter by type or
  text; click an XBee line to read its fields.
- **Sessions**: the telemetry CSVs in `logs/`, with GPX export for each.
- **Settings**: baud, auto-connect, map style and theme, and the values the
  code sets.
- **Mission control** and **Firmware checks**, while the boat simulator runs:
  the simulator's own screens (the boat in 3D, run controls, wind and faults,
  which link the firmware obeys, and every firmware test), shown here.

Differences from the desktop app:

- The commanded sail starts at the angle the boat reports, instead of at 0°.
  **Line up with the boat** on Live re-aligns it if the two drift apart.
- Positions that arrive as whole degrees (today's firmware sends `clat` and
  `clon` with `%d`) are shown as truncated and kept off the map, instead of
  being drawn tens of kilometres away.
- There's no 3D view of the real boat (the simulator's Mission control has one).
- The base map is OpenStreetMap, muted to the theme. CartoDB's free tiles now
  return an "API key required" image.

**With the boat simulator.** The firmware-in-the-loop simulator in
`windbound-firmware/sim` runs the real firmware against a simulated boat and
speaks the controller's serial protocol on `socket://127.0.0.1:5760`. With
windbound-firmware checked out next to this folder, one command runs both:

```
python windbound_web.py --simulator
```

It starts the simulator (the first start sets up its Python environment and
builds the firmware, a minute or two), connects to it when it's ready, and
stops it when you stop this. Its **Mission control** and **Firmware checks**
then appear as tabs here: they are the simulator's own screens, so they
always match the simulator you're running. If a simulator is already
running, it's used instead of starting another.

You can also start the simulator yourself (`python3 sim/start.py` in
windbound-firmware) and pick **Simulator** in the connection menu; the two
tabs appear once it's connected. The simulator serves its own page too, on
port 8765, so this UI moves to the next free port and prints it.

---

## Quick start (if you already have Python)

From the project folder:

```
pip install -r requirements.txt
python sailboat_monitor.py
```

---

## Notes for tuning

A couple of constants in `station.py` (shared by both UIs) you may want to adjust:

- **`SAIL_ROTATION_RATE_DPS`** — slew rate (deg/s) the *commanded* sail is animated at.
- **`SAIL_CMD_DEADBAND`** — joystick deadband below which the commanded sail holds still.

---

## Bathymetry and waypoint route safety

The visual Nova Scotia Lake Inventory overlay and the numeric depth source are now
separate. The GUI does **not** inspect contour lines or try to infer depths from the
overlay image. Numeric queries come only from a precomputed static file:

```text
lake_depth_maps/<Lake Name>/depth_grid.npz
```

Lake Micmac and Lake Charles are supplied with 1 m grid-spacing depth maps prepared
from the corresponding inventory PDFs. The 1 m value is the **cell spacing**, not the
measurement accuracy; the depth values remain approximate historical-map estimates.

- Enable a prepared lake with **Lake depth** in the 3D/GPS map window.
- Right-click and choose **Approx. water depth here (±)** to query the static grid.
- Waypoint legs are sampled at about 1 m intervals and only the local route sections
  change colour:
  - blue: estimated depth >= 2.5 m
  - yellow: 2.0 m <= estimated depth < 2.5 m
  - red: estimated depth < 2.0 m boat draft
  - grey: no grid value exists at that position
- `BOAT_DRAFT_M = 2.0` and `SAFE_DEPTH_M = 2.5` are defined in `route.py`.

Each custom grid contains its own geographic bounds, spacing and nominal uncertainty.
`prepare_lake_depth.py` now prepares only the visual overlay; it does not perform OCR,
contour detection or depth interpolation.

