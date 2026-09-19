# Configuration

[← README](../README.md) · [Setup](setup.md) · [Features](features.md) · [Server tool](server-tool.md) · **Configuration** · [Privacy](privacy.md) · [Architecture](architecture.md) · [Development](development.md)

Every setting in one place.

- [The agent: `agent/config.toml`](#the-agent-agentconfigtoml)
- [The mod: `config/mc-status.properties`](#the-mod-configmc-statusproperties)
- [The server tool: `config/mc-status-server.properties`](#the-server-tool)
- [Worker secrets](#worker-secrets)
- [GitHub variables and secrets](#github-variables-and-secrets)
- [Things you can change in code](#things-you-can-change-in-code)

## The agent: `agent/config.toml`

Created by `python setup.py` from [`agent/config.example.toml`](../agent/config.example.toml). Gitignored, because it holds the push token. Restart the agent after changing it.

### `[site]`

| Key | Default | What it does |
| --- | --- | --- |
| `url` | | your status page; only used to print invite links |
| `name` | | the name shown on the page. `setup.py` pushes it to the `MCS_SITE_NAME` repository variable and remembers it here, so running setup again doesn't rename the page |

### `[worker]`

| Key | Default | What it does |
| --- | --- | --- |
| `url` | | the Worker, e.g. `https://mc-status.<account>.workers.dev` or your own domain |
| `token` | | the Worker's `PUSH_TOKEN` secret |

### `[agent]`

| Key | Default | What it does |
| --- | --- | --- |
| `interval_seconds` | `10` | how often to push while you're in game |
| `offline_interval_seconds` | `60` | how often to push while you're not |
| `playtime_file` | `agent/playtime.json` | where time in game per day is kept |
| `events_file` | `agent/events.json` | where the day's timeline is kept; ten days stored, the last day published |

### `[source]`

| Key | Default | What it does |
| --- | --- | --- |
| `type` | `"mod"` | `"mod"`: singleplayer with the mc-status mod. `"rcon"`: a server you run, asked over RCON |
| `game_dir` | the launcher's folder | `%APPDATA%/.minecraft` on Windows, `~/Library/Application Support/minecraft` on macOS, `~/.minecraft` elsewhere |

### `[rcon]`

Only with `source.type = "rcon"`. Enable RCON in the server's `server.properties` and keep it on localhost: it's an unencrypted console.

| Key | Default | What it does |
| --- | --- | --- |
| `host` | `"127.0.0.1"` | RCON host |
| `port` | `25575` | RCON port |
| `password` | | RCON password |
| `player` | | your exact in-game name |

Without the mod there's no frame unless `screenshot.directory` is set. For logout maps, set `map.world` to the server's world folder.

### `[screenshot]`

| Key | Default | What it does |
| --- | --- | --- |
| `directory` | | with RCON: a folder to take the newest image from. With the mod, its HUD-free frame is always used and F2 screenshots are never published |
| `max_width` | `1920` | frames are scaled down to this width |
| `quality` | `82` | JPEG quality of uploaded frames |

### `[shots]`

The frame archive behind the *Looking back* card. Thirty days are kept locally
and ten are published — replacing the branch is a real delete, so the local copy
is the only undo.

| Key | Default | What it does |
| --- | --- | --- |
| `keep` | `false` | keep a thinned archive of the mod's frames on this machine |
| `publish` | `false` | push a ten-day window to the `shots` branch when you log out |
| `every_seconds` | `300` | at most one frame kept per this many seconds |
| `directory` | `agent/shots` | where the local archive lives |

### `[map]`

| Key | Default | What it does |
| --- | --- | --- |
| `render_on_logout` | `false` | render the area around your logout and publish it to the `map` branch |
| `radius_chunks` | `8` | chunks rendered in every direction |
| `accept_mojang_eula` | `false` | required: BlueMap downloads textures from your client jar, which means you accept [Mojang's EULA](https://www.minecraft.net/eula) |
| `publish` | `true` | push the render; `false` keeps it local in `map/work/` |
| `remote` | `"origin"` | git remote to push the `map` branch to |
| `java` | found automatically | Java 25+; `JAVA_HOME`, `PATH` or the Minecraft launcher's runtime |
| `world` | | with RCON: the server's world folder |

To render by hand:

```bash
python map/render.py --world "path/to/saves/My World" --center 120 -40 --accept-mojang-eula --publish
```

| `render.py` flag | What it does |
| --- | --- |
| `--world` | the world save folder (the one with `level.dat`) |
| `--center X Z` | render around this spot instead of the whole world |
| `--radius-chunks` | with `--center`: chunks in each direction (default 8) |
| `--dimension` | with `--center`: which dimension (default overworld) |
| `--force` | re-render everything, not just changed chunks |
| `--fresh` | drop the rendered tiles and the render cache first, as if this world had never been rendered |
| `--publish` | push the result to the `map` branch |
| `--publish-only` | skip rendering; push the last render with any changed map scripts |
| `--java`, `--threads`, `--remote`, `--live-url` | Java executable, render threads, git remote, Worker base URL for live markers |

### `[privacy]`

| Key | Default | What it does |
| --- | --- | --- |
| `hide_coordinates` | `false` | removes coordinates from the page, the map marker and the death marker. The logout map still renders around your position, so turn `render_on_logout` off too if that matters |
| `share_server_world` | `false` | publish coordinates and frames while you're on someone else's server. The mod has a `share_server_world` of its own and both have to be on. Statistics and advancements stay off regardless: the client can't read them on a server |

### `[cursed]`

| Key | Default | What it does |
| --- | --- | --- |
| `enabled` | `false` | run the rules below |

Each `[[cursed.rules]]`:

| Key | What it does |
| --- | --- |
| `name` | shown on the page when it fires |
| `when` | `"<metric> <op> <number>"`, e.g. `"gpu_percent >= 95"`. Operators: `>`, `>=`, `<`, `<=`, `==` |
| `cooldown_seconds` | how long before it can fire again while the condition still holds |
| `commands` | Minecraft commands; `{player}` becomes your name |

Metrics: `cpu_percent`, `gpu_percent`, `mem_percent`, `uptime_hours`, `health` (0–20), `foodlevel` (0–20), `xplevel`, and `cpu_temp`, `gpu_temp` (°C) when [fastfetch](https://github.com/fastfetch-cli/fastfetch) is installed. See [Cursed mode](features.md#cursed-mode).

### Agent command line

| Flag | What it does |
| --- | --- |
| `--config PATH` | another config file (default `agent/config.toml`) |
| `--once` | collect and push a single time |
| `--dry-run` | print the payload and which curses would fire; push nothing |
| `--log-file PATH` | also log to this file, rotated at 1 MB |

## The mod: `config/mc-status.properties`

Written in the game's `config` folder on first start. Restart the game after changing it.

| Key | Default | Range | What it does |
| --- | --- | --- | --- |
| `capture_interval_seconds` | `60` | 10+ | how often the frame updates while you play |
| `capture_width` | `1920` | 160–1920 | width the frame is scaled to on the GPU |
| `state_interval_ticks` | `20` | 5–200 | how often `state.json` is checked for changes (20 ticks = 1 s) |
| `share_item_names` | `false` | | publish custom item names, which can contain anything |
| `share_mods` | `true` | | publish the list of installed mods and their versions |
| `share_server_world` | `false` | | allow frames and coordinates from servers, where other players are in shot |

## The server tool

`config/mc-status-server.properties` on a Fabric server. See [Server tool → Settings](server-tool.md#settings).

## Worker secrets

Set with `python setup.py` (or `npx wrangler secret put <NAME>` in `worker/`). Never committed. For `wrangler dev`, put test values in `worker/.dev.vars` (gitignored).

| Secret | Set by | Used for |
| --- | --- | --- |
| `PUSH_TOKEN` | `setup.py token` | the agent's pushes |
| `VIEW_KEY` | `setup.py invite` | the invite key viewers use |
| `SERVER_PUSH_TOKEN` | `setup.py server` | the server tool's pushes and link requests |
| `ADMIN_KEY` | `setup.py server` | the server tool's admin page, look only |
| `CONTROL_KEY` | `setup.py server` | the server tool's admin page with actions and the console |
| `PLAYER_LINK_SECRET` | `setup.py server` | signing player links |

## GitHub variables and secrets

**Repository variables** (Settings → Secrets and variables → Actions → Variables):

| Variable | Required | Used by |
| --- | --- | --- |
| `MCS_API_URL` | yes | Deploy site writes it into `site/js/config.js`; Deploy Worker adds a route for a custom domain |
| `MCS_SITE_NAME` | no | the name shown on the page |

**Repository secrets**, only to deploy the Worker from GitHub:

| Secret | Used by |
| --- | --- |
| `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | Deploy Worker; without them it skips itself |
| `PUSH_TOKEN`, `VIEW_KEY`, `SERVER_PUSH_TOKEN`, `ADMIN_KEY`, `CONTROL_KEY`, `PLAYER_LINK_SECRET` | copied to the Worker on each deploy, if present |

## Things you can change in code

| What | Where |
| --- | --- |
| Most viewers at once (10) | `MAX_VIEWERS` in `worker/worker.js` (and `worker/server.js` for the server tool) |
| When the page calls the machine quiet (90 s) | `STALE_MS` in `worker/worker.js` |
| BlueMap version | `BLUEMAP_VERSION` in `map/render.py` |
| Minecraft version of the page's icons | `MC_VERSION` in `site/build_assets.py` |
| The page's colours and layout | `site/style.css` |
