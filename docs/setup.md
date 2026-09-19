# Setup

[← README](../README.md) · **Setup** · [Features](features.md) · [Server tool](server-tool.md) · [Configuration](configuration.md) · [Privacy](privacy.md) · [Architecture](architecture.md) · [Development](development.md)

About 15 minutes, all on free plans.

- [What you need](#what-you-need)
- [Start your own and deploy](#start-your-own-and-deploy)
- [Your own domain](#your-own-domain)
- [Commands for later](#commands-for-later)
- [Deploying the Worker from GitHub](#deploying-the-worker-from-github)
- [Installing without the wizard](#installing-without-the-wizard)
- [Updating](#updating)
- [Troubleshooting](#troubleshooting)

## What you need

| | |
| --- | --- |
| **Accounts** | GitHub and Cloudflare, both free |
| **On the PC you play on** | Python 3.11+ and Node.js 20+ (for `npx wrangler`) |
| **Minecraft** | Java Edition 26.2 with [Fabric Loader](https://fabricmc.net/use/installer/) and [Fabric API](https://modrinth.com/mod/fabric-api) |
| **Optional** | the [gh CLI](https://cli.github.com/), so the wizard can set repository variables and secrets itself; Java 25+ for the logout map (the Minecraft launcher's own Java is found automatically) |

## Start your own and deploy

### 1. Use the template, and clone it

Press **Use this template → Create a new repository**, then clone what it made
onto the PC you play on:

```bash
git clone https://github.com/<you>/<your-repo>.git
cd <your-repo>
```

The template copies `main` and nothing else, which is what you want: the other
branches hold *published content* rather than code — `map` the rendered world,
`shots` the frame archive, `demo` the `?demo` imagery — and they belong to
whoever published them. Yours get filled in by the tools below. (If you fork
instead, or clone this repository directly, add `--single-branch` to leave
someone else's world behind.)

While you're there, point the README's Tests badge at your own repository: it
carries `nyannoying1337/mc-status` twice, and until you change it the badge
reports this repository's runs rather than yours.

### 2. Build the mod once

In your repository on GitHub: **Actions → Release mod → Run workflow**. It builds the jar,
tags the version in `mod/gradle.properties`, and attaches the jar to a release where the
wizard can download it. Pushing a tag like `v1.4.0` does the same thing.

For a proper versioned release, push a tag matching `version` in `mod/gradle.properties`:

```bash
git tag v1.3.0
git push --tags
```

### 3. Run the wizard

```bash
python setup.py
```

It asks before each step, and it's safe to run again:

1. **Your site:** the page's URL and the name shown on it.
2. **The Worker:** deploys it with wrangler. A browser opens to log in to Cloudflare the first time.
3. **Secrets:** generates the push token and stores it as a Worker secret and in `agent/config.toml` (gitignored).
4. **GitHub:** sets the repository variables if `gh` is installed, otherwise prints them.
5. **The agent:** installs its Python packages into `agent/.venv` and starts it at login (a scheduled task on Windows, launchd on macOS, a systemd user service on Linux).
6. **The mod:** downloads the jar from your latest release into your `mods` folder.
7. **Invite:** creates the invite key and prints your invite link.

Secrets are handed straight to wrangler and never printed. `python setup.py --dry-run` shows everything it would do without changing anything.

### 4. Set two repository variables

**Settings → Secrets and variables → Actions → Variables** (repository variables, not environment variables):

| Name | Value |
| --- | --- |
| `MCS_API_URL` | your Worker's URL, e.g. `https://mc-status.<account>.workers.dev` |
| `MCS_SITE_NAME` | the name shown on the page |

The Deploy site workflow writes them into `site/js/config.js`, and stops with an error if `MCS_API_URL` is missing.

### 5. Turn on Pages

1. **Settings → Pages → Source:** GitHub Actions.
2. **Settings → Environments → github-pages → Deployment branches:** add `map`, `shots`
   and `demo` — logout maps, the frame archive and the `?demo` imagery each publish to a
   branch of their own and deploy from it. A branch that isn't listed here fails the run
   before its first step, which looks like a broken workflow rather than a missing setting.

### 6. Push and open your invite link

Push to `main`, or re-run **Actions → Deploy site**. Then open the invite link the wizard printed. The page remembers the key on that device and removes it from the address bar.

## Your own domain

- **The page:** set a custom domain under **Settings → Pages**.
- **The Worker:** enter a subdomain of a domain whose DNS is on Cloudflare as the Worker URL, e.g. `status-api.example.com`. `setup.py` writes `worker/wrangler.generated.toml` with the route and deploys with it; `worker/wrangler.toml` itself stays route-free, so forks deploy as-is.

## Commands for later

| Command | What it does |
| --- | --- |
| `python setup.py` | The guided setup again. It keeps your invite key and page name unless you say otherwise, but it does re-run all seven steps — for one job, use a command below |
| `python setup.py invite` | New invite link; everyone on the old one is disconnected |
| `python setup.py token` | New push token for the agent |
| `python setup.py deploy` | Deploy Worker changes from your PC |
| `python setup.py server` | Keys and config file for the [server tool](server-tool.md) |
| `python setup.py autostart` | Reinstall the agent's autostart |
| `python setup.py restart` | Restart the agent so it picks up pulled changes |
| `python setup.py demo-assets` | Fill `?demo` from your frame archive; force-pushes the `demo` branch, nothing to commit |
| `python setup.py worker-config` | Only write `worker/wrangler.generated.toml` |
| `python agent/agent.py --dry-run` | Print exactly what would be published, and which curses would fire |
| `python agent/agent.py --once` | Collect and push once |

Useful flags: `--rotate` (replace the push token and invite key during guided setup), `--yes` (accept every default), `--dry-run` (change nothing).

## Deploying the Worker from GitHub

Optional. Add these repository **secrets** and every push that touches `worker/` deploys it:

| Secret | Where it comes from |
| --- | --- |
| `CLOUDFLARE_API_TOKEN` | Cloudflare dashboard → API Tokens → "Edit Cloudflare Workers" template |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare dashboard, right sidebar of Workers & Pages |
| `PUSH_TOKEN`, `VIEW_KEY` | set by the wizard if `gh` is installed |
| `SERVER_PUSH_TOKEN`, `ADMIN_KEY`, `CONTROL_KEY`, `PLAYER_LINK_SECRET` | set by `setup.py server` if `gh` is installed |

Without the Cloudflare secrets the workflow skips itself (it still shows as successful), and you deploy with `python setup.py deploy`.

## Installing without the wizard

**Worker**
1. `cd worker && npx wrangler deploy` for workers.dev, or `python setup.py worker-config` then `npx wrangler deploy -c wrangler.generated.toml` for a custom domain.
2. `npx wrangler secret put PUSH_TOKEN` and `npx wrangler secret put VIEW_KEY`, with long random values.

**Agent**
1. `python -m venv agent/.venv`
2. `agent/.venv/bin/pip install -r agent/requirements.txt` (`agent\.venv\Scripts\pip` on Windows)
3. Copy `agent/config.example.toml` to `agent/config.toml` and fill in `[worker]`.
4. `agent/.venv/bin/python agent/agent.py --dry-run`, then run it with `--log-file agent/agent.log`.

**Mod**
Take the jar from your Releases, or build it with `cd mod && ./gradlew build` (Java 25). Put it in `.minecraft/mods` next to Fabric API.

## Updating

- **Pulling changes in:** a repository made from the template has no upstream link —
  that is the trade for getting `main` and nothing else — so point one at it yourself,
  once:

  ```bash
  git remote add upstream https://github.com/nyannoying1337/mc-status.git
  git pull upstream main
  ```

  Then `python setup.py deploy` if `worker/` changed. The page redeploys by itself on
  push. (A fork instead of a template copy has the **Sync fork** button, and then just
  `git pull`.)
- **The mod:** your Releases page only changes when *you* publish to it — pulling does
  not do it. After pulling mod changes, bump `version=` in `mod/gradle.properties`,
  then push a matching tag (`git tag v1.4.1 && git push origin v1.4.1`) or run
  **Actions → Release mod → Run workflow**. Then download the new jar and replace the
  old one while the game is closed.
  The agent logs a warning when the jar it can see is older than the checkout, so
  check `agent/agent.log` if a card is missing rows you expected.
- **The agent:** `python setup.py restart`. A running agent keeps executing the code it
  started with, so pulling changes does nothing until it's restarted — which looks exactly
  like a broken page while every file on disk is correct.
- **A new Minecraft version:** see [Development](development.md#updating-to-a-new-minecraft-version).

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| The mod jar isn't on your Releases page | Syncing a fork doesn't publish one, and **Build mod** only makes an expiring workflow artifact. Publish with **Actions → Release mod → Run workflow**, or push a tag matching `version=` in `mod/gradle.properties`. |
| Deploy site fails with "Set the MCS_API_URL repository variable" | Add the repository variable (step 4). It must be a *repository* variable, not an environment variable. |
| Deploy site fails in seconds having run **no steps**, after a push to `map`, `shots` or `demo` | That branch isn't in the github-pages environment's Deployment branches, so the deployment is refused before the job starts. Add it (step 5) and re-run; nothing needs pushing again. |
| The page says "Nothing reported yet" | The agent hasn't pushed. Run `python agent/agent.py --once` and read the error. |
| The agent's pushes are refused (HTTP 401 in `agent/agent.log`) | The push token in `agent/config.toml` doesn't match the Worker's. Run `python setup.py token`. |
| "That key didn't work" | The invite key was replaced with `setup.py invite`. Use the new link. |
| "The machine went quiet" | No push for 90 seconds: the agent stopped or the PC is off. Check `agent/agent.log`. |
| No frame or panorama | The mod isn't installed or loaded. Check that `.minecraft/mc-status/latest.png` is being written. |
| Map button missing | No logout map rendered yet, or `map.render_on_logout` is off, or `map` isn't allowed in the github-pages environment. |
| wrangler asks to paste a secret | Some terminals make wrangler ignore piped input. The wizard puts the value on your clipboard: paste, press Enter, and it clears the clipboard afterwards. |
