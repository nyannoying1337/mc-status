"""Frames kept over time, and published to the repo's `shots` branch.

The mod overwrites one latest.png, and the Worker keeps one frame, so until now
nothing anywhere remembered yesterday. This keeps a thinned copy on your own
machine and publishes a window of it.

Two sizes, because they do different jobs: a 320 px thumbnail so the whole strip
can load at once while you drag the scrubber, and a 1280 px frame fetched only
when you open one. The local window is longer than the published one on purpose
— publishing replaces the branch wholesale, so a frame that rolls out of it is
gone for good, and the local copy is the only undo.
"""

from __future__ import annotations

import datetime
import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

import upload
from common import (expand, hide_coordinates, log, mod_dir, run_hidden, share_server_world, source_type,
                    write_json_atomic)

DEFAULT_DIR = Path(__file__).with_name("shots")
# Your own disk is cheap; the published branch is not. Keeping more locally than
# is published is what makes the ten-day window recoverable rather than final.
KEEP_DAYS = 30
PUBLISH_DAYS = 10
EVERY_SECONDS = 300
FULL_WIDTH, FULL_QUALITY = 1280, 78
THUMB_WIDTH, THUMB_QUALITY = 320, 70
PANORAMA_QUALITY = 80
BRANCH = "shots"


def archive_dir(config: dict) -> Path:
    raw = config.get("shots", {}).get("directory")
    return expand(raw) if raw else DEFAULT_DIR


def enabled(config: dict) -> bool:
    return bool(config.get("shots", {}).get("keep", False))


def every_seconds(config: dict) -> int:
    try:
        return max(30, int(config.get("shots", {}).get("every_seconds", EVERY_SECONDS)))
    except (TypeError, ValueError):
        return EVERY_SECONDS


# ---------------------------------------------------------------- keeping


def track(config: dict, state: dict, player: dict, mode: str | None, now: float) -> None:
    """Keep the current frame if it's new and enough time has passed."""
    if not enabled(config) or source_type(config) != "mod" or not player.get("online"):
        return
    if mode == "multiplayer" and not share_server_world(config):
        return

    path = mod_dir(config) / "latest.png"
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return
    if stamp == state.get("archived_shot"):
        return
    if now - state.get("archived_at", 0) < every_seconds(config):
        return

    root = archive_dir(config)
    day = time.strftime("%Y-%m-%d", time.localtime(stamp))
    folder = root / day
    name = time.strftime("%H%M%S", time.localtime(stamp))
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.jpg").write_bytes(upload.encode_jpeg(path, FULL_QUALITY, FULL_WIDTH))
        (folder / f"{name}.t.jpg").write_bytes(upload.encode_jpeg(path, THUMB_QUALITY, THUMB_WIDTH))
    except Exception as err:
        log.warning("could not archive %s: %s", path.name, err)
        return

    entry = {"at": int(stamp * 1000), "file": f"{day}/{name}.jpg", "thumb": f"{day}/{name}.t.jpg"}
    if player.get("dimension"):
        entry["dimension"] = player["dimension"]
    position = player.get("position")
    # Coordinates follow the same rules here as everywhere else: never written
    # to the archive at all rather than written and stripped later.
    if isinstance(position, list) and len(position) == 3 and not hide_coordinates(config):
        entry["position"] = [round(value) for value in position]
    _append(folder / "day.json", entry)

    state["archived_shot"] = stamp
    state["archived_at"] = now
    _prune(root, KEEP_DAYS)


def keep_panorama(config: dict, state: dict) -> None:
    """One panorama a day: the last logout of that day, overwritten as it goes."""
    if not enabled(config) or source_type(config) != "mod":
        return
    folder = mod_dir(config)
    try:
        taken_at = int(json.loads((folder / "panorama.json").read_text(encoding="utf-8"))["at"])
    except (OSError, ValueError, KeyError, TypeError):
        return
    if taken_at == state.get("archived_pano"):
        return
    day = time.strftime("%Y-%m-%d", time.localtime(taken_at / 1000))
    target = archive_dir(config) / "pano"
    try:
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{day}.jpg").write_bytes(upload.encode_jpeg(folder / "panorama.png", PANORAMA_QUALITY))
    except Exception as err:
        log.warning("could not archive panorama: %s", err)
        return
    state["archived_pano"] = taken_at
    log.info("archived the panorama for %s", day)


def _append(path: Path, entry: dict) -> None:
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            entries = []
    except (OSError, json.JSONDecodeError):
        entries = []
    entries.append(entry)
    entries.sort(key=lambda item: item.get("at", 0))
    write_json_atomic(path, entries)


