#!/usr/bin/env python3
"""Runs the whole status page locally: Worker, page and sample data, in one command.

    python tools/preview.py                  # then open the link it prints
    python tools/preview.py --scene logout   # the logged-out half: panorama and last seen
    python tools/preview.py --no-worker      # you already have `wrangler dev` running

Three pieces, none of which need Minecraft:

  * the real Worker, through `wrangler dev` — worker/worker.js itself, with its
    Durable Object and WebSockets, so there is no second implementation to drift;
  * site/, served statically, with /js/config.js swapped for one pointing at that
    Worker. The file on disk is never touched, so there is nothing to un-commit;
  * tools/fake_agent.py, pushing the page's own demo fixture on a loop.

Ctrl-C stops all three.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
WORKER = ROOT / "worker"
DEV_VARS = WORKER / ".dev.vars"
DEV_VARS_EXAMPLE = WORKER / ".dev.vars.example"
ASSETS = SITE / "assets" / "mc"
LINKS = ROOT / "LOCAL-TEST-LINKS.txt"

DEFAULT_PORT = 8000
DEFAULT_WORKER_PORT = 8788
WORKER_START_TIMEOUT = 120  # the first run downloads the Workers runtime


def say(message: str = "") -> None:
    print(message, flush=True)


# ---- secrets -------------------------------------------------------------------------

def dev_vars() -> dict[str, str]:
    """worker/.dev.vars, created from the example the first time."""
    if not DEV_VARS.exists():
        shutil.copyfile(DEV_VARS_EXAMPLE, DEV_VARS)
        say(f"Wrote {DEV_VARS.relative_to(ROOT)} from the example (gitignored).")
    values = {}
    for line in DEV_VARS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, value = line.partition("=")
        values[name.strip()] = value.strip().strip('"').strip("'")
    for required in ("PUSH_TOKEN", "VIEW_KEY"):
        if not values.get(required):
            sys.exit(f"preview: {DEV_VARS.relative_to(ROOT)} has no {required}. "
                     f"See {DEV_VARS_EXAMPLE.relative_to(ROOT)}.")
    return values


# ---- the page ------------------------------------------------------------------------

def page_server(port: int, api_url: str) -> http.server.ThreadingHTTPServer:
    """site/, with config.js answered from memory instead of from disk.

    The deploy generates that file from repository variables and the copy in the tree
    points at an example Worker (site/js/config.js). Editing it for a preview is one
    `git add -p` away from being published, so it is overridden in flight instead.
    """
    config = (
        "// Served by tools/preview.py; site/js/config.js on disk is untouched.\n"
        f"export const API_URL = {json.dumps(api_url)};\n"
        "export const SITE_NAME = \"Local preview\";\n"
    ).encode()

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(SITE), **kwargs)

        def do_GET(self):
            if self.path.split("?")[0] == "/js/config.js":
                self.send_response(200)
                self.send_header("Content-Type", "text/javascript")
                self.send_header("Content-Length", str(len(config)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(config)
                return
            super().do_GET()

        def end_headers(self):
            # Nothing is cached, so an edit shows up on reload.
            if self.path.split("?")[0] != "/js/config.js":
                self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def log_message(self, *args):
            pass  # the page makes a request per icon; the log would bury everything else

    return http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)


# ---- child processes -----------------------------------------------------------------

# `wrangler dev` is a wrapper: the thing actually holding the port is a workerd it
# starts. Terminating the wrapper alone leaves that behind, and the next preview then
# refuses to start because :8788 is taken — so each child gets its own process group
# and the whole group is signalled.

def spawn(command: list[str], **kwargs) -> subprocess.Popen:
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(command, **kwargs)


def stop(process: subprocess.Popen | None) -> None:
    if not process or process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                       capture_output=True, check=False)
    else:
        for sign in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(os.getpgid(process.pid), sign)
            except (ProcessLookupError, PermissionError):
                return
            try:
                process.wait(timeout=5)
                return
            except subprocess.TimeoutExpired:
                continue
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


# ---- the Worker ----------------------------------------------------------------------

def port_is_free(port: int) -> bool:
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", port)) != 0


def worker_is_up(port: int) -> bool:
    """A 401 from /status means the Worker is answering: no key was given."""
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=2).read()
        return True
    except urllib.error.HTTPError:
        return True
    except OSError:
        return False


def start_worker(port: int) -> subprocess.Popen:
    if not (WORKER / "node_modules").exists():
        say("Installing wrangler (first run only)…")
        if subprocess.run(["npm", "install"], cwd=WORKER).returncode != 0:
            sys.exit("preview: `npm install` failed in worker/.")
    say(f"Starting the Worker on :{port} …")
    process = spawn(["npx", "wrangler", "dev", "--port", str(port), "--local"],
                    cwd=WORKER, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + WORKER_START_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            sys.exit("preview: wrangler stopped. Run `npx wrangler dev` in worker/ to see why.")
        if worker_is_up(port):
            return process
        time.sleep(0.5)
    stop(process)
    sys.exit(f"preview: the Worker didn't come up within {WORKER_START_TIMEOUT}s. "
             f"Run `npx wrangler dev --port {port}` in worker/ to see why.")


# ---- wiring --------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"page port, default {DEFAULT_PORT}")
    parser.add_argument("--worker-port", type=int, default=DEFAULT_WORKER_PORT,
                        help=f"Worker port, default {DEFAULT_WORKER_PORT}")
    parser.add_argument("--scene", default="playing", choices=("playing", "logout", "stale"))
    parser.add_argument("--no-worker", action="store_true", help="a Worker is already running there")
    parser.add_argument("--no-push", action="store_true", help="don't send sample data")
    args = parser.parse_args()

    values = dev_vars()
    api_url = f"http://127.0.0.1:{args.worker_port}"

    if not ASSETS.exists():
        say("No site/assets/mc — item icons and HUD sprites will be missing.")
        say("  Build them once with: pip install Pillow && python site/build_assets.py")
        say()

    worker = None
    if args.no_worker:
        if not worker_is_up(args.worker_port):
            sys.exit(f"preview: nothing is answering on :{args.worker_port}, and --no-worker "
                     f"says not to start one.")
    else:
        if not port_is_free(args.worker_port):
            sys.exit(f"preview: something is already on :{args.worker_port}. "
                     f"Use --no-worker, or --worker-port.")
        worker = start_worker(args.worker_port)

    if not port_is_free(args.port):
        sys.exit(f"preview: something is already on :{args.port}. Use --port.")
    pages = page_server(args.port, api_url)
    threading.Thread(target=pages.serve_forever, daemon=True).start()

    pusher = None
    if not args.no_push:
        pusher = spawn([sys.executable, str(ROOT / "tools" / "fake_agent.py"),
                        "--worker", api_url, "--scene", args.scene])

    page = f"http://localhost:{args.port}/#key={values['VIEW_KEY']}"
    links = "\n".join([
        "mc-status local preview (written by tools/preview.py; gitignored)",
        "",
        f"  status page   {page}",
        f"  demo fixture  http://localhost:{args.port}/?demo",
        f"  Worker        {api_url}",
        "",
        f"  invite key    {values['VIEW_KEY']}",
        f"  push token    {values['PUSH_TOKEN']}",
        "",
        "Local development values. The real ones live only in Cloudflare.",
        ""])
    LINKS.write_text(links, encoding="utf-8")

    say()
    say(f"  Status page   {page}")
    say(f"  Worker        {api_url}")
    say(f"  Sample data   {'off (--no-push)' if args.no_push else f'pushing, scene: {args.scene}'}")
    say()
    say(f"  Also in {LINKS.name}. Ctrl-C to stop.")
    say()

    try:
        while True:
            if pusher and pusher.poll() is not None:
                say("The sample data stopped; the page keeps whatever it already had.")
                pusher = None
            if worker and worker.poll() is not None:
                say("The Worker stopped.")
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        say()
    finally:
        for process in (pusher, worker):
            stop(process)
        pages.shutdown()


if __name__ == "__main__":
    main()
