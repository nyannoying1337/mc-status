// The optional server tool: every player on a server, pushed by the mod running
// on a Fabric server with SERVER_PUSH_TOKEN. It has its own token, so a server
// (or whoever hosts it) can't touch your own status, and your agent's token
// can't mint admin links. Without its secrets every route here refuses.
//
// Three kinds of viewers:
//   control ?key=<CONTROL_KEY>                 the admin page, plus actions and the console
//   admin   ?key=<ADMIN_KEY>                   sees the server and every player
//   player  ?key=<uuid>.<signature>            sees only that player
// Player keys are an HMAC of the player's UUID with PLAYER_LINK_SECRET, so no
// key table is stored: the Worker can check a link without remembering it.
//
// A player's view of the world (when the server has player_screens on and the
// player's own client opted in) arrives on /server/shot and is kept as one row
// per player. Who may look is the server's choice, pushed with its status.
//
// The server connects out to /server/connect and keeps that WebSocket open:
// its status arrives over it, and actions from control viewers go back down it.
// Nothing ever has to connect to the server.
import { DurableObject } from "cloudflare:workers";

const MAX_VIEWERS = 10;
const STALE_MS = 90000;
// The meta row (TPS, time of day, ...) changes on every push; writing it at most
// once a minute keeps a busy server well inside the free 100,000 writes a day.
// Viewers still get every push live, and the latest meta is kept in memory.
const META_WRITE_MS = 60000;
const MAX_BODY_BYTES = 8 * 1024 * 1024;
// One frame, scaled down on the player's own machine before it is sent. Well
// under the Durable Object's 2 MB value limit, and small enough that a player's
// connection isn't the price of being looked at.
const MAX_SHOT_BYTES = 512 * 1024;
const LOG_SIZE = 50;
const ACTIONS_PER_MINUTE = 30;
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Authorization, Content-Type",
};
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const GAME_MODES = ["survival", "creative", "adventure", "spectator"];

function json(body, status = 200, extra = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...CORS, ...extra } });
}

function safeEqual(given, expected) {
  if (!expected || typeof given !== "string" || given.length !== expected.length) return false;
  let diff = 0;
  for (let i = 0; i < given.length; i++) diff |= given.charCodeAt(i) ^ expected.charCodeAt(i);
  return diff === 0;
}

const base64url = (bytes) => btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

async function sha256(text) {
  return base64url(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)));
}

async function signature(secret, uuid) {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return base64url(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(`player:${uuid}`))).slice(0, 32);
}

export async function playerKey(env, uuid) {
  return `${uuid}.${await signature(env.PLAYER_LINK_SECRET, uuid)}`;
}

/** Who a key belongs to: {role: "admin", control?} | {role: "player", uuid} | null. */
export async function viewerFor(env, key) {
  if (typeof key !== "string" || !key) return null;
  if (safeEqual(key, env.CONTROL_KEY)) return { role: "admin", control: true };
  if (safeEqual(key, env.ADMIN_KEY)) return { role: "admin" };
  const [uuid, sig] = key.split(".");
  if (!UUID.test(uuid || "") || !env.PLAYER_LINK_SECRET) return null;
  return safeEqual(sig || "", await signature(env.PLAYER_LINK_SECRET, uuid)) ? { role: "player", uuid } : null;
}

// Changing any key disconnects everyone who joined before.
const secretsHash = (env) => sha256(`${env.CONTROL_KEY || ""}|${env.ADMIN_KEY || ""}|${env.PLAYER_LINK_SECRET || ""}`);

/**
 * Who may look at a player's view of the world. The server decides with
 * player_screens: "control" keeps frames with whoever holds the control key,
 * "admin" lets every admin see them, "off" (or unset) means there are none.
 * A player may always see their own, so they can check what is being shared.
 */
export function canSeeScreen(viewer, mode, uuid) {
  if (mode !== "control" && mode !== "admin") return false;
  if (viewer.role === "player") return viewer.uuid === uuid;
  if (viewer.role !== "admin") return false;
  return mode === "admin" || Boolean(viewer.control);
}

