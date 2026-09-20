"""The tray icon's wording, state and colour, and the no-console-window flags.

Everything the icon shows is a plain function over a status dict, so it can be
checked here without a desktop; pystray is never imported. The CREATE_NO_WINDOW
flags are the reason a logout used to throw terminals on the screen, so they are
pinned down too — on Linux they have to be absent, on Windows present.
"""

import logging, subprocess, sys, threading, time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "agent"))
import common, tray  # noqa: E402

# ============================================================ no console windows

# The flag only exists on Windows, and must not be passed anywhere else.
if sys.platform == "win32":
    assert common.NO_WINDOW == {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    assert common.NO_WINDOW == {}

# Still a working subprocess call after the flags are folded in.
done = common.run_hidden([sys.executable, "-c", "print('hi')"], capture_output=True, text=True, check=True)
assert done.stdout.strip() == "hi"
process = common.popen_hidden([sys.executable, "-c", "print('there')"], stdout=subprocess.PIPE, text=True)
assert process.communicate()[0].strip() == "there"

# Every subprocess the agent starts goes through them, and render.py — which runs
# standalone, so it carries its own copy — spreads NO_WINDOW into each call itself.
# A new subprocess that forgets is exactly how the terminals came back.
import ast  # noqa: E402

def subprocess_calls(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"
                and node.func.attr in ("run", "Popen", "call", "check_output")):
            yield node

for module in ("agent.py", "archive.py", "collect.py", "media.py", "presence.py", "sysinfo.py", "tray.py"):
    calls = list(subprocess_calls(HERE.parent / "agent" / module))
    assert not calls, f"{module}: line {calls[0].lineno} should use run_hidden/popen_hidden"

for call in subprocess_calls(HERE.parent / "map" / "render.py"):
    spread = [keyword.value for keyword in call.keywords if keyword.arg is None]
    assert any(isinstance(value, ast.Name) and value.id == "NO_WINDOW" for value in spread), \
        f"render.py line {call.lineno}: a subprocess without **NO_WINDOW pops a console window"
print("NO-WINDOW TESTS PASSED")

# ============================================================ what the icon says

playing = {"online": True, "player": "nyannoying", "dimension": "minecraft:the_nether",
           "cpu": 34.2, "gpu": 61.0, "cpu_temp": 58.4, "today": 5025, "pushed_at": time.time()}
assert tray.condition(playing) == tray.PLAYING
lines = tray.summary(playing)
assert lines[0] == "In game as nyannoying, in the Nether", lines
assert lines[1] == "Played today — 1 h 23 min", lines
assert lines[2] == "CPU 34% · GPU 61% · 58 °C", lines
assert lines[3].startswith("Pushed at "), lines
assert lines[4] == "", lines
assert len(lines) == tray.LINES
assert tray.tooltip(playing).startswith("mc-status — In game as nyannoying")

# Away: the headline becomes when you left, and playtime is only shown if there is any.
away = {"online": False, "last_seen_at": 1_700_000_000_000, "pushed_at": time.time(), "publishing": True}
assert tray.condition(away) == tray.AWAY
lines = tray.summary(away)
assert lines[0].startswith("Away since "), lines
assert lines[1] == "" and lines[2] == "", lines
assert lines[4] == "Publishing frames…", lines
assert tray.summary({"online": False, "rendering": True})[4] == "Rendering the map…"
assert tray.summary({})[0] == "Waiting for Minecraft"
assert tray.summary({"online": True})[0] == "In game"
assert tray.dimension_name("minecraft:overworld") == "the Overworld"
assert tray.dimension_name("someone_elses:deep_dark") == "deep dark" and tray.dimension_name(None) == ""
assert tray.summary({"online": False, "pushed_at": None})[3] == "Nothing pushed yet"

# A failing push is the one thing that changes the colour, whatever the game is doing.
broken = dict(playing, error="connection refused")
assert tray.condition(broken) == tray.BROKEN
assert tray.summary(broken)[3] == "Push failed — connection refused"
assert "push failing" in tray.tooltip(broken)
assert len(tray.tooltip(dict(broken, player="x" * 300))) <= 127

# Seconds and milliseconds both turn up in the payload; both read as a time of day.
assert tray._clock(1_700_000_000_000) == tray._clock(1_700_000_000)
assert tray._duration(30) == "under a minute" and tray._duration(3600) == "1 h 0 min"
assert tray._duration(600) == "10 min" and tray._duration(None) == "under a minute"
print("WORDING TESTS PASSED")

# ============================================================ the picture

for status, expected in ((playing, tray.PLAYING), (away, tray.AWAY), (broken, tray.BROKEN)):
    image = tray.icon_image(status, 32)
    assert image.size == (32, 32) and image.mode == "RGBA"
    assert image.getpixel((16, 24))[:3] == tray.COLOURS[expected], expected
    assert image.getpixel((16, 6))[:3] != tray.COLOURS[expected], "no lighter band across the top"
    assert image.getpixel((0, 0))[3] == 0, "the corners should be transparent"
print("ICON TESTS PASSED")

# ============================================================ what the loop reads

state = {
    "last_payload": {
        "player": {"online": True, "name": "nyannoying", "dimension": "minecraft:overworld"},
        "system": {"cpu_percent": 12.0, "gpu_percent": 3.5, "cpu_temp": 44.0},
        "playtime": [{"date": "2026-09-19", "seconds": 60}, {"date": "2026-09-20", "seconds": 900}],
        "last_seen": {"at": 1_700_000_000_000},
    },
}
status = tray.blank_status()
tray.update(status, state, online=True)
assert status["player"] == "nyannoying" and status["today"] == 900 and status["cpu"] == 12.0
assert status["error"] is None and status["pushed_at"]
assert status["rendering"] is False and status["publishing"] is False

# A failed push keeps the last good pushed_at, so the menu still says when it last worked.
pushed_at = status["pushed_at"]
tray.update(status, state, online=True, error=RuntimeError("worker said 500"))
assert status["pushed_at"] == pushed_at and status["error"] == "worker said 500"

# A running render or publish thread is the only "busy" the icon knows about.
running = threading.Event()
thread = threading.Thread(target=running.wait, daemon=True)
thread.start()
tray.update(status, dict(state, render_thread=thread), online=False)
assert status["rendering"] is True and status["dimension"] is None
running.set()
thread.join()
tray.update(status, dict(state, render_thread=thread), online=False)
assert status["rendering"] is False
print("UPDATE TESTS PASSED")

# ============================================================ the loop's waiting

icon = tray.Tray({"site": {"url": "https://example.github.io/mc-status/"}}, log_file=None)
assert icon.wait(0.01) == "", "an uninterrupted wait just returns"
icon._push_now()
started = time.time()
assert icon.wait(30) == tray.PUSH, "Push now should not wait out the interval"
assert time.time() - started < 5
assert icon.wait(0.01) == "", "the request is cleared once it has been acted on"
icon._quit()
assert icon.wait(30) == tray.STOP

# An icon that cannot be drawn never takes a push down with it.
icon.pushed({"last_payload": {"player": None}}, online=False, error=None)
print("WAIT TESTS PASSED")

# ============================================================ the agent's loop, headless

import agent  # noqa: E402

pushes = []
agent.run_once = lambda config, state: (pushes.append(dict(state)), False)[1]
agent.loop({"agent": {"interval_seconds": 1, "offline_interval_seconds": 1}}, {}, once=True)
assert len(pushes) == 1, "--once still pushes exactly once"

# With a tray, a push that raises is reported to the icon rather than escaping.
def explode(config, state):
    raise RuntimeError("no worker")

agent.run_once = explode
logging.disable(logging.CRITICAL)  # the traceback below is the point, not CI noise
icon = tray.Tray({})
agent.loop({"agent": {"interval_seconds": 1}}, {}, once=True, icon=icon)
logging.disable(logging.NOTSET)
assert icon.status["error"] == "no worker" and tray.condition(icon.status) == tray.BROKEN
print("LOOP TESTS PASSED")
