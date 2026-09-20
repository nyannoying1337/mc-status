# tests

No Minecraft or Cloudflare needed; RCON, the Worker's storage and subprocesses are faked.
Run them with the agent's Python (`agent/.venv`) after `pip install -r agent/requirements.txt`.

```bash
python tests/test_curses.py      # curse rules over RCON
python tests/test_events.py      # the day's timeline: sessions, milestones, restarts
python tests/test_archive.py     # frame archive: thinning, privacy, pruning, staging
python tests/test_render.py      # map render: noticing a new world, starting clean
python tests/test_agent_mod.py   # mod source, logout render, panorama, play time, checklists, privacy
python tests/test_icons.py       # item icon renderer, over a synthetic client jar
python tests/test_tray.py        # tray icon: wording, colour, and the no-console-window flags
node tests/test_worker.mjs       # Worker routes, broadcasts, panorama, map markers
node tests/test_server_worker.mjs  # server tool: keys, per-viewer filtering, write throttling
```

More in [docs/development.md](../docs/development.md).

The WebSocket side (invite key, 10-viewer cap, broadcasts) needs the real Workers
runtime. Copy `worker/.dev.vars.example` to `worker/.dev.vars` (gitignored), then run
`cd worker && npx wrangler dev --port 8788` and, in another terminal,
`node tests/live_socket_test.mjs`.

To look at the page itself rather than assert on it, `python tools/preview.py` runs
the Worker, the site and sample data together — no Minecraft needed. See
[running it locally](../docs/development.md#running-it-locally).