/** What one viewer may see of the stored state. */
export function viewFor(viewer, state) {
  const players = state.players || [];
  const visible = viewer.role === "admin" ? players : players.filter((player) => player.uuid === viewer.uuid);
  const mode = state.server?.player_screens || "off";
  const shots = state.shots || {};
  return {
    type: "server",
    role: viewer.role,
    control: Boolean(viewer.control),
    connected: Boolean(state.connected),
    you: viewer.uuid || null,
    stale_ms: STALE_MS,
    received_at: state.received_at || null,
    server: state.server || null,
    // When a frame exists and this viewer may see it, its time: the page asks
    // for the image itself over HTTP, so the frame is never pushed to someone
    // who only happens to be holding the socket open.
    players: visible.map((player) => (shots[player.uuid] && canSeeScreen(viewer, mode, player.uuid)
      ? { ...player, screen_at: shots[player.uuid] }
      : player)),
  };
}

// Control characters are dropped: they have no business in a reason or a command.
// Written as escapes, not the literal bytes: those made git treat this whole file
// as binary, so its diffs never rendered in a review and grep skipped it.
const clean = (value, max) => (typeof value === "string" ? value.replace(/[\x00-\x1f\x7f]/g, "").trim().slice(0, max) : "");

/**
 * An action from a control viewer, checked before it goes anywhere near the
 * server: known action, well-formed target, bounded text. Returns the action
 * to send, or {error}.
 */
export function checkAction(message) {
  const action = String(message?.action || "");
  const id = clean(message?.id, 40);
  if (!id) return { error: "missing id" };
  if (action === "command") {
    const command = clean(message.command, 1000).replace(/^\/+/, "");
    return command ? { id, action, command } : { id, error: "empty command" };
  }
  const targeted = ["kick", "ban", "pardon", "whitelist_add", "whitelist_remove", "gamemode", "heal", "feed", "message"];
  if (!targeted.includes(action)) return { id, error: "unknown action" };
  if (!UUID.test(message.uuid || "")) return { id, error: "missing player" };
  const out = { id, action, uuid: message.uuid };
  if (action === "kick" || action === "ban") out.reason = clean(message.reason, 200);
  if (action === "gamemode") {
    if (!GAME_MODES.includes(message.mode)) return { id, error: "unknown game mode" };
    out.mode = message.mode;
  }
  if (action === "message") {
    out.text = clean(message.text, 256);
    if (!out.text) return { id, error: "empty message" };
  }
  return out;
}

function refuseSocket(code, reason) {
  const [client, server] = Object.values(new WebSocketPair());
  server.accept();
  server.close(code, reason);
  return new Response(null, { status: 101, webSocket: client });
}

