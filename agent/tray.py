"""The agent's tray icon: what it's doing, and the few things worth a click.

The agent has no window of its own — on Windows it's a scheduled task on
pythonw.exe — which is right up until you want to know whether it's still
pushing. `agent.py --tray` puts a small block next to the clock instead: green
while you're in game, grey while you're away, red when the last push failed, with
the numbers you'd otherwise open the page for in its menu.

The state, the wording and the icon are plain functions over a status dict, so
they can be tested on a machine with no desktop at all. pystray is imported inside
Tray.run(), which is the only part that needs one.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

from PIL import Image, ImageDraw

from common import log, run_hidden

PLAYING, AWAY, BROKEN = "playing", "away", "broken"
# What woke the push loop out of its wait, for agent.loop to act on.
STOP, PUSH = "stop", "now"
# One colour per state, because at 16 px that is the only thing you can read.
COLOURS = {
    PLAYING: (76, 175, 80),
    AWAY: (107, 114, 128),
    BROKEN: (209, 75, 75),
}
# The same names the page uses (site/js/util.js), so the icon and the page agree.
DIMENSIONS = {"overworld": "the Overworld", "the_nether": "the Nether", "the_end": "the End"}
# The menu has a fixed set of lines so each can be one item with a callable text;
# a line that has nothing to say is blank, and blank lines are hidden.
LINES = 5


def blank_status() -> dict:
    return {"started_at": time.time()}


# ---------------------------------------------------------------- what it says


def condition(status: dict) -> str:
    if status.get("error"):
        return BROKEN
    return PLAYING if status.get("online") else AWAY


def _clock(stamp: float | int | None) -> str:
    """A time of day. Milliseconds since the epoch or seconds, both turn up here."""
    if not stamp:
        return "?"
    seconds = stamp / 1000 if stamp > 1e11 else stamp
    return time.strftime("%H:%M", time.localtime(seconds))


def _duration(seconds: float | None) -> str:
    if not seconds or seconds < 60:
        return "under a minute"
    hours, minutes = divmod(int(seconds) // 60, 60)
    return f"{hours} h {minutes} min" if hours else f"{minutes} min"


def dimension_name(dimension: str | None) -> str:
    if not dimension:
        return ""
    short = dimension.split(":")[-1]
    return DIMENSIONS.get(short, short.replace("_", " "))


def headline(status: dict) -> str:
    if status.get("online"):
        name = status.get("player")
        if not name:
            return "In game"
        where = dimension_name(status.get("dimension"))
        return f"In game as {name}, in {where}" if where else f"In game as {name}"
    if status.get("last_seen_at"):
        return f"Away since {_clock(status['last_seen_at'])}"
    return "Waiting for Minecraft"


def summary(status: dict) -> list[str]:
    """The menu's information lines, always LINES long; blanks are hidden."""
    machine = []
    if status.get("cpu") is not None:
        machine.append(f"CPU {round(status['cpu'])}%")
    if status.get("gpu") is not None:
        machine.append(f"GPU {round(status['gpu'])}%")
    if status.get("cpu_temp") is not None:
        machine.append(f"{round(status['cpu_temp'])} °C")

    if status.get("error"):
        push = f"Push failed — {status['error']}"
    elif status.get("pushed_at"):
        push = f"Pushed at {_clock(status['pushed_at'])}"
    else:
        push = "Nothing pushed yet"

    busy = ""
    if status.get("rendering"):
        busy = "Rendering the map…"
    elif status.get("publishing"):
        busy = "Publishing frames…"

    lines = [
        headline(status),
        f"Played today — {_duration(status.get('today'))}" if status.get("today") else "",
        " · ".join(machine),
        push,
        busy,
    ]
    return [line[:120] for line in lines][:LINES]


def tooltip(status: dict) -> str:
    """Windows truncates a tray tooltip at 127 characters, so this stays short."""
    parts = [headline(status)]
    if status.get("error"):
        parts.append("push failing")
    return f"mc-status — {', '.join(parts)}"[:127]


# ---------------------------------------------------------------- what it looks like


def _shade(colour: tuple[int, int, int], by: float) -> tuple[int, int, int]:
    return tuple(min(255, round(value + (255 - value) * by)) for value in colour)


