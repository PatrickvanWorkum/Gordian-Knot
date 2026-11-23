"""Gordian Knot: work time earns free time before Steam, YouTube, and games.

The window is a normal Windows app with a tray icon. Left-click the application
logo for About, Reset time, Clear history, Change password, and Unlock.
Closing it hides to the tray. One mutex (Local\\GordianKnotMonitor) keeps a second launch from starting
another copy. The venv pythonw and the system pythonw it starts are that one copy.

Startup is ui.main. It puts a Start menu shortcut in place, reads config.json
from the project folder, and loads today's totals from
%APPDATA%\\GordianKnot\\state.json.

config.json holds the lists, the password, the default ratio, the week
schedule, milestones, colors, and fonts. The password is stored in plain text
on purpose. It guards Reset time, ratio changes, schedule edits, Scan lock-in,
and marking a milestone done.

Each second App._tick does the real work:

1. monitor.get_sample reads the foreground window: process, path, title, how
   long the keyboard and mouse have been idle, and whether the window covers
   the screen. In a browser it also reads the address bar. The website name is
   the label after https:// and www, split into words (Interactive Brokers).
   The site's logo is shown beside that name when the site has one. Edit lists
   opens a window of those groups.
2. classify.resolve turns that into a kind. A productive process, or a
   productive title keyword inside a browser, earns only while you have used
   the keyboard or mouse within idle_seconds (60). A reward process, a reward
   title keyword such as YouTube, or a game spends even if you walk away.
   Games are fullscreen windows or executables under a known install path.
   The lock screen keeps spending when the window behind it was spending, and
   stops earning when it was work. Pause is a button. It holds the balance and
   lives only in memory, so a restart clears it. A minute on work programs
   asks before ending the pause and counting that minute. Switching work
   programs keeps the minute. An unproductive program clears it and brings
   the pause back. Hide legend puts the chart names away. Map is the second
   plot beside the time series: work and unproductive time start equal, work
   grows with the W:R, and the red block is the unproductive time. A program or site the lists
   do not know yet asks, in a small corner window, whether it is work.
   Wikipedia and other reference sites are saved as work on their own.
   YouTube stays unproductive, and a tutorial video can be marked as work
   without changing the rest of YouTube.
3. The schedule block for this minute can swap in a different ratio. Locked
   minimizes a reward window. Lockdown, which you tick on a block, closes
   unproductive programs for those hours and refuses new ones in Scan.
4. ledger.update adds the seconds since the last tick. Earned free time is
   banked on that tick as delta * reward / work, so a later ratio change leaves
   old earnings where they were. Milestone bonuses sit beside that bank.
   Remaining is earned plus bonus, minus time spent. Sleep (both clocks jumped
   45s or more) and shutdown (the process was gone that long) are drawn on the
   chart and do not move the balance. A clock change while the app is ticking
   is pink: spotted for time inserted, a solid line for time removed.
5. The ledger saves about every two seconds. The write is checked before it
   replaces the file, and a .bak keeps the last good copy. A blank file is
   loaded from that backup. At midnight the day is appended to history.jsonl
   and days\\YYYY-MM-DD.json. Today's work starts at zero. Free time carries
   over. Reset time clears free time and leaves a light blue line.
6. With no free time left, a reward window is warned at once, warned again
   after 1 minute, and closed at 5 minutes. Pause, Resume, and free time
   still left cancel a close that has not finished. A browser loses the front tab
   (Ctrl+W). Anything else gets Alt+F4, then a harder close if it is still
   there. That same program stays barred until the overspend is earned back.
   The warning floats over the window, plays the Windows beep (or a wav chosen
   in debug mode), and the balance keeps counting.
"""

__version__ = "0.1.0"