export class ServerStore extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.recentActions = new Map();  // socket -> timestamps, for the rate limit
    if (typeof WebSocketRequestResponsePair !== "undefined") {
      ctx.setWebSocketAutoResponse(new WebSocketRequestResponsePair("ping", "pong"));
    }
  }

  serverSocket() {
    return this.ctx.getWebSockets("server-link")[0] || null;
  }

  // Each player is its own row, so a big server never hits the 2 MB value limit.
  // Frames live under their own "shot:" prefix rather than in here, so listing
  // the players never pulls a single image into memory.
  async state() {
    const rows = await this.ctx.storage.list({ prefix: "server:" });
    const players = [];
    let meta = this.meta ? JSON.parse(this.meta.row) : { server: null, received_at: null };
    for (const [key, value] of rows) {
      if (key === "server:meta") {
        if (!this.meta) meta = JSON.parse(value);
      } else if (key.startsWith("server:player:")) players.push(JSON.parse(value));
    }
    players.sort((a, b) => Number(b.online) - Number(a.online) || String(a.name).localeCompare(String(b.name)));
    return { server: meta.server, received_at: meta.received_at, players, shots: await this.shots(), connected: Boolean(this.serverSocket()) };
  }

  /** uuid -> when its frame arrived. One small row, so a view never reads images. */
  async shots() {
    if (!this.shotIndex) this.shotIndex = JSON.parse((await this.ctx.storage.get("server:shots")) || "{}");
    return this.shotIndex;
  }

  async publish(payload) {
    const receivedAt = Date.now();
    const incoming = new Map((payload.players || []).filter((p) => UUID.test(p?.uuid || "")).map((p) => [p.uuid, p]));
    const existing = await this.ctx.storage.list({ prefix: "server:player:" });
    const changes = {};
    const metaRow = JSON.stringify({ server: payload.server || null, received_at: receivedAt });
    const metaShape = JSON.stringify([payload.server?.name, incoming.size]);
    if (!this.meta || this.meta.shape !== metaShape || receivedAt - this.meta.writtenAt >= META_WRITE_MS) {
      changes["server:meta"] = metaRow;
      this.meta = { row: metaRow, shape: metaShape, writtenAt: receivedAt };
    } else {
      this.meta = { ...this.meta, row: metaRow };
    }
    for (const [uuid, player] of incoming) {
      const row = JSON.stringify(player);
      if (existing.get(`server:player:${uuid}`) !== row) changes[`server:player:${uuid}`] = row;  // only changed rows are written
    }
    const gone = [...existing.keys()].filter((key) => !incoming.has(key.slice("server:player:".length)));
    const entries = Object.entries(changes);
    for (let i = 0; i < entries.length; i += 100) await this.ctx.storage.put(Object.fromEntries(entries.slice(i, i + 100)));
    if (gone.length) await this.ctx.storage.delete(gone);
    // A player the server no longer reports takes their frame with them, rather
    // than leaving the last thing they looked at sitting in storage for good.
    const shots = await this.shots();
    const staleShots = Object.keys(shots).filter((uuid) => !incoming.has(uuid));
    if (staleShots.length) {
      for (const uuid of staleShots) delete shots[uuid];
      await this.ctx.storage.delete(staleShots.map((uuid) => `shot:${uuid}`));
      await this.ctx.storage.put("server:shots", JSON.stringify(shots));
    }

    const state = { server: payload.server || null, received_at: receivedAt, players: [...incoming.values()], shots, connected: Boolean(this.serverSocket()) };
    await this.broadcast((viewer) => viewFor(viewer, state));
    return { players: incoming.size, written: entries.length, removed: gone.length };
  }

  /** Sends to every current viewer (optionally control viewers only); closes sockets from before a key change. */
  async broadcast(message, controlOnly = false) {
    const hash = await secretsHash(this.env);
    for (const socket of this.ctx.getWebSockets("server-viewer")) {
      try {
        const viewer = socket.deserializeAttachment();
        if (!viewer || viewer.secretsHash !== hash) socket.close(4001, "key changed");
        else if (!controlOnly || viewer.control) {
          // a per-viewer message may come back null, meaning "not for this one"
          const body = typeof message === "function" ? message(viewer) : message;
          if (body) socket.send(JSON.stringify(body));
        }
      } catch {
        // already gone
      }
    }
  }

  /** What the server last said about who may see frames. */
  async screensMode() {
    const row = this.meta ? this.meta.row : await this.ctx.storage.get("server:meta");
    try {
      return JSON.parse(row || "{}").server?.player_screens || "off";
    } catch {
      return "off";
    }
  }

  /** A frame from one player. Only its time is broadcast; the image is fetched. */
  async publishShot(uuid, image) {
    const at = Date.now();
    const shots = await this.shots();
    shots[uuid] = at;
    await this.ctx.storage.put({ [`shot:${uuid}`]: image, "server:shots": JSON.stringify(shots) });
    const mode = await this.screensMode();
    await this.broadcast((viewer) => (canSeeScreen(viewer, mode, uuid) ? { type: "screen", uuid, at } : null));
    return { at, bytes: image.byteLength };
  }

  /** The stored frame, if this viewer is allowed it. Null covers both "no" and "none". */
  async shotFor(viewer, uuid) {
    if (!canSeeScreen(viewer, await this.screensMode(), uuid)) return null;
    const shots = await this.shots();
    if (!shots[uuid]) return null;
    const image = await this.ctx.storage.get(`shot:${uuid}`);
    return image ? { image, at: shots[uuid] } : null;
  }

  /**
   * Which players someone is actually looking at, sent down to the server so it
   * only ever asks those clients for a frame. Nobody watching costs nothing:
   * no capture on anyone's machine, no rows written here.
   */
  async sendWatch() {
    const server = this.serverSocket();
    if (!server) return;
    const mode = await this.screensMode();
    const wanted = new Set();
    for (const socket of this.ctx.getWebSockets("server-viewer")) {
      try {
        const viewer = socket.deserializeAttachment();
        if (viewer?.watching && canSeeScreen(viewer, mode, viewer.watching)) wanted.add(viewer.watching);
      } catch {
        // already gone
      }
    }
    const uuids = [...wanted].sort();
    const line = JSON.stringify(uuids);
    if (line === this.lastWatch) return;
    this.lastWatch = line;
    try {
      server.send(JSON.stringify({ type: "watch", uuids }));
    } catch {
      this.lastWatch = null;  // say it again on the next socket
    }
  }

  async log() {
    return JSON.parse((await this.ctx.storage.get("server:log")) || "[]");
  }

  /** Adds or updates an entry in the action log, and shows it to control viewers. */
  async record(entry) {
    const log = await this.log();
    const index = log.findIndex((item) => item.id === entry.id);
    if (index >= 0) log[index] = { ...log[index], ...entry };
    else log.push(entry);
    const trimmed = log.slice(-LOG_SIZE);
    await this.ctx.storage.put("server:log", JSON.stringify(trimmed));
    await this.broadcast({ type: "log", entry: trimmed.find((item) => item.id === entry.id) }, true);
  }

  async fetch(request) {
    if (request.headers.get("Upgrade") !== "websocket") return new Response("expected websocket", { status: 426 });
    if (request.headers.get("X-Server-Link") === "1") {
      const [client, socket] = Object.values(new WebSocketPair());
      // one server at a time: a reconnecting server replaces its old socket
      for (const old of this.ctx.getWebSockets("server-link")) {
        try { old.close(4000, "replaced"); } catch { /* closed */ }
      }
      this.ctx.acceptWebSocket(socket, ["server-link"]);
      socket.serializeAttachment({ kind: "server" });
      await this.broadcast({ type: "link", connected: true });
      this.lastWatch = null;  // a fresh socket knows nothing; say it again
      await this.sendWatch();
      return new Response(null, { status: 101, webSocket: client });
    }

    if (this.ctx.getWebSockets("server-viewer").length >= MAX_VIEWERS) return refuseSocket(4003, "full");
    const viewer = JSON.parse(request.headers.get("X-Viewer"));
    const [client, socket] = Object.values(new WebSocketPair());
    this.ctx.acceptWebSocket(socket, ["server-viewer"]);
    socket.serializeAttachment({ ...viewer, secretsHash: await secretsHash(this.env) });
    socket.send(JSON.stringify(viewFor(viewer, await this.state())));
    if (viewer.control) socket.send(JSON.stringify({ type: "log", entries: await this.log() }));
    return new Response(null, { status: 101, webSocket: client });
  }

  async webSocketMessage(socket, raw) {
    if (typeof raw !== "string" || raw === "ping") return;
    const attachment = socket.deserializeAttachment() || {};
    let message;
    try {
      message = JSON.parse(raw);
    } catch {
      return;
    }
    if (attachment.kind === "server") await this.fromServer(message);
    else await this.fromViewer(socket, attachment, message);
  }

  async fromServer(message) {
    if (message?.type === "status") {
      await this.publish(message);
    } else if (message?.type === "result" && typeof message.id === "string") {
      await this.record({
        id: clean(message.id, 40),
        status: message.ok ? "done" : "failed",
        output: String(message.output ?? "").split("\n").map((line) => clean(line, 300)).join("\n").slice(0, 4000),
        finished_at: Date.now(),
      });
    }
  }

  async fromViewer(socket, viewer, message) {
    if (message?.type === "watch") {
      const uuid = UUID.test(message.uuid || "") ? message.uuid : null;
      if (uuid === (viewer.watching || null)) return;
      socket.serializeAttachment({ ...viewer, watching: uuid });
      await this.sendWatch();
      return;
    }
    if (message?.type !== "action") return;
    const reply = (body) => socket.send(JSON.stringify({ type: "action", ...body }));
    if (!viewer.control || viewer.secretsHash !== (await secretsHash(this.env))) {
      reply({ id: message.id, ok: false, error: "This key can't take actions." });
      return;
    }
    const now = Date.now();
    const recent = (this.recentActions.get(socket) || []).filter((at) => now - at < 60000);
    if (recent.length >= ACTIONS_PER_MINUTE) {
      reply({ id: message.id, ok: false, error: "Too many actions; wait a minute." });
      return;
    }
    this.recentActions.set(socket, [...recent, now]);

    const action = checkAction(message);
    if (action.error) {
      reply({ id: action.id || message.id, ok: false, error: action.error });
      return;
    }
    const server = this.serverSocket();
    if (!server) {
      reply({ id: action.id, ok: false, error: "The server isn't connected." });
      return;
    }
    const target = action.uuid ? JSON.parse((await this.ctx.storage.get(`server:player:${action.uuid}`)) || "null") : null;
    server.send(JSON.stringify({ type: "action", ...action }));
    reply({ id: action.id, ok: true });
    await this.record({
      id: action.id, at: now, action: action.action, status: "sent",
      target: target?.name || action.uuid || null,
      detail: action.command || action.reason || action.text || action.mode || "",
    });
  }

  async webSocketClose(socket, code, reason) {
    this.recentActions.delete(socket);
    const wasServer = socket.deserializeAttachment()?.kind === "server";
    try { socket.close(code, reason); } catch { /* closed */ }
    const stillLinked = this.ctx.getWebSockets("server-link").some((other) => other !== socket);
    if (wasServer && !stillLinked) await this.broadcast({ type: "link", connected: false });
    else if (!wasServer) await this.sendWatch();  // they may have been the only one looking
  }

  webSocketError(socket) {
    try { socket.close(1011, "error"); } catch { /* closed */ }
  }
}

