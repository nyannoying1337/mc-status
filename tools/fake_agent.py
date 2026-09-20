#!/usr/bin/env python3
"""Stands in for the agent and the mod, so the page can be exercised without Minecraft.

The Worker needs no stand-in: `wrangler dev` runs the real one. What needs faking is
the other end of the push — the mod that reads the game and the agent that uploads —
because that is the half that needs a running Minecraft.

This pushes the same fixture the page's ?demo mode draws (site/js/demo-data.js, read
through Node so there is only one copy of it) at a local Worker, with a frame and a
panorama drawn on the fly. The page then goes through its real path: WebSocket,
invite key, broadcasts, /shot and /pano.

    python tools/fake_agent.py                     # keep pushing, player online
    python tools/fake_agent.py --scene logout      # logged out: last seen and a panorama
    python tools/fake_agent.py --scene stale       # one push, then silence, so it goes quiet
    python tools/fake_agent.py --once

tools/preview.py runs this for you along with the Worker and the page.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "site" / "js" / "demo-data.js"
DEV_VARS = ROOT / "worker" / ".dev.vars"

DEFAULT_WORKER = "http://127.0.0.1:8788"
SCENES = ("playing", "logout", "stale")

# The page calls the machine quiet after STALE_MS (90 s in worker.js), so pushing a
# little under half that keeps it live with room for a slow render.
DEFAULT_INTERVAL = 5


def say(message: str) -> None:
    print(f"fake-agent: {message}", flush=True)


# ---- the fixture ---------------------------------------------------------------------

def read_fixture(*, logged_out: bool, images: bool) -> dict:
    """The page's own demo fixture, evaluated by Node so it can't drift from ?demo."""
    have = json.dumps({"frames": images, "panorama": images})
    script = (
        f'import {{ demoStatus }} from {json.dumps(FIXTURE.as_uri())};\n'
        f"process.stdout.write(JSON.stringify(demoStatus("
        f'{{ loggedOut: {json.dumps(logged_out)}, have: {have} }})));\n'
    )
    try:
        done = subprocess.run(["node", "--input-type=module", "-e", script],
                              capture_output=True, text=True, check=True)
    except FileNotFoundError:
        sys.exit("fake-agent: needs Node (20+) to read the page's fixture; it isn't on PATH.")
    except subprocess.CalledProcessError as error:
        sys.exit(f"fake-agent: couldn't read {FIXTURE.name}:\n{error.stderr.strip()}")
    status = json.loads(done.stdout)
    # Page-side paths for ?demo imagery; a real push carries the images themselves.
    for key in ("demo_shot", "demo_panorama"):
        status.pop(key, None)
    return status


def read_token(given: str | None) -> str:
    if given:
        return given
    if not DEV_VARS.exists():
        sys.exit(f"fake-agent: no {DEV_VARS.relative_to(ROOT)} and no --token. "
                 f"Copy worker/.dev.vars.example to it, or pass --token.")
    for line in DEV_VARS.read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition("=")
        if name.strip() == "PUSH_TOKEN":
            return value.strip().strip('"').strip("'")
    sys.exit(f"fake-agent: no PUSH_TOKEN in {DEV_VARS.relative_to(ROOT)}.")


# ---- the imagery ---------------------------------------------------------------------

# A frame the mod would have captured, and the six faces of a logout panorama. They are
# invented, like the rest of the fixture — the point is that something arrives, that it
# is the right shape, and that you can see it change.

def _pillow():
    try:
        from PIL import Image, ImageDraw  # noqa: F401
        return True
    except ImportError:
        return False


def draw_frame(tick: int) -> bytes:
    """A 16:9 'screenshot': sky, sun, hills, and a caption that moves so you can see it update."""
    import io
    from PIL import Image, ImageDraw

    width, height = 1280, 720
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        if y < height * 0.62:                       # sky, light at the horizon
            t = y / (height * 0.62)
            draw.line([(0, y), (width, y)], fill=(int(96 + 110 * t), int(150 + 80 * t), int(224 + 20 * t)))
        else:                                        # ground
            t = (y - height * 0.62) / (height * 0.38)
            draw.line([(0, y), (width, y)], fill=(int(86 - 30 * t), int(134 - 44 * t), int(58 - 20 * t)))
    sun = (width - 180 - (tick * 7) % (width - 360), 120)
    draw.ellipse([sun[0] - 46, sun[1] - 46, sun[0] + 46, sun[1] + 46], fill=(255, 241, 176))
    for x, top in ((120, 470), (430, 440), (760, 486), (1030, 452)):  # blocky trees
        draw.rectangle([x, top + 70, x + 26, top + 150], fill=(96, 70, 44))
        draw.rectangle([x - 54, top, x + 80, top + 80], fill=(58, 112, 48))
    # The page crops the frame to a wider strip than 16:9, so the caption sits well
    # inside the picture rather than along its bottom edge, where it would be cut off.
    draw.rectangle([0, int(height * 0.78), width, int(height * 0.84)], fill=(20, 20, 22))
    draw.text((24, int(height * 0.795)),
              f"mc-status local preview  ·  invented frame #{tick}  ·  {time.strftime('%H:%M:%S')}",
              fill=(236, 236, 236))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=72, optimize=True)
    return buffer.getvalue()


def draw_panorama() -> bytes:
    """Six 90° faces in one 6:1 strip, the layout site/js/panorama.js slices up."""
    import io
    from PIL import Image, ImageDraw

    face = 256
    faces = [("front", (96, 150, 224)), ("right", (118, 162, 228)), ("back", (140, 174, 232)),
             ("left", (118, 162, 228)), ("up", (74, 132, 220)), ("down", (70, 108, 48))]
    image = Image.new("RGB", (face * 6, face))
    draw = ImageDraw.Draw(image)
    for index, (label, colour) in enumerate(faces):
        left = index * face
        draw.rectangle([left, 0, left + face - 1, face - 1], fill=colour)
        if label not in ("up", "down"):  # a horizon, so turning is obvious
            draw.rectangle([left, int(face * 0.62), left + face - 1, face - 1], fill=(72, 118, 52))
        draw.text((left + 12, 12), label, fill=(255, 255, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=76, optimize=True)
    return buffer.getvalue()


# ---- pushing -------------------------------------------------------------------------

def post(url: str, token: str, body: bytes, content_type: str) -> None:
    request = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": content_type})
    with urllib.request.urlopen(request, timeout=20) as response:
        response.read()


def push(worker: str, token: str, status: dict, image: bytes | None, panorama: bytes | None) -> None:
    post(f"{worker}/status", token, json.dumps(status).encode(), "application/json")
    if image is not None:
        post(f"{worker}/shot", token, image, "image/jpeg")
    if panorama is not None:
        post(f"{worker}/pano", token, panorama, "image/jpeg")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--worker", default=DEFAULT_WORKER, help=f"default {DEFAULT_WORKER}")
    parser.add_argument("--token", help="PUSH_TOKEN; read from worker/.dev.vars by default")
    parser.add_argument("--scene", default="playing", choices=SCENES)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL, help="seconds between pushes")
    parser.add_argument("--once", action="store_true", help="push once and stop")
    args = parser.parse_args()

    worker = args.worker.rstrip("/")
    token = read_token(args.token)
    images = _pillow()
    if not images:
        say("no Pillow, so no frame and no panorama (pip install Pillow). Cards still render.")

    logged_out = args.scene == "logout"
    panorama = draw_panorama() if images and logged_out else None
    tick = 0

    while True:
        tick += 1
        status = read_fixture(logged_out=logged_out, images=images)
        frame = draw_frame(tick) if images and not logged_out else None
        if frame is not None:
            status["screenshot_at"] = int(time.time() * 1000)
        try:
            push(worker, token, status, frame, panorama if tick == 1 else None)
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace").strip()
            if error.code == 401:
                sys.exit(f"fake-agent: the Worker refused the token ({detail}). "
                         f"Check PUSH_TOKEN in worker/.dev.vars matches the one wrangler loaded.")
            sys.exit(f"fake-agent: the Worker answered {error.code}: {detail}")
        except OSError as error:
            sys.exit(f"fake-agent: can't reach {worker} ({error}). Is `wrangler dev` running?")

        if tick == 1:
            what = {"playing": "playing", "logout": "logged out", "stale": "one push, then silence"}
            say(f"pushed to {worker} — scene: {what[args.scene]}")
        if args.once:
            return
        if args.scene == "stale":
            say("staying quiet now; the page should call the machine quiet in about 90 seconds.")
            while True:
                time.sleep(3600)
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
