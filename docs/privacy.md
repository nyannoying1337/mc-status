# Privacy and security

[← README](../README.md) · [Setup](setup.md) · [Features](features.md) · [Server tool](server-tool.md) · [Configuration](configuration.md) · **Privacy** · [Architecture](architecture.md) · [Development](development.md)

- [What's published](#whats-published)
- [Never read or sent](#never-read-or-sent)
- [Your choices](#your-choices)
- [Who can watch](#who-can-watch)
- [What isn't protected](#what-isnt-protected)
- [How data travels and where it's stored](#how-data-travels-and-where-its-stored)
- [Third parties](#third-parties)
- [Staying free](#staying-free)

## What's published

What the page shows, nothing more. The agent builds every push from an explicit allowlist of fields (`PUBLISHED_PLAYER_KEYS` in `agent/collect.py`), so a Minecraft or mod update can't quietly start publishing something new. `python agent/agent.py --dry-run` prints exactly what would be sent.

On someone else's server, the mod doesn't collect coordinates, world facts, statistics or advancements, and the agent drops them again as a second line of defence. See [Multiplayer](features.md#multiplayer).

One thing travels the other way, and only if you switch it on: with `share_screen_with_server`, a server you join may ask for your view of the world for its own admin page. That is the only setting in this project that publishes anything of yours to somebody else's page rather than your own, which is why it is off by default and why you are told in chat the first time a server actually asks. See [A player's view of the world](server-tool.md#a-players-view-of-the-world).

## Never read or sent

- your local IP, hostname and user name;
- your actual screen: frames are taken before the game draws any interface, so no
  capture on your own page or a server's can contain chat, the HUD, an open
  screen, another window, or anything outside the game;
- the world seed and the world's folder path;
- chat, and the addresses of servers you join;
- F2 screenshots (the mod's HUD-free frame is used instead);
- custom item names, unless `share_item_names` is on;
- your mod list, if you turn `share_mods` off;
- anything from a server you join, unless `share_server_world` is on in both the
  mod and the agent — and even then only where you are and what you see, never
  the server's address;
- that server's statistics and advancements at all: the client can't read them
  without asking the server for them, and it never asks;
- any field that isn't on the allowlist.

## Your choices

| What | Default | Change it with |
| --- | --- | --- |
| Coordinates on the page and map markers | shown | `privacy.hide_coordinates = true` |
| The logout map (shows builds around you) | off | `map.render_on_logout` |
| The logout panorama | on, after singleplayer sessions only | no switch yet; the agent never uploads it after multiplayer |
| World name, biome, statistics, hardware names | shown, your own worlds only | edit `site/js/cards.js` |
| Custom item names | hidden | `share_item_names` in the mod's settings |
| Installed mods | shown | `share_mods` in the mod's settings |
| Coordinates and frames on someone's server | hidden | `share_server_world`, in **both** the mod's settings and the agent's `[privacy]` |
| Your view of the world on a server's admin page | off | `share_screen_with_server` in the mod's settings; the server must also have `player_screens` on |

## Who can watch

- **Invite key only.** Live data, the frame and the panorama all need the key. The Worker checks it before a WebSocket reaches storage. Visitors without it see a form, and nothing else.
- **The key stays out of logs.** Invite links put it in the URL fragment (`#key=…`), which browsers never send to GitHub Pages. The page stores it on that device and removes it from the address bar.
- **At most 10 viewers at once.** Hidden tabs give their slot back after a minute.
- **Revoking:** `python setup.py invite` replaces the key. Anyone still watching with the old one is disconnected at the agent's next push (within a minute), not just on their next visit.
- **Comparisons are constant time,** so keys can't be guessed character by character.
- **The server tool** has its own keys: a control key that can act on the server, an admin key that can only look, and player links that each open one player's page. Treat the control key like the server console's password. See [Server tool → How keys work](server-tool.md#how-keys-work).
- **A player's view of the world** is a separate decision from the rest of the admin page: `player_screens=control` keeps frames with whoever holds the control key, and `admin` lets every admin key see them. Either way only players who opted in on their own client are ever asked, and each player can always see their own.

## What isn't protected

- **The map tiles** under `<your site>/map`. They're static files in your public repository's `map` branch, so anyone who finds the URL sees the terrain around your last logout, without markers (markers need the key).
- **The page's code and assets,** which are public like the rest of the repository.

## How data travels and where it's stored

- **In transit:** everything uses HTTPS and secure WebSockets (TLS): agent to Worker, server mod to Worker, Worker to browsers. Nothing is sent unencrypted.
- **The push token** is sent in the `Authorization` header, never in a URL.
- **Stored:** the latest status, frame and panorama sit in a SQLite-backed Durable Object in your Cloudflare account, encrypted at rest by Cloudflare. Only the latest of each is kept; there's no history.
- **Secrets** (tokens and keys) are Cloudflare Worker secrets, not stored in the database or the repository. Player links aren't stored anywhere: they're verified by recomputing their signature.
- **On your PC:** `agent/config.toml` holds the push token and is gitignored. `server/mc-status-server.properties` holds the server tool's token and is gitignored too.
- **Your PC accepts nothing.** The agent only makes outgoing requests; there's no open port.

## Third parties

What visitors' browsers talk to besides your page and Worker:

- **mc-heads.net** for player skins. The page falls back to the game's player head if it's unreachable.
- **Google Fonts** for the Noto Sans typeface.

The Minecraft icons and sprites are rendered at deploy time from Mojang's client jar and served from your own site; nothing from the game is committed to the repository. The page and map show the disclaimer the [Minecraft Usage Guidelines](https://www.minecraft.net/en-us/usage-guidelines) require.

## Staying free

Everything runs on free plans. Going over a limit makes requests fail until 00:00 UTC; nothing is ever billed.

| Service | Free limit | This uses |
| --- | --- | --- |
| Workers requests | 100,000 a day | the agent pushes about 8,600 a day if you play all day, 1,440 while away; each viewer costs about one request per visit |
| Durable Object storage writes | 100,000 a day | one per push, plus one per frame and one per logout panorama; the server tool adds one per changed player per push |
| GitHub Actions | unlimited on public repositories | one deploy per push to `main` or logout render |
| GitHub Pages | 1 GB site, 100 GB a month | about 10 MB for the page and map |

**Why a Durable Object and not KV:** KV's free plan allows 1,000 writes a day. An agent pushing every 10 seconds would use that up in under three hours.

**Why viewers don't poll:** the page and the map each open one WebSocket, and every push is broadcast over it. Opening the socket costs one request; messages to viewers and keep-alive pings are free. The panorama is only fetched by viewers who see it, and then cached.