const store = (env) => env.SERVER_STORE.get(env.SERVER_STORE.idFromName("server"));

/** The server mod, proving itself with SERVER_PUSH_TOKEN (never the agent's PUSH_TOKEN). */
function fromServer(request, env) {
  const header = request.headers.get("Authorization") || "";
  return safeEqual(header.startsWith("Bearer ") ? header.slice(7) : "", env.SERVER_PUSH_TOKEN);
}

// Only the Worker decides who a socket belongs to: whatever the client sent is dropped.
function forwardHeaders(extra) {
  const headers = new Headers({ Upgrade: "websocket" });
  for (const [name, value] of Object.entries(extra)) headers.set(name, value);
  return headers;
}

/** Routes under /server/. Returns null for anything else. */
export async function handleServer(request, env, url, path) {
  if (!path.startsWith("/server/")) return null;

  if (path === "/server/status" && request.method === "POST") {
    if (!fromServer(request, env)) return json({ error: "unauthorized" }, 401);
    if (Number(request.headers.get("Content-Length") || 0) > MAX_BODY_BYTES) return json({ error: "too large" }, 413);
    let payload;
    try {
      payload = await request.json();
    } catch {
      return json({ error: "body must be json" }, 400);
    }
    return json({ ok: true, ...(await store(env).publish(payload)) });
  }

  // The server's own connection: status up, actions down.
  if (path === "/server/connect" && request.method === "GET") {
    if (request.headers.get("Upgrade") !== "websocket") return json({ error: "expected websocket" }, 426);
    if (!fromServer(request, env)) return json({ error: "unauthorized" }, 401);
    return store(env).fetch(new Request(request.url, { headers: forwardHeaders({ "X-Server-Link": "1" }) }));
  }

  // A player's view of the world, pushed by the server that collected it.
  if (path === "/server/shot" && request.method === "POST") {
    if (!fromServer(request, env)) return json({ error: "unauthorized" }, 401);
    const uuid = url.searchParams.get("uuid") || "";
    if (!UUID.test(uuid)) return json({ error: "uuid required" }, 400);
    if (Number(request.headers.get("Content-Length") || 0) > MAX_SHOT_BYTES) return json({ error: "too large" }, 413);
    const image = await request.arrayBuffer();
    if (image.byteLength === 0) return json({ error: "empty body" }, 400);
    if (image.byteLength > MAX_SHOT_BYTES) return json({ error: "too large" }, 413);
    return json({ ok: true, ...(await store(env).publishShot(uuid, image)) });
  }

  // 404 covers both "no frame" and "not for you", so an admin without the
  // control key can't find out who is sharing one by asking.
  if (path === "/server/shot" && request.method === "GET") {
    const viewer = await viewerFor(env, url.searchParams.get("key"));
    if (!viewer) return json({ error: "invite required" }, 401);
    const uuid = url.searchParams.get("uuid") || "";
    if (!UUID.test(uuid)) return json({ error: "uuid required" }, 400);
    const found = await store(env).shotFor(viewer, uuid);
    if (!found) return json({ error: "no frame" }, 404);
    // The page asks with ?t=<screen_at>, so each frame is its own URL.
    return new Response(found.image, {
      headers: { "Content-Type": "image/jpeg", "Cache-Control": "private, max-age=31536000, immutable", "X-Taken-At": String(found.at), ...CORS },
    });
  }

  if (path === "/server/live" && request.method === "GET") {
    if (request.headers.get("Upgrade") !== "websocket") return json({ error: "expected websocket" }, 426);
    const viewer = await viewerFor(env, url.searchParams.get("key"));
    if (!viewer) return refuseSocket(4001, "invite invalid");
    return store(env).fetch(new Request(request.url, { headers: forwardHeaders({ "X-Viewer": JSON.stringify(viewer) }) }));
  }

  // For the server mod's /mcstatus command: the server proves itself with its
  // token and gets the key to put in a clickable chat link. The keys never have
  // to be copied into the server's config.
  if (path === "/server/links" && request.method === "POST") {
    if (!fromServer(request, env)) return json({ error: "unauthorized" }, 401);
    let body;
    try {
      body = await request.json();
    } catch {
      return json({ error: "body must be json" }, 400);
    }
    const noStore = { "Cache-Control": "no-store" };
    if (body?.control === true) {
      return env.CONTROL_KEY ? json({ key: env.CONTROL_KEY }, 200, noStore) : json({ error: "CONTROL_KEY not set" }, 503);
    }
    if (body?.admin === true) {
      return env.ADMIN_KEY ? json({ key: env.ADMIN_KEY }, 200, noStore) : json({ error: "ADMIN_KEY not set" }, 503);
    }
    if (!UUID.test(body?.uuid || "")) return json({ error: "uuid, admin or control required" }, 400);
    if (!env.PLAYER_LINK_SECRET) return json({ error: "PLAYER_LINK_SECRET not set" }, 503);
    return json({ key: await playerKey(env, body.uuid) }, 200, noStore);
  }

  if (path === "/server/link" && request.method === "GET") {
    const viewer = await viewerFor(env, url.searchParams.get("key"));
    if (viewer?.role !== "admin") return json({ error: "admin key required" }, 401);
    const uuid = url.searchParams.get("uuid") || "";
    if (!UUID.test(uuid)) return json({ error: "uuid required" }, 400);
    return json({ key: await playerKey(env, uuid) }, 200, { "Cache-Control": "no-store" });
  }

  return json({ error: "not found" }, 404);
}
