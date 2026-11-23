# Gordian Knot

A Windows tray app that makes you earn your free time.

Time spent on work tools or completing admin (IDEs, game engines, allowlisted sites) earns minutes.
Other applications spent that time. When the balance hits zero, reward apps
are warned and then closed until you have worked the overspend back.

By default **10 minutes of work earns 10 minutes of free time** (1 : 1).

**Windows 10 / 11 only.** This app uses Windows APIs for the foreground window,
browser address bar, tray icon, and closing programs. It will not currently run on macOS
or Linux.

## Quick start

1. Install [Python 3.12](https://www.python.org/downloads/) and tick
   **Add python.exe to PATH** during setup.
2. Download or clone this project into a folder you will keep
   (do not delete the folder after installing).
3. Double-click **`GordianKnot.bat`**.

The first launch creates a local `.venv`, installs `pystray` and `Pillow`,
creates `config.json` if needed, and opens the app. It also adds a
**Gordian Knot** shortcut to the Start menu, so after that you can launch it
from Windows search.

Only one copy runs at a time. Closing the window or pressing Escape hides it
to the tray. Use **Quit** on the tray menu to exit for real.

### From git

```bat
git clone https://github.com/PatrickvanWorkum/Gordian-Knot.git
cd Gordian-Knot
GordianKnot.bat
```

### From a ZIP

Download the source ZIP from GitHub, extract it somewhere permanent, then
double-click `GordianKnot.bat` inside the folder.

## How time is counted

About once a second the app looks at the window in front of you and sorts it
in this order:

1. **Work process** (Cursor, VS Code, Visual Studio, Godot, Unity, Blender,
   Rider, Aseprite, Unreal, and anything you add): earns.
2. **Work title keyword**: earns. This is how you mark a browser page as work.
3. **Reward process** (Steam by default): spends.
4. **Reward title keyword** (YouTube by default): spends.
5. **Game**: spends. A game is a window that covers the whole screen, or an exe
   under a known install folder (Steam, Epic, GOG, and others).
6. Anything else is neutral and does not move the balance.

Work only earns while you have used the keyboard or mouse in the last 60
seconds. Reward time spends even when you are idle. **Pause** holds the
balance still until you press Resume.

A change to the ratio only affects time earned from then on. It never
recalculates what you already earned. Free time **carries past midnight**;
today's work total resets at midnight. Reset time clears free time and leaves
a light blue mark on the chart.

### Pause while working

If Pause is on and you spend a minute on a work program, the app asks whether
to end the pause and count that minute (including the free time it earns).
Switching between work tools keeps that minute; an unproductive window clears
it and brings Pause back.

## When free time runs out

A reward window gets:

1. a warning straight away,
2. a second warning after 1 minute,
3. closed after 5 minutes of overspend.

The warning floats over the window without stealing focus. The overspent
balance keeps counting while it is open. A browser loses only the front tab
(Ctrl+W). Other programs get Alt+F4, then a forced close if they are still
running. That program stays blocked until the overspend has been earned back.

Gordian itself, Explorer, and Windows system processes are never closed.

The delays live in `gordian/warning.py` as `SECOND_WARNING` and `CLOSE_AFTER`.

## Features

### Main window

Shows the current foreground window, whether it is earning or spending, the
balance, Pause / Resume, and the work:reward ratio (W:R). The application logo
opens a menu for About, Reset time, Clear history, Change password, Unlock,
Warning sound, and Colors.

### Apps

Total time per program as bars. Each browser page gets its own bar.

### Activity

A live feed of each stretch of time (work, reward, paused, neutral).

### Chart

Today's cumulative work, reward, paused, and not-counting lines.

- Green = productive, purple = reward, gold = paused, grey = not counting
- Grey bands = sleep; dark red = shutdown; pink = clock changed while running
- Scroll to zoom, drag to pan, double-click or Fit to show the whole day
- Narrow the view to a date range (for example `6 Aug` to `17 Aug`)
- Click a legend name to hide that line; **Hide legend** puts the names away
- **Reload** rereads saved days from disk

### Map

A second plot beside the time series (treemap style). Work is green,
unproductive time is red. The blocks start equal; work grows with the W:R.
When free time is below zero, the red block grows exponentially with
overspend. Programs fill each block in squares. YouTube videos share one
square labeled **YT**.

### Schedule

A week grid. Drag down a day to create a block, then set a name, W:R, and
which days it covers. A block can:

- set a **different ratio** for those hours,
- turn on **Locked** (minimize reward apps),
- turn on **Lockdown** (close every unproductive program for those hours and
  refuse new ones in Scan).

### Scan

Finds installed programs and sites in your browser history (Chrome, Edge,
Brave, Vivaldi, Opera, Firefox) so you can sort each one into Work or Spend,
then lock the choice in with the password. Scan may take a while and looks
across fixed and removable drives.

### Ask

When an unknown program or website appears, a small corner window asks whether
it counts as work. Typing an address does not ask; the question waits until
the page has loaded. Wikipedia and other reference sites count as work on
their own. YouTube stays unproductive, but a tutorial video can be marked as
work without changing the rest of YouTube.

### Milestones

Tasks that add bonus free time when you mark them done (password protected).

### Tray

Left-click or use the tray menu for Show, About, Pause, Edit lists, Reset,
Clear history, password, Unlock, Open data folder, and Quit.

### Themes and sound

- **Night mode** for a dark UI
- **Warning sound**: Windows beep by default, or pick a `.wav` from the logo menu
- **Colors** (logo menu) or debug mode (**Shift+`**): click a widget to change
  its color or font

### Unlock

Unlock asks for the password once. Reset time, the schedule, lists, Scan, and
milestones then skip it until you press Lock. Closing the app locks it again.
The password is a speed bump for your own impulses, not security.

## The password

Reset time, ratio changes, schedule edits, Scan lock-in, completing a
milestone, and related edits ask for a password. The default is `0000`. It is
stored in plain text in `config.json` on purpose.

**Reset time** clears free time (and can clear every saved day when you choose).
**Clear history** clears saved days on its own. Lists, the schedule, and the
password stay.

## Privacy

Everything stays on your PC. There is no account and no telemetry.

| What | Why |
| --- | --- |
| Foreground process, title, and idle state | Classify work vs reward |
| Browser address bar (UI Automation) | Label sites and match keywords |
| Browser history databases (during Scan only) | Suggest sites to sort |
| Favicon downloads for the current site | Show the site logo in the header |
| Local files under `%APPDATA%\GordianKnot\` | Balance, history, charts, errors |

The app **can close** reward programs and browser tabs when free time runs out
or Lockdown is on. Keep that in mind before you leave it running overnight.

## Where things live

| File | What it is |
| --- | --- |
| `config.json` (project folder) | Your lists, password, ratio, schedule, milestones, colors, fonts. Created on first launch and not tracked by git. See `config.example.json` for defaults. Use **Edit lists** in the app when you can. |
| `%APPDATA%\GordianKnot\state.json` | Today's totals, plus `state.json.bak` as the last good copy |
| `%APPDATA%\GordianKnot\history.jsonl` | One summary line per finished day |
| `%APPDATA%\GordianKnot\days\` | Each finished day's segments |
| `%APPDATA%\GordianKnot\favicons\` | Cached site icons |
| `%APPDATA%\GordianKnot\error.log` | Crashes and errors |

Task Manager shows **Gordian Knot** (not Python) because the launcher stamps a
branded `GordianKnot.exe` beside the venv on first run.

## Requirements

- Windows 10 or 11
- Python 3.12 with "Add python.exe to PATH" ticked during install
- Packages: `pystray`, `Pillow` (installed automatically by `GordianKnot.bat`)

The window is plain tkinter. Charts are drawn on a tk Canvas.

## Troubleshooting

| Problem | What to try |
| --- | --- |
| "Could not create a Python environment" | Reinstall Python 3.12 and tick Add to PATH, then run `GordianKnot.bat` again |
| Packages fail to install | Check internet access; open a terminal in the project folder and run `.\.venv\Scripts\python.exe -m pip install -r requirements.txt` |
| Double-click does nothing visible | Look for the tray icon; only one instance runs |
| Antivirus or SmartScreen warns on `GordianKnot.exe` | Expected for an unsigned stamped copy of pythonw; keep the project folder you trust |
| Want a clean slate | Quit, delete `%APPDATA%\GordianKnot\`, optionally delete `config.json`, then launch again |

## Code map

| Module | Job |
| --- | --- |
| `ui.py` | The window and the once-a-second tick |
| `monitor.py` | Reading the foreground window, and closing a window |
| `classify.py` | Deciding whether a window earns or spends |
| `ledger.py` | The day's totals and saving them |
| `warning.py` | The out-of-time warning stages |
| `schedule.py`, `weekgrid.py` | Week schedule logic and grid |
| `scan.py` | Finding programs and sites |
| `chart.py`, `treemap.py` | The lines, bars, and Map |
| `ask.py` | Corner prompt for unknown apps and sites |
| `settings.py` | Reading and writing `config.json` |
| `paths.py` | File locations and the checked save |
| `branding.py`, `startmenu.py` | Branded exe and Start menu shortcut |
| `sound.py`, `favicon.py` | Warning sound and site icons |

## Tests

```bat
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## License

MIT. See [LICENSE](LICENSE).

Version `0.1.0`.