def icon_image(status: dict, size: int = 64) -> Image.Image:
    """A little block, in the state's colour. Drawn rather than shipped as a file:
    Pillow is already a dependency, and one colour per state beats any artwork at
    the size a tray actually renders."""
    base = COLOURS[condition(status)]
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    radius = max(2, size // 8)
    edge = (1, 1, size - 2, size - 2)
    draw.rounded_rectangle(edge, radius, fill=base)
    # A lighter band across the top, so the icon still reads as a block and not a dot.
    grass = round(size * 0.42)
    draw.rounded_rectangle((1, 1, size - 2, grass), radius, fill=_shade(base, 0.35))
    draw.rectangle((1, grass - radius, size - 2, grass), fill=_shade(base, 0.35))
    return image


# ---------------------------------------------------------------- the icon itself


def _open_path(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606 — the path is ours, not user input
        elif sys.platform == "darwin":
            run_hidden(["open", str(path)])
        else:
            run_hidden(["xdg-open", str(path)])
    except OSError as err:
        log.warning("could not open %s: %s", path, err)


class Tray:
    """The icon, and the two flags the push loop watches.

    The loop keeps running in its own thread; this only reads what the loop left
    in `state` and draws it. Nothing here can make a push fail: an icon that
    cannot be drawn logs and the agent carries on headless.
    """

    def __init__(self, config: dict, log_file: Path | None = None):
        self.config = config
        self.log_file = log_file
        self.status = blank_status()
        self.stopping = threading.Event()
        self.push_now = threading.Event()
        self._icon = None

    # ---- read by the push loop

    def wait(self, seconds: float) -> str:
        """Sleep, unless the menu asks for a push or for the agent to stop.
        Returns STOP, PUSH, or "" when the time was simply up."""
        woken = self.push_now.wait(seconds)
        if woken:
            self.push_now.clear()
        if self.stopping.is_set():
            return STOP
        return PUSH if woken else ""

    def pushed(self, state: dict, online: bool, error: BaseException | None) -> None:
        """Take everything the icon shows from the push that just happened."""
        try:
            update(self.status, state, online, error)
        except Exception as err:  # the icon is never worth a crashed agent
            log.warning("tray update failed: %s", err)
            return
        self.refresh()

    def refresh(self) -> None:
        icon = self._icon
        if icon is None:
            return
        try:
            icon.icon = icon_image(self.status)
            icon.title = tooltip(self.status)
            icon.update_menu()
        except Exception as err:
            log.warning("tray redraw failed: %s", err)

    # ---- the menu's actions

    def _open_page(self) -> None:
        url = self.config.get("site", {}).get("url")
        if url:
            webbrowser.open(url)

    def _open_log(self) -> None:
        if self.log_file:
            _open_path(Path(self.log_file))

    def _push_now(self) -> None:
        self.push_now.set()

    def _quit(self) -> None:
        self.stopping.set()
        self.push_now.set()  # wake the loop so it stops now rather than at the interval
        self._stop_icon()    # and take the icon away now, not when the loop notices

    def run(self, loop) -> int:
        """Hold the icon on this thread and run `loop` on another. pystray needs the
        main thread, and the push loop does not care which thread it is on."""
        try:
            import pystray
        except Exception as err:
            # A missing tray backend is not a reason to stop reporting. This runs
            # as a login task: dying here would take the status page down with it.
            log.error("no tray icon (%s) — running without one. Install it with "
                      "`pip install -r agent/requirements-tray.txt`.", err)
            loop()
            return 0

        items = [
            pystray.MenuItem(_line(self.status, index), None, enabled=False,
                             visible=_has_line(self.status, index))
            for index in range(LINES)
        ]
        items.append(pystray.Menu.SEPARATOR)
        if self.config.get("site", {}).get("url"):
            items.append(pystray.MenuItem("Open the status page", self._open_page, default=True))
        if self.log_file:
            items.append(pystray.MenuItem("Open the log", self._open_log))
        items += [pystray.MenuItem("Push now", self._push_now), pystray.Menu.SEPARATOR,
                  pystray.MenuItem("Quit", self._quit)]

        self._icon = pystray.Icon("mc-status", icon_image(self.status), tooltip(self.status),
                                  pystray.Menu(*items))
        # pystray runs `setup` in its own thread once the icon is up, which is both
        # where the push loop belongs and what keeps the first redraw from landing
        # before there is an icon to redraw. run() holds this thread until Quit.
        self._icon.run(setup=lambda icon: self._start(icon, loop))
        self.stopping.set()
        return 0

    def _start(self, icon, loop) -> None:
        icon.visible = True  # pystray only does this itself when there is no setup
        try:
            loop()
        except Exception:
            log.exception("the push loop stopped")
        finally:
            # A loop that has stopped with the icon still up would report yesterday's
            # numbers for ever, which is worse than no icon at all.
            self.stopping.set()
            self._stop_icon()

    def _stop_icon(self) -> None:
        try:
            if self._icon is not None:
                self._icon.stop()
        except Exception as err:  # already stopping, on some backends
            log.info("tray icon would not stop cleanly: %s", err)


def _line(status: dict, index: int):
    return lambda item: (summary(status)[index] or "")


def _has_line(status: dict, index: int):
    return lambda item: bool(summary(status)[index])


def update(status: dict, state: dict, online: bool, error: BaseException | None = None) -> dict:
    """Everything the icon shows comes from the last push's payload, which
    agent.run_once leaves in `state`, plus the two background threads."""
    payload = state.get("last_payload") or {}
    now = time.time()
    status["online"] = bool(online)
    status["error"] = str(error) if error else None
    if not error:
        status["pushed_at"] = now

    player = payload.get("player") or {}
    status["player"] = player.get("name")
    status["dimension"] = player.get("dimension") if online else None

    system = payload.get("system") or {}
    for key, source in (("cpu", "cpu_percent"), ("gpu", "gpu_percent"), ("cpu_temp", "cpu_temp")):
        status[key] = system.get(source)

    days = payload.get("playtime") or []
    status["today"] = days[-1].get("seconds") if days else None
    seen = payload.get("last_seen") or state.get("last_seen") or {}
    status["last_seen_at"] = seen.get("at")

    status["rendering"] = _alive(state.get("render_thread"))
    status["publishing"] = _alive(state.get("publish_thread"))
    return status


def _alive(thread) -> bool:
    return bool(thread is not None and thread.is_alive())
