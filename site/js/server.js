// The server tool page (server.html).
//
// Admins (opened with the admin key) get the server overview and a table of
// every player; clicking one opens their page. The control key adds actions and
// the console (server-control.js). A player's own link opens only their page.
// The Worker decides what each key may see and do; this page just shows it.
//
// Opening a player's page also tells the Worker so, which is how the server
// knows to ask that player's client for a frame. Closing it stops the asking.
import { assetsReady, glyphWidths } from "./assets.js";
import { advancementsPanel, chip, itemIcon, panel, statsPanel } from "./cards.js";
import { API_URL, SITE_NAME } from "./config.js";
import { fitPixels, inventoryNode, vitalsNode } from "./gui.js";
import { consolePanel, controlsPanel, setSender } from "./server-control.js";
import { clockTime, dimensionName, duration, el, timeAgo, titleCase } from "./util.js";

const KEY_STORAGE = "mc-status-server-key";
const root = document.getElementById("root");
const pill = document.getElementById("live-pill");
const forgetButton = document.getElementById("forget-key");

let key = (() => {
  const match = location.hash.match(/(?:^#|&)key=([^&]+)/);
  if (match) {
    const found = decodeURIComponent(match[1]);
    try { localStorage.setItem(KEY_STORAGE, found); } catch { /* private mode */ }
    history.replaceState(null, "", location.pathname + location.search + location.hash.replace(/(^#|&)key=[^&]+/, "$1").replace(/^#&?$/, ""));
    return found;
  }
  try { return localStorage.getItem(KEY_STORAGE); } catch { return null; }
})();

let view = null;         // latest message from the Worker
let selected = null;     // uuid of the open player page (admins)
let filter = "";
let sortBy = "status";
let actionLog = [];      // control key only: recent actions and their results
let notice = null;       // a refused action, shown for a few seconds
let noticeTimer = null;

document.getElementById("brand-name").textContent = `${SITE_NAME} server`;

function setPill(state, text) {
  pill.hidden = !state;
  pill.dataset.state = state || "";
  pill.textContent = text || "";
}

function screen(icon, title, detail, extra) {
  root.replaceChildren(el("div", { class: "center" }, [el("section", { class: "panel card-narrow" }, [
    itemIcon(icon), el("h1", { text: title }), el("p", { text: detail }), extra,
  ])]));
}

function join(error = "") {
  setPill(null);
  forgetButton.hidden = true;
  const input = el("input", { id: "server-key", type: "text", autocomplete: "off", spellcheck: "false", placeholder: "Admin key or your player link" });
  const form = el("form", { novalidate: "" }, [
    el("div", { class: "field" }, [el("label", { for: "server-key", text: "Key" }), input, el("p", { class: "error", role: "alert", text: error })]),
    el("button", { type: "submit", class: "btn btn-green", text: "Open" }),
  ]);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const raw = input.value.trim();
    const fromLink = raw.match(/[#&]key=([^&\s]+)/);
    key = fromLink ? decodeURIComponent(fromLink[1]) : raw;
    if (!key) return;
    try { localStorage.setItem(KEY_STORAGE, key); } catch { /* private mode */ }
    connect();
  });
  screen("command_block", "Server status", "Staff open this with the admin key; players with the link they were given.", form);
  input.focus();
}

// ---- views ----------------------------------------------------------------------

const avatar = (player, size = 64) => {
  const img = el("img", { alt: "", src: `https://mc-heads.net/avatar/${encodeURIComponent(player.name || "MHF_Steve")}/${size}` });
  img.addEventListener("error", () => { img.src = "assets/mc/item/player_head.png"; }, { once: true });
  return img;
};

const advancementPercent = (player) => {
  const adv = player.advancements;
  return adv?.total ? Math.floor((adv.done / adv.total) * 100) : null;
};

function serverBar(server, players) {
  if (!server) return null;
  const online = players.filter((p) => p.online).length;
  const tpsLevel = server.tps >= 19 ? "online" : server.tps >= 15 ? "quiet" : "offline";
  const bar = panel(server.name || "Server", [
    el("div", { class: "chips" }, [
      chip("Players", `${online} / ${server.max_players ?? "?"}`),
      chip("TPS", String(server.tps ?? "?")),
      chip("MSPT", `${server.mspt ?? "?"} ms`),
      chip(`Day ${server.day ?? "?"}`, clockTime(server.time ?? 0)),
      chip("Weather", titleCase(server.weather || "clear")),
      chip("Version", server.version || "?"),
    ]),
  ], "server-bar");
  bar.dataset.tps = tpsLevel;
  return bar;
}

function playerTable(players) {
  const search = el("input", { type: "search", class: "table-search", placeholder: "Filter players", value: filter, "aria-label": "Filter players" });
  search.addEventListener("input", () => {
    filter = search.value;
    const at = search.selectionStart;
    render();
    const again = root.querySelector(".table-search");
    again.focus();
    again.setSelectionRange(at, at);
  });

  const columns = [
    ["name", "Player"], ["status", "Status"], ["health", "Health"], ["where", "Where"], ["ping", "Ping"], ["adv", "Advancements"],
  ];
  const sorters = {
    name: (a, b) => a.name.localeCompare(b.name),
    status: (a, b) => Number(b.online) - Number(a.online) || (b.last_seen || 0) - (a.last_seen || 0) || a.name.localeCompare(b.name),
    health: (a, b) => (a.health ?? 99) - (b.health ?? 99),
    where: (a, b) => String(a.dimension || "~").localeCompare(String(b.dimension || "~")),
    ping: (a, b) => (a.ping ?? 1e9) - (b.ping ?? 1e9),
    adv: (a, b) => (advancementPercent(b) ?? -1) - (advancementPercent(a) ?? -1),
  };
  const shown = players
    .filter((p) => !filter || p.name.toLowerCase().includes(filter.toLowerCase()))
    .sort(sorters[sortBy]);

  const head = el("tr", {}, columns.map(([id, label]) => {
    const button = el("button", { type: "button", class: "th-sort", text: label });
    if (sortBy === id) button.dataset.active = "true";
    button.addEventListener("click", () => { sortBy = id; render(); });
    return el("th", { scope: "col" }, [button]);
  }));

  const body = shown.map((player) => {
    const row = el("tr", { tabindex: "0", "aria-label": `Open ${player.name}` }, [
      el("td", {}, [el("span", { class: "who-cell" }, [avatar(player, 32), el("b", { text: player.name })])]),
      el("td", {}, [el("span", {
        class: "status-dot", "data-online": String(Boolean(player.online)), "data-banned": String(Boolean(player.banned)),
        text: player.banned ? "Banned" : player.online ? "Online" : player.last_seen ? `Seen ${timeAgo(player.last_seen)}` : "Offline",
      })]),
      el("td", { text: typeof player.health === "number" ? `${Math.ceil(player.health) / 2} ♥` : "—" }),
      el("td", { text: player.online && player.position ? `${dimensionName(player.dimension)} · ${player.position.map((n) => Math.round(n)).join(" ")}` : "—" }),
      el("td", { text: player.online && typeof player.ping === "number" ? `${player.ping} ms` : "—" }),
      el("td", { text: advancementPercent(player) === null ? "—" : `${advancementPercent(player)}%` }),
    ]);
    const open = () => { selected = player.uuid; location.hash = `player=${player.uuid}`; render(); window.scrollTo(0, 0); };
    row.addEventListener("click", open);
    row.addEventListener("keydown", (event) => { if (event.key === "Enter") open(); });
    return row;
  });

  return panel(`Players · ${players.length}`, [
    search,
    el("div", { class: "table-scroll" }, [
      el("table", { class: "player-table" }, [el("thead", {}, [head]), el("tbody", {}, body)]),
    ]),
    shown.length ? null : el("p", { class: "empty", text: filter ? "No player matches." : "No players yet." }),
  ]);
}

/** Mirrors canSeeScreen in worker/server.js; the Worker is what actually decides. */
function maySeeScreen(player) {
  const mode = view.server?.player_screens || "off";
  if (mode !== "control" && mode !== "admin") return false;
  if (view.role === "player") return true;
  return mode === "admin" || Boolean(view.control);
}

/**
 * What the player is looking at, if their own client agreed to send it. The
 * image is fetched with the key rather than pushed, so it never reaches a
 * viewer who isn't allowed it, and ?t= makes each frame its own URL.
 */
function screenPanel(player) {
  if (!maySeeScreen(player)) return null;
  const note = player.screen_at ? `Taken ${timeAgo(player.screen_at)}` : "";
  if (player.screen_at) {
    const image = el("img", {
      class: "shot-image", alt: `What ${player.name} is looking at`, loading: "lazy",
      src: `${API_URL}/server/shot?key=${encodeURIComponent(key)}&uuid=${player.uuid}&t=${player.screen_at}`,
    });
    image.addEventListener("click", () => window.open(image.src, "_blank", "noopener"));
    return panel("Their view", [image], "", note);
  }
  if (!player.online) return panel("Their view", [el("p", { class: "empty", text: "Only while they're online." })]);
  if (player.screen_capable === false) {
    return panel("Their view", [el("p", {
      class: "empty",
      text: "Nothing to show. A view only comes from a player who has the mod and has turned sharing on themselves; there's no way to see one otherwise.",
    })]);
  }
  return panel("Their view", [el("p", { class: "empty", text: "Waiting for a frame from their game." })]);
}

function playerPage(player, isAdmin) {
  const chips = [
    chip("", player.online ? "Online" : player.last_seen ? `Last seen ${timeAgo(player.last_seen)}` : "Offline"),
  ];
  if (player.online) {
    if (player.game_mode) chips.push(chip("", titleCase(player.game_mode)));
    if (player.dimension) chips.push(chip("", dimensionName(player.dimension)));
    if (player.position) chips.push(chip("XYZ", player.position.map((n) => Math.round(n)).join("  ")));
    if (typeof player.ping === "number") chips.push(chip("Ping", `${player.ping} ms`));
  }
  if (player.stats?.play_time) chips.push(chip("Played", duration(player.stats.play_time / 20)));

  const actions = [];
  if (isAdmin) {
    const back = el("button", { type: "button", class: "btn btn-stone btn-small", text: "← All players" });
    back.addEventListener("click", () => { selected = null; history.replaceState(null, "", location.pathname); render(); });
    const linkButton = el("button", { type: "button", class: "btn btn-green btn-small", text: "Copy player link" });
    const linkOut = el("input", { type: "text", class: "link-out", readonly: "", hidden: "", "aria-label": "Player link" });
    linkButton.addEventListener("click", async () => {
      linkButton.disabled = true;
      try {
        const response = await fetch(`${API_URL}/server/link?key=${encodeURIComponent(key)}&uuid=${player.uuid}`, { cache: "no-store" });
        const { key: playerKey } = await response.json();
        const link = `${location.origin}${location.pathname}#key=${encodeURIComponent(playerKey)}`;
        linkOut.value = link;
        linkOut.hidden = false;
        linkOut.select();
        await navigator.clipboard?.writeText(link).catch(() => {});
        linkButton.textContent = "Copied: send it only to them";
      } finally {
        linkButton.disabled = false;
      }
    });
    actions.push(el("div", { class: "player-actions" }, [back, linkButton, linkOut]));
  }

  const identity = el("section", { class: "panel" }, [
    ...actions,
    el("div", { class: "identity player-identity" }, [
      el("div", { class: "avatar" }, [avatar(player)]),
      el("div", { class: "who" }, [el("h1", { text: player.name }), el("div", { class: "chips" }, chips)]),
    ]),
    player.online && glyphWidths && typeof player.health === "number"
      ? el("div", { class: "vitals-well" }, [vitalsNode(player)]) : null,
  ]);

  // statistics sit under the inventory, so the left column keeps pace with the
  // (usually much longer) advancements list on the right
  const left = el("div", { class: "col" }, [
    screenPanel(player),
    player.online && glyphWidths ? panel("Inventory", [el("div", { class: "inventory-well" }, [inventoryNode(player)])])
      : panel("Inventory", [el("p", { class: "empty", text: "Only shown while they're online." })]),
    player.stats ? statsPanel(player.stats, player.online) : null,
  ]);
  const right = el("div", { class: "col" }, [
    player.advancements?.total ? advancementsPanel(player.advancements) : panel("Advancements", [el("p", { class: "empty", text: "None yet." })]),
  ]);
  const controls = isAdmin && view.control ? controlsPanel(player, { connected: view.connected, log: actionLog }) : null;
  return [identity, controls, el("div", { class: "grid" }, [left, right])];
}

// The server only asks a client for a frame while someone has that player's
// page open, so this is what starts and stops the asking. A reconnect forgets,
// so it's sent again whenever the socket opens.
let wantWatch = null;
let sentWatch = null;

function watch(uuid) {
  wantWatch = uuid || null;
  flushWatch();
}

function flushWatch(force = false) {
  if (socket?.readyState !== WebSocket.OPEN) return;
  if (!force && wantWatch === sentWatch) return;
  sentWatch = wantWatch;
  socket.send(JSON.stringify({ type: "watch", uuid: wantWatch }));
}

function render() {
  if (!view) return;
  // the text fields survive re-renders; keep the caret where it was too
  const active = document.activeElement;
  const caret = active && "selectionStart" in active ? [active.selectionStart, active.selectionEnd] : null;
  draw();
  if (active && active !== document.body && root.contains(active) && document.activeElement !== active) {
    active.focus();
    if (caret) try { active.setSelectionRange(...caret); } catch { /* not a text field */ }
  }
}

function draw() {
  forgetButton.hidden = false;
  const age = Date.now() - (view.received_at || 0);
  const quiet = age > (view.stale_ms || 90000);
  setPill(quiet ? "quiet" : "online", quiet ? "Server quiet" : "Live");
  const players = view.players || [];

  if (view.role === "player") {
    const me = players[0];
    watch(me?.uuid || null);
    if (!me) {
      screen("clock", "Nothing about you yet", "The server hasn't reported you. Join it once, and this page fills in by itself.");
      return;
    }
    root.replaceChildren(...playerPage(me, false).filter(Boolean));
  } else {
    const wanted = selected || location.hash.match(/player=([0-9a-f-]{36})/)?.[1];
    const player = wanted && players.find((p) => p.uuid === wanted);
    const top = notice ? el("p", { class: "notice", role: "alert", text: notice }) : null;
    watch(player ? player.uuid : null);
    if (player) {
      selected = player.uuid;
      root.replaceChildren(...[top, ...playerPage(player, true)].filter(Boolean));
    } else {
      selected = null;
      const consoleSection = view.control ? consolePanel({ connected: view.connected, log: actionLog }) : null;
      root.replaceChildren(...[top, serverBar(view.server, players), consoleSection, playerTable(players)].filter(Boolean));
    }
  }
  fitPixels();
}

function showNotice(text) {
  notice = text;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { notice = null; render(); }, 6000);
  render();
}

setSender((body) => {
  if (socket?.readyState !== WebSocket.OPEN) {
    showNotice("Not connected to the Worker; try again in a moment.");
    return null;
  }
  const id = crypto.randomUUID().slice(0, 13);
  socket.send(JSON.stringify({ type: "action", id, ...body }));
  return id;
});

// ---- connection -------------------------------------------------------------------

let socket = null;
let failures = 0;

function connect() {
  if (!key) { join(); return; }
  if (socket) socket.close(1000, "reconnect");
  const ws = new WebSocket(`${API_URL.replace(/^http/, "ws")}/server/live?key=${encodeURIComponent(key)}`);
  socket = ws;
  ws.addEventListener("open", () => {
    failures = 0;
    sentWatch = undefined;  // a fresh socket knows nothing about what we're looking at
    flushWatch(true);
  });
  ws.addEventListener("message", async (event) => {
    if (typeof event.data !== "string") return;
    const message = JSON.parse(event.data);
    if (message.type === "log") {
      if (message.entries) actionLog = message.entries;
      else if (message.entry) actionLog = [...actionLog.filter((entry) => entry.id !== message.entry.id), message.entry].slice(-50);
    } else if (message.type === "link" && view) {
      view = { ...view, connected: message.connected };
    } else if (message.type === "screen" && view) {
      // Only the time arrives; the image itself is fetched with the key.
      view = { ...view, players: (view.players || []).map((player) => (player.uuid === message.uuid ? { ...player, screen_at: message.at } : player)) };
    } else if (message.type === "action") {
      if (!message.ok) showNotice(`Not done: ${message.error}`);
      return;
    } else if (message.type !== "server") {
      return;
    } else {
      view = message;
    }
    await assetsReady;
    render();
  });
  ws.addEventListener("close", (event) => {
    if (socket !== ws) return;
    socket = null;
    if (event.code === 4001) {
      try { localStorage.removeItem(KEY_STORAGE); } catch { /* nothing stored */ }
      key = null;
      join("That key didn't work, or it was replaced. Ask for a new one.");
      return;
    }
    if (event.code === 4003) {
      screen("spyglass", "Too many people are watching", "Try again in a bit.");
      return;
    }
    failures += 1;
    if (!view) screen("redstone_torch", "Can't reach the server tool", "Retrying by itself.");
    setTimeout(connect, Math.min(60000, 2000 * 2 ** (failures - 1)));
  });
}

forgetButton.addEventListener("click", () => {
  try { localStorage.removeItem(KEY_STORAGE); } catch { /* nothing stored */ }
  key = null;
  view = null;
  socket?.close(1000, "forget");
  socket = null;
  join();
});
window.addEventListener("hashchange", () => {
  if (!location.hash.includes("player=")) selected = null;
  render();
});
setInterval(() => { if (view && !document.querySelector(".table-search:focus, .link-out:not([hidden])")) render(); }, 30000);
setInterval(() => { if (socket?.readyState === WebSocket.OPEN) socket.send("ping"); }, 45000);

fitPixels();
connect();
