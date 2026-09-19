# Development

[← README](../README.md) · [Setup](setup.md) · [Features](features.md) · [Server tool](server-tool.md) · [Configuration](configuration.md) · [Privacy](privacy.md) · [Architecture](architecture.md) · **Development**

- [Tests](#tests)
- [Running it locally](#running-it-locally)
- [Building the mod](#building-the-mod)
- [Releasing](#releasing)
- [Updating to a new Minecraft version](#updating-to-a-new-minecraft-version)
- [Minecraft assets](#minecraft-assets)
- [Conventions](#conventions)

## Tests

No Minecraft or Cloudflare needed: RCON, the Worker's storage and subprocesses are faked. Run the Python tests with the agent's environment (`agent/.venv`, after `pip install -r agent/requirements.txt`).

```bash
python tests/test_agent_mod.py     # mod source, logout render, panorama, play time, checklists, privacy
python tests/test_curses.py        # curse rules over RCON
python tests/test_events.py        # the day's timeline: sessions, milestones, restarts
python tests/test_archive.py       # frame archive: thinning, privacy, pruning, staging
python tests/test_render.py        # map render: noticing a new world, starting clean
python tests/test_icons.py         # item icon renderer, over a synthetic client jar
node tests/test_worker.mjs         # Worker routes, broadcasts, panorama, map markers
node tests/test_server_worker.mjs  # server tool: keys, per-viewer filtering, write throttling
python setup.py --dry-run --yes --site-url https://example.github.io/mc-status/ --worker-url https://mc-status.example.workers.dev
```

All of them run in the **Tests** workflow on every push and pull request. See also [`tests/README.md`](../tests/README.md).

**WebSockets against the real runtime** (invite key, 10-viewer cap, broadcasts):

```bash
cd worker && npx wrangler dev --port 8788   # with test secrets in worker/.dev.vars
node tests/live_socket_test.mjs              # in another terminal
```

## Running it locally

**Worker.** Put test values in `worker/.dev.vars` (gitignored):

```
PUSH_TOKEN=local-push-token
VIEW_KEY=local-view-key
SERVER_PUSH_TOKEN=local-server-token
ADMIN_KEY=local-admin-key
CONTROL_KEY=local-control-key
PLAYER_LINK_SECRET=local-link-secret
```

then `cd worker && npx wrangler dev --port 8788`.

**Page.** `site/js/config.js` points at the example Worker by default. For a local preview, serve `site/` with any static server and change `API_URL` to `http://127.0.0.1:8788` (don't commit that change; the deploy rewrites the file anyway). Open `http://localhost:<port>/#key=local-view-key`.

**Agent.** Point a config at the local Worker and push once:

```bash
python agent/agent.py --config agent/local.toml --once
```

**Server tool.** `cd mod && ./gradlew runServer` starts a dedicated Fabric server from the source, in `mod/run/` (gitignored). Accept the EULA in `mod/run/eula.txt`, set `online-mode=false` in `server.properties` for offline dev clients, and fill in `mod/run/config/mc-status-server.properties` with `worker_url=http://127.0.0.1:8788` and the `SERVER_PUSH_TOKEN` from `.dev.vars`. Serve the page and open `server.html#key=local-admin-key`.

## Building the mod

Java 25. From `mod/`:

```bash
./gradlew build          # jar in mod/build/libs/
./gradlew runClient      # a dev client with the mod
./gradlew runServer      # a dev server with the mod
```

The **Build mod** workflow builds every change under `mod/`.

## Releasing

1. Bump `version` in `mod/gradle.properties`.
2. Commit and push.
3. Tag and push the tag:
   ```bash
   git tag v1.3.0
   git push --tags
   ```

The **Release mod** workflow builds the jar and attaches it to a GitHub release. `setup.py` downloads the latest release when it installs the mod.

## Updating to a new Minecraft version

1. **Mod:** update `minecraft_version`, `loader_version`, `loom_version` and `fabric_api_version` in `mod/gradle.properties` (current values on [fabricmc.net/develop](https://fabricmc.net/develop)), then fix what no longer compiles.
2. **Page assets:** bump `MC_VERSION` in `site/build_assets.py`.
3. **RCON durability table:** regenerate `agent/max_durability.json` with `agent/update_durability.py`.
4. **Map:** check whether BlueMap needs a newer `BLUEMAP_VERSION` in `map/render.py`, and check the live markers afterwards.
5. **Checklists** need no list update: they're detected from each advancement's structure.

## Minecraft assets

The page draws real item icons, HUD sprites and the inventory screen. The [Minecraft Usage Guidelines](https://www.minecraft.net/en-us/usage-guidelines) allow that for fan sites that:

- **show the disclaimer:** "NOT AN OFFICIAL MINECRAFT WEBSITE. NOT APPROVED BY OR ASSOCIATED WITH MOJANG OR MICROSOFT." It's in the page footer and on the map; keep it if you restyle either;
- **don't look official;**
- **don't redistribute game files.** Nothing from the game is committed. At deploy, `site/build_assets.py` downloads the client jar from Mojang (checksum-verified) and renders what the page needs into the gitignored `site/assets/mc/`. For local previews: `pip install Pillow && python site/build_assets.py`.

## Conventions

- **Allowlists, not blocklists,** for anything published: add a field to `PUBLISHED_PLAYER_KEYS` in `agent/collect.py` on purpose, and to `SINGLEPLAYER_ONLY_KEYS` if servers mustn't see it.
- **Nothing on the render thread** that can run elsewhere; the mod's capture budget is about 1 ms.
- **No build step for the page.** Plain ES modules; the only generated file is `site/js/config.js`.
- **Stay inside free tiers.** Check a change's effect on requests and storage writes per day ([Staying free](privacy.md#staying-free)).
- **Secrets never touch the command line or the screen.** `setup.py` hands them to wrangler through stdin, falling back to the clipboard.
