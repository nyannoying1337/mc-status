// Loaded by the BlueMap webapp via webapp.conf `scripts`.
//
// BlueMap polls <live-data-root>/<map>/live/players.json every second. Here
// that polling is switched off entirely: the player marker comes over the same
// WebSocket the status page uses, which the Worker pushes to as the agent
// reports — one request per visit instead of one per second.
//
// Live data is invite-only. The key is the one the status page stored after
// opening an invite link (same site, so it's shared). Without it the map still
// shows the rendered area, just no marker.
//
// This reaches into BlueMap's marker managers, which aren't a public API:
// checked against BlueMap 5.24, the version map/render.py pins.
(() => {
  const KEY_STORAGE = "mc-status-invite";
  const HIDDEN_GRACE_MS = 60000;

  let key = null;
  try { key = localStorage.getItem(KEY_STORAGE); } catch { /* storage blocked */ }

  // Same ids as MAPS in map/render.py.
  const MAP_DIMENSIONS = { overworld: "minecraft:overworld", nether: "minecraft:the_nether", end: "minecraft:the_end" };

  let players = null;       // latest {mapId: {players: [...]}} from the socket
  let lastDeath = null;     // {dimension, position} from the status, own worlds only
  let socket = null;
  let failures = 0;
  let retryTimer = null;
  let hiddenTimer = null;
  let closingOnPurpose = false;

  const app = () => window.bluemap;

  function currentMapId() {
    return app()?.mapViewer?.map?.data?.id;
  }

  // Managers are recreated whenever the viewer switches maps, so keep them paused
  // and keep feeding the current map its markers.
  function applyToBlueMap() {
    const bluemap = app();
    if (!bluemap) return;
    for (const manager of [bluemap.playerMarkerManager, bluemap.markerFileManager]) {
      if (manager && !manager.disposed && !manager._paused) {
        manager.setAutoUpdateInterval(0);
        manager.pauseAutoUpdates();
      }
    }
    const manager = bluemap.playerMarkerManager;
    if (manager && !manager.disposed) {
      manager.updateFromData(players?.[currentMapId()] || { players: [] });
    }
    const files = bluemap.markerFileManager;
    if (files && !files.disposed) files.updateFromData(deathMarkers(currentMapId()));
  }

  // Where you last died, as a marker set BlueMap can toggle in its menu.
  function deathMarkers(mapId) {
    const spot = lastDeath;
    if (!spot || !Array.isArray(spot.position) || spot.dimension !== MAP_DIMENSIONS[mapId]) return {};
    const [x, y, z] = spot.position.map(Number);
    return {
      "mc-status-deaths": {
        label: "Deaths",
        toggleable: true,
        defaultHidden: false,
        sorting: 10,
        markers: {
          "last-death": {
            type: "poi",
            position: { x: x + 0.5, y: y + 0.5, z: z + 0.5 },
            label: "Last death",
            detail: `Last death<br><small>${Math.round(x)} ${Math.round(y)} ${Math.round(z)}</small>`,
            icon: "../assets/mc/item/bone.png",
            anchor: { x: 21, y: 21 },  // centre of the 32 px icon plus padding and border (ore-ui.css)
            classes: ["mcs-death"],
            maxDistance: 100000,
          },
        },
      },
    };
  }
  setInterval(applyToBlueMap, 2000);

  function liveUrl() {
    const root = app()?.settings?.liveDataRoot || "";
    const base = root.replace(/\/bluemap\/?$/, "");
    return `${base.replace(/^http/, "ws")}/live?key=${encodeURIComponent(key)}`;
  }

  function connect() {
    clearTimeout(retryTimer);
    if (!key || socket || document.hidden) return;
    if (!app()?.settings?.liveDataRoot) {
      retryTimer = setTimeout(connect, 1000); // BlueMap still loading its settings
      return;
    }
    closingOnPurpose = false;
    const ws = new WebSocket(liveUrl());
    socket = ws;
    ws.addEventListener("open", () => { failures = 0; });
    ws.addEventListener("message", (event) => {
      if (typeof event.data !== "string") return; // screenshots: not needed here
      const message = JSON.parse(event.data);
      if (message.type === "status") {
        players = message.players;
        lastDeath = message.status?.player?.last_death || null;
        applyToBlueMap();
      }
    });
    ws.addEventListener("close", (event) => {
      if (socket === ws) socket = null;
      if (event.code === 4001 || event.code === 4003 || closingOnPurpose) return; // invalid key / full / hidden
      failures += 1;
      retryTimer = setTimeout(connect, Math.min(300000, 2000 * 2 ** (failures - 1)));
    });
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      hiddenTimer = setTimeout(() => {
        closingOnPurpose = true;
        clearTimeout(retryTimer);
        socket?.close(1000, "tab hidden");
        socket = null;
      }, HIDDEN_GRACE_MS);
    } else {
      clearTimeout(hiddenTimer);
      connect();
    }
  });

  // keep-alive, answered by Cloudflare without waking anything
  setInterval(() => {
    if (socket && socket.readyState === WebSocket.OPEN) socket.send("ping");
  }, 45000);

  connect();
})();
