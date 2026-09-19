#!/usr/bin/env python3
"""mc-status setup: everything between "I forked the repo" and "it's live".

    python setup.py                 guided setup; safe to run again
    python setup.py invite          new invite key and link (old links stop working)
    python setup.py token           new push token for the agent
    python setup.py deploy          deploy the Worker from this machine
    python setup.py worker-config   write worker/wrangler.generated.toml (used by CI)
    python setup.py autostart       start the agent when you log in
    python setup.py restart         restart the agent after pulling changes
    python setup.py demo-assets     fill site/demo/ from your frame archive, for ?demo
    python setup.py server          optional server tool: keys, and the server mod's config file

Secrets are generated here and handed straight to wrangler (and to GitHub if
the gh CLI is installed). They're never printed; the push token is also kept in
agent/config.toml, which is gitignored.

Needs Python 3.11+. Deploying from this machine also needs Node.js (for npx).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
AGENT = ROOT / "agent"
WORKER = ROOT / "worker"
CONFIG = AGENT / "config.toml"
EXAMPLE = AGENT / "config.example.toml"
WRANGLER = ["npx", "--yes", "wrangler@4"]
TASK_NAME = "mc-status agent"

DRY_RUN = False
ASSUME_YES = False

# never crash on a console that can't show a character
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")


# ---------------------------------------------------------------- terminal helpers

def say(text: str = "") -> None:
    print(text, flush=True)


def step(title: str) -> None:
    say(f"\n== {title}")


def ask(question: str, default: str = "") -> str:
    if ASSUME_YES:
        return default
    suffix = f" [{default}]" if default else ""
    answer = input(f"{question}{suffix}: ").strip()
    return answer or default


def confirm(question: str, default: bool = True) -> bool:
    if ASSUME_YES:
        return default
    answer = input(f"{question} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    return default if not answer else answer.startswith("y")


def run(command: list[str], *, cwd: Path | None = None, input_text: str | None = None, capture: bool = False):
    if DRY_RUN:
        say(f"  (dry run) would run: {' '.join(command)}")
        return subprocess.CompletedProcess(command, 0, "", "")
    executable = shutil.which(command[0])
    if not executable:
        raise FileNotFoundError(command[0])
    # wrangler prints UTF-8 (emoji, box drawing); the console's code page can't decode that
    return subprocess.run([executable, *command[1:]], cwd=cwd, input=input_text, text=True,
                          encoding="utf-8", errors="replace", capture_output=capture)


def copy_to_clipboard(text: str) -> bool:
    for command in (["clip"], ["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(command[0]):
            try:
                subprocess.run(command, input=text, text=True, check=True)
                return True
            except (OSError, subprocess.CalledProcessError):
                continue
    return False


# ---------------------------------------------------------------- config.toml

def load_config() -> dict:
    if not CONFIG.is_file():
        return {}
    with CONFIG.open("rb") as handle:
        return tomllib.load(handle)


def set_config_value(section: str, key: str, value: str) -> None:
    """Change one `key = "value"` line inside [section], keeping every comment."""
    if DRY_RUN and CONFIG.is_file():
        say(f"  (dry run) would set [{section}] {key} in {CONFIG.relative_to(ROOT)}")
        return
    if not CONFIG.is_file():
        shutil.copyfile(EXAMPLE, CONFIG)
    text = CONFIG.read_text(encoding="utf-8")
    pattern = re.compile(rf'(^\[{re.escape(section)}\][^\[]*?^{re.escape(key)}\s*=\s*)"[^"]*"', re.M | re.S)
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    if pattern.search(text):
        text = pattern.sub(lambda match: f'{match.group(1)}"{escaped}"', text, count=1)
    elif re.search(rf"^\[{re.escape(section)}\]", text, re.M):
        text = re.sub(rf"(^\[{re.escape(section)}\]\n)", lambda m: f'{m.group(1)}{key} = "{escaped}"\n', text, count=1, flags=re.M)
    else:
        text = f'[{section}]\n{key} = "{escaped}"\n\n' + text
    CONFIG.write_text(text, encoding="utf-8")


def placeholder(value: str | None) -> bool:
    return not value or "change-me" in value or "your-" in value or "example" in value


def configured(config: dict) -> bool:
    """Has setup finished here before? Then its secrets are live and replacing one costs something."""
    worker = config.get("worker", {})
    return not placeholder(worker.get("url")) and not placeholder(worker.get("token"))


# ---------------------------------------------------------------- GitHub

def repo_slug() -> str | None:
    try:
        url = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except OSError:
        return None
    match = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
    return match.group(1) if match else None


def gh_ready() -> bool:
    if DRY_RUN or not shutil.which("gh"):
        return False
    return subprocess.run([shutil.which("gh"), "auth", "status"], capture_output=True).returncode == 0


def github_secret(name: str, value: str) -> bool:
    if not gh_ready():
        return False
    return run(["gh", "secret", "set", name], cwd=ROOT, input_text=value, capture=True).returncode == 0


def github_variable(name: str, value: str) -> bool:
    if not gh_ready():
        return False
    return run(["gh", "variable", "set", name, "--body", value], cwd=ROOT, capture=True).returncode == 0


# ---------------------------------------------------------------- the Worker

def worker_domain(api_url: str) -> str | None:
    """A custom domain gets a route; *.workers.dev needs none."""
    host = urlparse(api_url).hostname or ""
    return None if not host or host.endswith(".workers.dev") or placeholder(api_url) else host


def write_worker_config(api_url: str) -> Path:
    base = (WORKER / "wrangler.toml").read_text(encoding="utf-8")
    domain = worker_domain(api_url)
    if domain:
        route = f'routes = [{{ pattern = "{domain}", custom_domain = true }}]\n'
        base = re.sub(r"^(compatibility_date = .*\n)", lambda m: m.group(1) + route, base, count=1, flags=re.M)
    target = WORKER / "wrangler.generated.toml"
    target.write_text("# Generated by setup.py from wrangler.toml; don't edit.\n" + base, encoding="utf-8")
    return target


def deploy_worker(api_url: str) -> str | None:
    """Deploy with wrangler (it opens a browser to log in the first time).
    Returns the workers.dev URL wrangler reports, if any."""
    generated = write_worker_config(api_url)
    say(f"Deploying the Worker{' to ' + worker_domain(api_url) if worker_domain(api_url) else ''}...")
    result = run([*WRANGLER, "deploy", "-c", generated.name], cwd=WORKER, capture=True)
    output = (result.stdout or "") + (result.stderr or "")
    say(output.strip()[-1500:])
    if result.returncode:
        raise SystemExit("wrangler deploy failed; see the output above")
    match = re.search(r"https://[\w.-]+\.workers\.dev", output)
    return match.group(0) if match else None


def worker_secret(name: str, value: str) -> None:
    """Store a Worker secret without it touching the command line or the screen."""
    say(f"Storing {name} as a Worker secret...")
    generated = write_worker_config(load_config().get("worker", {}).get("url", ""))
    result = run([*WRANGLER, "secret", "put", name, "-c", generated.name], cwd=WORKER, input_text=value, capture=True)
    if result.returncode == 0:
        return
    # Some terminals make wrangler treat piped input as "non-interactive" and
    # ignore the browser login. Then it asks, and you paste from the clipboard.
    if not copy_to_clipboard(value):
        raise SystemExit(f"wrangler couldn't read {name} from stdin and there's no clipboard tool to fall back on")
    say(f"\n{name} is on your clipboard. When wrangler asks for the secret value: paste (Ctrl+V / Cmd+V), then Enter.")
    try:
        if run([*WRANGLER, "secret", "put", name, "-c", generated.name], cwd=WORKER).returncode:
            raise SystemExit(f"wrangler secret put {name} failed")
    finally:
        copy_to_clipboard(" ")
        say("Clipboard cleared.")


def check_worker(api_url: str, token: str) -> bool:
    """A harmless keyed read: 401 means the Worker isn't set up with these secrets yet."""
    if DRY_RUN:
        return True
    request = urllib.request.Request(f"{api_url.rstrip('/')}/status", method="POST", data=b"not json",
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(request, timeout=15)
    except urllib.error.HTTPError as err:
        return err.code == 400  # token accepted, body refused: nothing was changed
    except OSError:
        return False
    return True


# ---------------------------------------------------------------- the mod and the agent

def install_agent_requirements() -> None:
    venv = AGENT / ".venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        say("Creating agent/.venv...")
        run([sys.executable, "-m", "venv", str(venv)])
    run([str(python), "-m", "pip", "install", "--quiet", "--upgrade", "-r", str(AGENT / "requirements.txt")])


def agent_python(windowless: bool = False) -> Path:
    venv = AGENT / ".venv"
    if os.name == "nt":
        return venv / "Scripts" / ("pythonw.exe" if windowless else "python.exe")
    return venv / "bin" / "python"


def install_autostart() -> None:
    if sys.platform == "win32":
        run(["powershell", "-ExecutionPolicy", "Bypass", "-File", str(AGENT / "install-windows.ps1")])
        return
    agent = AGENT / "agent.py"
    log = AGENT / "agent.log"
    if sys.platform == "darwin":
        plist = Path.home() / "Library/LaunchAgents/io.github.mc-status.agent.plist"
        body = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>io.github.mc-status.agent</string>
  <key>ProgramArguments</key><array>
    <string>{agent_python()}</string><string>{agent}</string><string>--config</string><string>{CONFIG}</string>
    <string>--log-file</string><string>{log}</string>
  </array>
  <key>WorkingDirectory</key><string>{AGENT}</string>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
</dict></plist>
"""
        if DRY_RUN:
            say(f"  (dry run) would write {plist}")
            return
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text(body, encoding="utf-8")
        run(["launchctl", "unload", str(plist)], capture=True)
        run(["launchctl", "load", str(plist)])
        say(f"Installed {plist}")
        return
    unit = Path.home() / ".config/systemd/user/mc-status-agent.service"
    body = f"""[Unit]
Description=mc-status agent
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory={AGENT}
ExecStart={agent_python()} {agent} --config {CONFIG} --log-file {log}
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
"""
    if DRY_RUN:
        say(f"  (dry run) would write {unit}")
        return
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(body, encoding="utf-8")
    run(["systemctl", "--user", "daemon-reload"])
    run(["systemctl", "--user", "enable", "--now", "mc-status-agent.service"])
    say(f"Installed {unit}")


DEMO_DIR = ROOT / "site" / "demo"
DEMO_FRAMES = 8
DEMO_FULL_WIDTH, DEMO_THUMB_WIDTH = 640, 320
DEMO_PANORAMA_WIDTH = 1536  # a 6:1 strip; panorama.js slices it into six faces
DEMO_BUDGET = 400 * 1024  # keep the branch small; it is force-pushed whole every time
DEMO_BRANCH = "demo"


def _demo_days(root: Path) -> list[Path]:
    return sorted(d for d in root.iterdir()
                  if d.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d.name))


def _blank(path: Path) -> bool:
    """A frame with almost no variation: a loading screen, or a capture taken in the dark."""
    from PIL import Image
    try:
        with Image.open(path) as image:
            # draft() lets the JPEG decoder skip most of its work; this only needs to
            # know whether the picture is flat, and the archive can hold hundreds.
            image.draft("L", (32, 32))
            low, high = image.convert("L").getextrema()
    except OSError:
        return True  # unreadable is no more use than blank
    return high - low < 8


def _spread(items: list, count: int) -> list:
    """`count` items spread evenly across the list, ends included.

    The newest frames are minutes apart and look nearly identical; a demo wants the
    scrubber to show the day going by.
    """
    if len(items) <= count:
        return items
    return [items[round(i * (len(items) - 1) / (count - 1))] for i in range(count)]


def build_demo_assets() -> None:
    """Fill site/demo/ from your own frame archive, so ?demo showcases a real world.

    Images only. The demo page keeps its own invented coordinates and timestamps, so
    what lands here can't say where you actually play — the archive's day.json, which
    is the file that holds positions, is never read.
    """
    try:
        from PIL import Image
    except ImportError:
        # Pillow lives in the agent's venv, not necessarily in whatever python you
        # typed. Re-run there rather than making you work out the path — which is
        # Scripts\python.exe on Windows and bin/python everywhere else.
        python = agent_python()
        # sys.prefix, not the executable path: a venv's python is usually a symlink to
        # the system one, so comparing resolved paths would say we are already inside
        # it and skip the re-run. sys.prefix is the venv directory when we really are.
        inside = Path(sys.prefix).resolve() == (AGENT / ".venv").resolve()
        if python.is_file() and not inside:
            say(f"Pillow isn't in this Python; re-running with {python}")
            arguments = [str(python), __file__, "demo-assets"]
            if DRY_RUN:
                arguments.append("--dry-run")
            raise SystemExit(subprocess.run(arguments, cwd=ROOT).returncode)
        say("Pillow isn't installed, and agent/.venv can't supply it.")
        say("Run `python setup.py` and say yes at step 5, which installs the agent's packages.")
        return

    config = load_config()
    raw = config.get("shots", {}).get("directory")
    root = Path(os.path.expandvars(raw)).expanduser() if raw else AGENT / "shots"
    if not root.is_dir():
        say(f"No frame archive at {root}.")
        say("Set [shots] keep = true in agent/config.toml, restart the agent, and play a while.")
        return

    found = sorted(path for day in _demo_days(root)
                   for path in day.glob("*.jpg") if not path.name.endswith(".t.jpg"))
    # The first capture of a session is often taken before the world has drawn, so it
    # is solid black — and being the oldest, it lands at the head of the strip where
    # it is the first thing anyone sees.
    frames = [path for path in found if not _blank(path)]
    if len(found) - len(frames):
        say(f"Skipped {len(found) - len(frames)} blank frame(s) — captured before the world drew.")
    if len(frames) < DEMO_FRAMES:
        say(f"Only {len(frames)} usable frame(s) in {root}; the demo scrubber wants {DEMO_FRAMES}.")
        say("[shots] every_seconds defaults to 300, so ten minutes is two frames. Set it to")
        say("60, run `python setup.py restart`, and play for ten minutes.")
        return
    chosen = _spread(frames, DEMO_FRAMES)

    panoramas = sorted((root / "pano").glob("*.jpg")) if (root / "pano").is_dir() else []
    if not panoramas:
        say("No panorama in the archive yet — one is written when you Save & Quit a")
        say("singleplayer world. ?demo=offline will keep the old one until then.")

    def write(image, path: Path, width: int) -> int:
        if image.width > width:
            image = image.resize((width, round(image.height * width / image.width)), Image.LANCZOS)
        if DRY_RUN:
            return 0
        image.convert("RGB").save(path, format="WEBP", quality=80, method=6)
        return path.stat().st_size

    if not DRY_RUN:
        DEMO_DIR.mkdir(parents=True, exist_ok=True)
    total = 0
    for index, source in enumerate(chosen):
        with Image.open(source) as image:
            total += write(image, DEMO_DIR / f"frame-{index}.webp", DEMO_FULL_WIDTH)
            total += write(image, DEMO_DIR / f"frame-{index}.t.webp", DEMO_THUMB_WIDTH)
    if panoramas:
        with Image.open(panoramas[-1]) as image:
            total += write(image, DEMO_DIR / "panorama.webp", DEMO_PANORAMA_WIDTH)

    if DRY_RUN:
        say(f"  (dry run) would write {len(chosen)} frames + thumbnails"
            + (" and a panorama" if panoramas else "") + f" to {DEMO_DIR.relative_to(ROOT)}")
        return
    say(f"Wrote {len(chosen) * 2 + len(panoramas[-1:])} files to "
        f"{DEMO_DIR.relative_to(ROOT)} ({total // 1024} KB).")
    if total > DEMO_BUDGET:
        say(f"That's over the {DEMO_BUDGET // 1024} KB this is meant to stay under.")
        say("The branch is force-pushed whole each time, so trim them first.")
    publish_demo_assets()


def publish_demo_assets(remote: str = "origin") -> None:
    """Force-push site/demo/ to the `demo` branch, the way archive.py publishes frames.

    These are pictures of your world. Keeping them off main means someone who forks
    this to run their own page gets a clean checkout instead of your screenshots, and
    pages.yml skips the branch entirely on a fork so their site never serves them.
    """
    try:
        url = subprocess.run(["git", "remote", "get-url", remote], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError) as err:
        say(f"No git remote to publish to ({err}); site/demo/ is written but not pushed.")
        return
    if DRY_RUN:
        say(f"  (dry run) would force-push site/demo/ to the {DEMO_BRANCH} branch")
        return

    work = DEMO_DIR.parent / ".demo-publish"
    shutil.rmtree(work, ignore_errors=True)
    try:
        shutil.copytree(DEMO_DIR, work)
        (work / ".nojekyll").touch()
        # GitHub only runs workflows that exist in the branch that was pushed, so the
        # branch carries a copy of the deploy — same reason the shots branch does.
        workflow = ROOT / ".github" / "workflows" / "pages.yml"
        if workflow.is_file():
            (work / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
            shutil.copy2(workflow, work / ".github" / "workflows" / "pages.yml")
        for command in (
                ["git", "init", "-q", "-b", DEMO_BRANCH, "."],
                ["git", "add", "-A"],
                ["git", "-c", "user.name=mc-status", "-c", "user.email=mc-status@localhost",
                 "commit", "-q", "-m", "demo imagery"],
                ["git", "push", "--force", url, f"{DEMO_BRANCH}:{DEMO_BRANCH}"]):
            subprocess.run(command, cwd=work, check=True, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError, shutil.Error) as err:
        say(f"Couldn't push the {DEMO_BRANCH} branch: {err}")
        return
    finally:
        shutil.rmtree(work, ignore_errors=True)
    say(f"Pushed them to the {DEMO_BRANCH} branch. The deploy picks them up; nothing to commit.")


WINDOWS_TASK = "mc-status agent"
LAUNCHD_LABEL = "io.github.mc-status.agent"
SYSTEMD_UNIT = "mc-status-agent.service"


def restart_agent() -> None:
    """Restart the agent, however this machine starts it.

    A running agent keeps executing the code it started with, so pulling changes
    does nothing until it is restarted — which is its own quiet class of bug: the
    page looks broken while every file on disk is correct.
    """
    try:
        if sys.platform == "win32":
            run(["schtasks", "/End", "/TN", WINDOWS_TASK], capture=True)  # not running is fine
            done = run(["schtasks", "/Run", "/TN", WINDOWS_TASK], capture=True)
        elif sys.platform == "darwin":
            done = run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"], capture=True)
        else:
            done = run(["systemctl", "--user", "restart", SYSTEMD_UNIT], capture=True)
    except FileNotFoundError as err:
        say(f"Couldn't restart the agent: {err} isn't on PATH. Stop and start it by hand.")
        return
    if DRY_RUN:
        return
    if done.returncode == 0:
        say("Restarted the agent. It's now running the code that's on disk.")
        say(f"Check {AGENT / 'agent.log'} if a card still looks wrong — an out-of-date mod jar is logged there.")
    else:
        say("Couldn't restart the agent — it may not be set to start automatically.")
        say("Run `python setup.py autostart` to set that up, or start agent.py by hand.")
        if (done.stderr or done.stdout or "").strip():
            say((done.stderr or done.stdout).strip().splitlines()[0])


def default_mods_dir() -> Path:
    sys.path.insert(0, str(AGENT))
    from common import default_game_dir, expand  # the agent's own idea of where the game is
    raw = load_config().get("source", {}).get("game_dir", default_game_dir())
    return expand(raw) / "mods"


def download_mod(slug: str) -> None:
    """The newest mc-status jar from this repo's GitHub Releases."""
    with urllib.request.urlopen(f"https://api.github.com/repos/{slug}/releases/latest", timeout=20) as response:
        release = json.load(response)
    jars = [asset for asset in release.get("assets", []) if asset["name"].endswith(".jar")]
    if not jars:
        say(f"The latest release ({release.get('tag_name')}) has no jar attached yet.")
        return
    jar = jars[0]
    mods = default_mods_dir()
    if not confirm(f"Download {jar['name']} ({jar['size'] // 1024} kB) from github.com/{slug} into {mods}?"):
        return
    if DRY_RUN:
        say("  (dry run) skipped the download")
        return
    mods.mkdir(parents=True, exist_ok=True)
    for old in mods.glob("mc-status-*.jar"):
        say(f"Replacing {old.name} (close Minecraft first if it's running)")
        old.unlink()
    urllib.request.urlretrieve(jar["browser_download_url"], mods / jar["name"])
    say(f"Saved {mods / jar['name']}. Fabric Loader and Fabric API need to be installed too.")


# ---------------------------------------------------------------- commands

def new_token(api_url: str, push_to_github: bool = True) -> None:
    token = secrets.token_hex(32)
    worker_secret("PUSH_TOKEN", token)
    set_config_value("worker", "token", token)
    if push_to_github and github_secret("PUSH_TOKEN", token):
        say("Also stored PUSH_TOKEN as a GitHub secret, for the Deploy Worker workflow.")
    say("Wrote the token into agent/config.toml.")
    say("The Worker accepted it." if check_worker(api_url, token) else
        "The Worker didn't accept it yet; secrets can take a minute. Check with: python agent/agent.py --once")


def new_invite(site_url: str, push_to_github: bool = True) -> None:
    key = secrets.token_urlsafe(18)
    worker_secret("VIEW_KEY", key)
    if push_to_github and github_secret("VIEW_KEY", key):
        say("Also stored VIEW_KEY as a GitHub secret, for the Deploy Worker workflow.")
    link = f"{site_url.rstrip('/')}/#key={key}"
    say("\nInvite link. Share it only with people who may watch; running `setup.py invite` again replaces it:")
    say(f"  {link}")


def new_server(api_url: str, site_url: str, server_name: str | None = None) -> None:
    """The optional server tool: its four Worker secrets, and the mod's config file for the server."""
    if not api_url:
        raise SystemExit("No Worker URL: run the guided setup first, or pass --worker-url")
    say("This creates new server tool keys. Running it again replaces them: the server needs the new")
    say("config file, and every admin and player link stops working.")
    if not confirm("Continue?"):
        return
    name = server_name or ask("Server name shown on the admin page", "Minecraft server")
    push_token = secrets.token_hex(32)
    for secret, value in (("SERVER_PUSH_TOKEN", push_token),
                          ("ADMIN_KEY", secrets.token_urlsafe(24)),
                          ("CONTROL_KEY", secrets.token_urlsafe(32)),
                          ("PLAYER_LINK_SECRET", secrets.token_urlsafe(32))):
        worker_secret(secret, value)
        if github_secret(secret, value):
            say(f"Also stored {secret} as a GitHub secret, for the Deploy Worker workflow.")
    page = f"{site_url.rstrip('/')}/server.html" if site_url else ""
    target = ROOT / "server" / "mc-status-server.properties"
    if DRY_RUN:
        say(f"  (dry run) would write {target.relative_to(ROOT)} for {page or 'the server page'}")
        return
    target.parent.mkdir(exist_ok=True)
    target.write_text(
        "# mc-status server tool. Copy into the server's config/ folder. Contains a secret: don't share it.\n"
        f"worker_url={api_url}\n"
        f"push_token={push_token}\n"
        f"site_url={page}\n"
        f"server_name={name}\n"
        "interval_seconds=30\n"
        "share_item_names=false\n"
        "# A player's view of the world on the admin page: off, control (only the\n"
        "# control key) or admin (every admin key). Only players who have the mod and\n"
        "# turned share_screen_with_server on ever send one; vanilla clients never can.\n"
        "player_screens=off\n"
        "player_screen_interval_seconds=30\n"
        "player_screen_width=480\n",
        encoding="utf-8")
    say(f"\nWrote {target.relative_to(ROOT)} (gitignored).")
    say("On the server (Fabric Loader + Fabric API, Minecraft matching the mod):")
    say("  1. put the mc-status jar in mods/")
    say(f"  2. copy {target.name} into config/")
    say("  3. start it, then in game as an op: /mcstatus admin (view) or /mcstatus control (actions, op level 4)")
    if not page:
        say("No site URL in agent/config.toml: fill in site_url yourself (<your site>/server.html).")


def guided(args) -> None:
    say(__doc__.split("\n\n")[0])
    if sys.version_info < (3, 11):
        raise SystemExit("Python 3.11 or newer is needed")
    config = load_config()
    slug = repo_slug()
    # Decide this before step 1 writes anything: from here on the config looks configured
    # whether or not it did when you started.
    returning = configured(config)
    if returning:
        say("\nThis is the full setup and it re-runs all seven steps. For one thing on its own:")
        for command, what in (
                ("invite", "a new invite link — replaces the one you've shared"),
                ("token", "a new push token for the agent"),
                ("deploy", "redeploy the Worker from this machine"),
                ("restart", "restart the agent so it picks up pulled changes"),
                ("demo-assets", "fill site/demo/ from your frame archive, for ?demo"),
                ("server", "keys for the optional server tool")):
            say(f"  python setup.py {command:<12} {what}")

    step("1/7  Your site")
    owner, _, repo = (slug or "your-name/mc-status").partition("/")
    default_site = config.get("site", {}).get("url")
    if placeholder(default_site):
        default_site = f"https://{owner.lower()}.github.io/{repo}/"
    site_url = args.site_url or ask("Status page URL (GitHub Pages address, or your own domain)", default_site)
    # Default to the name already in use. It used to default to the repo owner, so
    # running setup again and pressing Enter quietly renamed the page.
    site_name = args.site_name or ask("Name shown on the page",
                                      config.get("site", {}).get("name") or owner)
    set_config_value("site", "url", site_url)
    set_config_value("site", "name", site_name)

    step("2/7  The Worker")
    api_url = config.get("worker", {}).get("url", "")
    say("It runs on Cloudflare's free plan: either at mc-status.<your-account>.workers.dev, or at a")
    say("subdomain of a domain whose DNS is on Cloudflare (e.g. status-api.example.com).")
    answer = args.worker_url or ask("Worker URL (leave empty for workers.dev)", "" if placeholder(api_url) else api_url)
    api_url = answer.rstrip("/")
    if api_url and not api_url.startswith("http"):
        api_url = f"https://{api_url}"
    if (shutil.which("npx") or DRY_RUN) and confirm("Deploy the Worker from this machine now? (opens a browser to log in to Cloudflare)"):
        reported = deploy_worker(api_url)
        if not api_url and reported:
            api_url = reported
    elif not api_url:
        say("Deploy it once (python setup.py deploy, or the Deploy Worker workflow) and run setup again with its URL.")
    if api_url:
        set_config_value("worker", "url", api_url)

    step("3/7  Secrets")
    config = load_config()
    token = config.get("worker", {}).get("token")
    if api_url and (placeholder(token) or args.rotate or not check_worker(api_url, token)):
        new_token(api_url)
    elif api_url:
        say("The push token in agent/config.toml already works; keeping it (use --rotate to replace it).")
    # Saying yes here throws away the key every existing link uses. On a first run
    # there is nothing to lose; on a re-run the safe answer is the default one.
    invite_needed = api_url and (args.rotate or confirm(
        "Replace the invite key? Every link you have already shared stops working"
        if returning else "Create an invite key and link now?", default=not returning))

    step("4/7  GitHub")
    if slug:
        say(f"Repository: github.com/{slug}")
    pages = f"https://github.com/{slug}/settings" if slug else "your repository's settings"
    variables = {"MCS_API_URL": api_url, "MCS_SITE_NAME": site_name}
    if all(github_variable(name, value) for name, value in variables.items() if value):
        say("Set the MCS_API_URL and MCS_SITE_NAME repository variables.")
    else:
        say("Set these under Settings -> Secrets and variables -> Actions -> Variables:")
        for name, value in variables.items():
            say(f"  {name} = {value or '(the Worker URL, once deployed)'}")
    say(f"Then, in {pages}:")
    say("  Pages -> Source: GitHub Actions")
    # Every branch that publishes content into the site deploys from itself, and the
    # environment refuses a deployment from a branch that isn't on this list. Leaving
    # one off fails the run before a single step, which reads like a broken workflow.
    say("  Environments -> github-pages -> Deployment branches: add `map`, `shots` and `demo`")
    say("    (logout maps, the frame archive, and the ?demo imagery)")
    say("Optional, to deploy the Worker from GitHub: add the secrets CLOUDFLARE_API_TOKEN")
    say("(template \"Edit Cloudflare Workers\") and CLOUDFLARE_ACCOUNT_ID.")

    step("5/7  The agent")
    if confirm("Install the agent's Python packages into agent/.venv?"):
        install_agent_requirements()
    if confirm("Start the agent automatically when you log in?"):
        install_autostart()

    step("6/7  The mod")
    say("Needs Minecraft with Fabric Loader and Fabric API. The mc-status jar comes from this")
    say("repository's Releases (the Release workflow builds it when you push a tag like v1.0.4).")
    if slug and not DRY_RUN and confirm("Look for the latest release now?"):
        try:
            download_mod(slug)
        except OSError as err:
            say(f"Couldn't fetch the release: {err}")

    step("7/7  Invite")
    if invite_needed:
        new_invite(site_url)
    else:
        say("Create one any time with: python setup.py invite")
    say("\nDone. Push to main to deploy the page.")


def main() -> int:
    global DRY_RUN, ASSUME_YES
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", default="guided",
                        choices=["guided", "invite", "token", "deploy", "worker-config", "autostart", "restart", "server", "demo-assets"])
    parser.add_argument("--site-url")
    parser.add_argument("--site-name")
    parser.add_argument("--server-name", help="for `server`: the name shown on the admin page")
    parser.add_argument("--worker-url")
    parser.add_argument("--rotate", action="store_true", help="replace the push token and invite key")
    parser.add_argument("--yes", action="store_true", help="accept every default (for scripts)")
    parser.add_argument("--dry-run", action="store_true", help="write local files only; run nothing, contact nothing")
    args = parser.parse_args()
    DRY_RUN, ASSUME_YES = args.dry_run, args.yes

    api_url = (args.worker_url or os.environ.get("MCS_API_URL") or load_config().get("worker", {}).get("url", "")).rstrip("/")
    if args.command == "guided":
        guided(args)
    elif args.command == "worker-config":
        say(f"wrote {write_worker_config(api_url).relative_to(ROOT)}"
            + (f" with the route {worker_domain(api_url)}" if worker_domain(api_url) else " (workers.dev, no route)"))
    elif args.command == "deploy":
        deploy_worker(api_url)
    elif args.command == "token":
        new_token(api_url)
    elif args.command == "invite":
        new_invite(args.site_url or load_config().get("site", {}).get("url", ""))
    elif args.command == "autostart":
        install_autostart()
    elif args.command == "restart":
        restart_agent()
    elif args.command == "demo-assets":
        build_demo_assets()
    elif args.command == "server":
        new_server(api_url, args.site_url or load_config().get("site", {}).get("url", ""), args.server_name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
