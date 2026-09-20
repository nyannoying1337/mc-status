"""Where the player was last seen, and the map render when they leave a world."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

from common import hide_coordinates, log, popen_hidden, worker_endpoint

RENDER_SCRIPT = Path(__file__).resolve().parent.parent / "map" / "render.py"

# Said once per run, not once per logout: someone who does not want maps should not
# read about it every time they quit.
_said_render_is_off = False


def track(config: dict, state: dict, player: dict, raw: dict | None, mode: str | None) -> None:
    """Remember where the player was; when they leave a singleplayer world,
    render that spot. Server sessions never touch the map: their coordinates
    belong to someone else's world."""
    now_ms = int(time.time() * 1000)

    if player.get("online"):
        state["was_online"] = True
        state["session_mode"] = mode
        # always from this session, so a server session can't reuse the last world
        state["world_path"] = raw.get("world_path") if raw and mode == "singleplayer" else None
        if mode == "multiplayer":
            state["last_seen"] = {"mode": "multiplayer", "at": now_ms}
            return
        source = raw if raw else player
        position = source.get("position")
        if isinstance(position, list) and len(position) == 3:
            state["last_seen"] = {
                "position": position,
                "dimension": source.get("dimension"),
                "at": now_ms,
                **({"mode": mode} if mode else {}),
            }
        return

    if state.get("was_online"):
        state["was_online"] = False
        if state.get("session_mode") == "multiplayer":
            log.info("left a multiplayer server — no map render")
        else:
            start_logout_render(config, state)
    elif raw and "last_seen" not in state:
        # Agent started while the player is away (restart, reboot): the mod's
        # last state still says where and when they left.
        at = raw.get("written_at") or now_ms
        if mode == "multiplayer":
            state["last_seen"] = {"mode": "multiplayer", "at": at}
        elif isinstance(raw.get("position"), list):
            state["last_seen"] = {"position": raw["position"], "dimension": raw.get("dimension"),
                                  "at": at, "mode": "singleplayer"}
            state["world_path"] = raw.get("world_path")


def last_seen_payload(config: dict, state: dict) -> dict | None:
    seen = state.get("last_seen")
    if not seen:
        return None
    payload = dict(seen)
    if hide_coordinates(config) and "position" in payload:
        payload["position"] = None
    return payload


def start_logout_render(config: dict, state: dict) -> None:
    global _said_render_is_off
    map_config = config.get("map", {})
    if not map_config.get("render_on_logout", False):
        # Every other reason to skip logs one line. This one used to return in
        # silence, which made the likeliest cause — the switch is simply off — the
        # only one the log could not tell you about.
        if not _said_render_is_off:
            _said_render_is_off = True
            log.info("no map on logout: [map] render_on_logout is off in agent/config.toml")
        return
    world = map_config.get("world") or state.get("world_path")
    seen = state.get("last_seen") or {}
    if not world or not seen.get("position") or not seen.get("dimension"):
        log.info("logout render skipped: no world path or last position known")
        return
    running = state.get("render_thread")
    if running and running.is_alive():
        log.info("logout render skipped: previous render still running")
        return
    thread = threading.Thread(
        target=render_after_save,
        args=(config, Path(world), seen, lambda: state.get("was_online", False)),
        name="map-render",
        daemon=True,
    )
    state["render_thread"] = thread
    thread.start()


def session_lock_released(world: Path) -> bool:
    """Minecraft holds session.lock while the world is open; on Windows the
    lock makes the file unreadable, elsewhere the level.dat check has to do."""
    try:
        with (world / "session.lock").open("rb") as handle:
            handle.read(1)
        return True
    except FileNotFoundError:
        return True
    except PermissionError:
        return False


def wait_for_world_saved(world: Path, rejoined, quiet_seconds: float = 5, timeout: float = 180,
                         poll: float = 1.0) -> bool:
    """True once the save looks finished; False if the player came back first."""
    deadline = time.time() + timeout
    level = world / "level.dat"
    while time.time() < deadline:
        if rejoined():
            return False
        try:
            settled = time.time() - level.stat().st_mtime >= quiet_seconds
        except FileNotFoundError:
            settled = True
        if settled and session_lock_released(world):
            return True
        time.sleep(poll)
    log.warning("world %s still looks busy after %ds — rendering anyway", world, timeout)
    return True


def render_command(config: dict, world: Path, seen: dict) -> list[str]:
    map_config = config.get("map", {})
    x, _, z = seen["position"]
    command = [
        sys.executable, str(RENDER_SCRIPT),
        "--world", str(world),
        "--center", str(int(x)), str(int(z)),
        "--radius-chunks", str(int(map_config.get("radius_chunks", 8))),
        "--dimension", seen["dimension"],
        "--live-url", f"{worker_endpoint(config)}/bluemap",
    ]
    if map_config.get("accept_mojang_eula", False):
        command.append("--accept-mojang-eula")
    if map_config.get("java"):
        command += ["--java", str(map_config["java"])]
    if map_config.get("remote"):
        command += ["--remote", str(map_config["remote"])]
    if map_config.get("publish", True):
        command.append("--publish")
    return command


def render_after_save(config: dict, world: Path, seen: dict, rejoined) -> None:
    if not wait_for_world_saved(world, rejoined):
        log.info("logout render cancelled: player rejoined")
        return
    command = render_command(config, world, seen)
    log.info("rendering map around %s in %s", seen["position"], seen["dimension"])
    try:
        process = popen_hidden(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               encoding="utf-8", errors="replace")
        for line in process.stdout:
            log.info("render: %s", line.rstrip())
        code = process.wait(timeout=30 * 60)
    except (OSError, subprocess.SubprocessError) as err:
        log.warning("map render failed to run: %s", err)
        return
    if code:
        log.warning("map render exited with %d", code)
    else:
        log.info("map render finished")