def _days(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    return sorted(item.name for item in root.iterdir()
                  if item.is_dir() and item.name != "pano" and _is_date(item.name))


def _is_date(name: str) -> bool:
    try:
        datetime.date.fromisoformat(name)
        return True
    except ValueError:
        return False


def _prune(root: Path, keep: int) -> None:
    for day in _days(root)[:-keep] if keep else []:
        shutil.rmtree(root / day, ignore_errors=True)
    panoramas = root / "pano"
    if not panoramas.is_dir():
        return
    stale = sorted(p.name for p in panoramas.glob("*.jpg"))[:-keep] if keep else []
    for name in stale:
        (panoramas / name).unlink(missing_ok=True)


# ---------------------------------------------------------------- publishing


def index(root: Path, days: list[str]) -> dict:
    frames: list[dict] = []
    for day in days:
        try:
            entries = json.loads((root / day / "day.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(entries, list):
            frames.extend(entry for entry in entries if isinstance(entry, dict))
    frames.sort(key=lambda entry: entry.get("at", 0))
    panoramas = {day: f"pano/{day}.jpg" for day in days if (root / "pano" / f"{day}.jpg").is_file()}
    return {"frames": frames, "panoramas": panoramas, "built_at": int(time.time() * 1000)}


def stage(config: dict, into: Path) -> dict:
    """Copy the published window into a clean directory and write its index."""
    root = archive_dir(config)
    days = _days(root)[-PUBLISH_DAYS:]
    shutil.rmtree(into, ignore_errors=True)
    into.mkdir(parents=True, exist_ok=True)
    for day in days:
        shutil.copytree(root / day, into / day, ignore=shutil.ignore_patterns("day.json"))
    manifest = index(root, days)
    if manifest["panoramas"]:
        (into / "pano").mkdir(exist_ok=True)
        for day in manifest["panoramas"]:
            shutil.copy2(root / "pano" / f"{day}.jpg", into / "pano" / f"{day}.jpg")
    write_json_atomic(into / "index.json", manifest)
    return manifest


def publish(config: dict, remote: str = "origin") -> bool:
    """Replace the `shots` branch with the published window.

    Force-pushed as a single commit, exactly like map/render.py does for tiles:
    re-publishing never piles image history into the repo. The flip side is that
    a frame which rolls out of the window is gone, which is why the local archive
    keeps more than this does.
    """
    if not config.get("shots", {}).get("publish", False):
        return False
    here = Path(__file__).resolve().parent.parent
    try:
        url = run_hidden(["git", "remote", "get-url", remote], cwd=here,
                         capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError) as err:
        log.warning("no git remote to publish shots to: %s", err)
        return False

    # GitHub only runs workflows that exist in the pushed branch, so the branch
    # carries a copy of the site deploy, the same way the map branch does.
    workflow = here / ".github" / "workflows" / "pages.yml"
    work = archive_dir(config) / ".publish"
    try:
        # Staging reads a tree the player can still be writing into: they may have
        # rejoined and archived another frame, or the disk may be full. This runs
        # in a daemon thread, where an escaping error is a silent death.
        manifest = stage(config, work)
        if not manifest["frames"]:
            log.info("nothing archived yet — not publishing")
            return False
        (work / ".nojekyll").touch()
        if workflow.is_file():
            (work / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
            shutil.copy2(workflow, work / ".github" / "workflows" / "pages.yml")
        _run(["git", "init", "-q", "-b", BRANCH, "."], work)
        _run(["git", "add", "-A"], work)
        _run(["git", "-c", "user.name=mc-status", "-c", "user.email=mc-status@localhost",
              "commit", "-q", "-m", f"{len(manifest['frames'])} frames"], work)
        _run(["git", "push", "--force", url, f"{BRANCH}:{BRANCH}"], work)
    except (OSError, subprocess.SubprocessError, shutil.Error) as err:
        log.warning("could not publish shots: %s", err)
        return False
    finally:
        shutil.rmtree(work, ignore_errors=True)
    log.info("published %d frames to the %s branch", len(manifest["frames"]), BRANCH)
    return True


def publish_later(config: dict, state: dict) -> None:
    """Push in the background: a few dozen MB of frames shouldn't stall the
    status loop, the same reason presence.py threads the logout render."""
    if not config.get("shots", {}).get("publish", False):
        return
    running = state.get("publish_thread")
    if running and running.is_alive():
        log.info("shots publish skipped: the previous push is still running")
        return
    thread = threading.Thread(target=publish, args=(config,), name="shots-publish", daemon=True)
    state["publish_thread"] = thread
    thread.start()


def _run(command: list[str], cwd: Path) -> None:
    run_hidden(command, cwd=cwd, check=True, capture_output=True, text=True)
