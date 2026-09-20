"""Config access, paths and privacy rules shared by the agent's modules."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

log = logging.getLogger("agent")

# The agent runs without a console of its own: a scheduled task on pythonw.exe,
# launchd, systemd. Windows gives every console program started from such a
# process a console of its own, so git and java each threw up a terminal on the
# desktop — a handful of them at once every time a session ended. CREATE_NO_WINDOW
# keeps them where they belong, in the log. Every subprocess the agent starts goes
# through run_hidden or popen_hidden; map/render.py carries its own copy, because
# it runs standalone and imports nothing from here.
NO_WINDOW = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if sys.platform == "win32" else {}

# Minecraft usernames. Anything else is refused before it can be spliced into
# an RCON command.
PLAYER_NAME = re.compile(r"^[A-Za-z0-9_]{3,16}$")


def run_hidden(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run, with no console window on Windows."""
    return subprocess.run(command, **NO_WINDOW, **kwargs)


def popen_hidden(command: list[str], **kwargs) -> subprocess.Popen:
    """subprocess.Popen, with no console window on Windows."""
    return subprocess.Popen(command, **NO_WINDOW, **kwargs)


def load_config(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def source_type(config: dict) -> str:
    """"mod" reads the Fabric mod's files (singleplayer); "rcon" asks a server."""
    return config.get("source", {}).get("type", "mod")


def expand(raw: str) -> Path:
    return Path(os.path.expandvars(raw)).expanduser()


def default_game_dir() -> str:
    if sys.platform == "win32":
        return "%APPDATA%/.minecraft"
    if sys.platform == "darwin":
        return "~/Library/Application Support/minecraft"
    return "~/.minecraft"


def game_dir(config: dict) -> Path:
    return expand(config.get("source", {}).get("game_dir", default_game_dir()))


def mod_dir(config: dict) -> Path:
    return game_dir(config) / "mc-status"


def hide_coordinates(config: dict) -> bool:
    return bool(config.get("privacy", {}).get("hide_coordinates", False))


def share_server_world(config: dict) -> bool:
    """Publish where you are, and frames of what you see, while on someone
    else's server. Off by default: coordinates on a shared world are a route to
    your base, and a frame can hold other players' builds and nametags."""
    return bool(config.get("privacy", {}).get("share_server_world", False))


def apply_privacy(config: dict, player: dict) -> dict:
    """privacy.hide_coordinates: no position, facing or death spot anywhere."""
    if hide_coordinates(config):
        if "position" in player:
            player["position"] = None
        player.pop("rotation", None)
        player.pop("last_death", None)
    return player


def write_json_atomic(path: Path, data) -> None:
    """Write beside the target and swap it in, so readers never see half a file."""
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    os.replace(temp, path)


def worker_endpoint(config: dict) -> str:
    return config["worker"]["url"].rstrip("/")


def auth_header(config: dict) -> dict:
    return {"Authorization": f"Bearer {config['worker']['token']}"}
