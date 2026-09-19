// The server tool's Worker routes (worker/server.js), in plain Node.
import { readFileSync, writeFileSync } from "node:fs";
import assert from "node:assert/strict";

const copy = new URL("./server-under-test.mjs", import.meta.url);
writeFileSync(copy, readFileSync(new URL("../worker/server.js", import.meta.url), "utf8").replace(
  'import { DurableObject } from "cloudflare:workers";',
  "class DurableObject { constructor(ctx, env) { this.ctx = ctx; this.env = env; } }",
));
const mod = await import(copy);

const rows = new Map();
const sockets = [];
const storage = {
  async list({ prefix }) { return new Map([...rows].filter(([k]) => k.startsWith(prefix)).sort()); },
  async get(key) { return rows.get(key); },
  async put(entries, value) {
    if (typeof entries === "string") rows.set(entries, value);
    else for (const [k, v] of Object.entries(entries)) rows.set(k, v);
  },
  async delete(keys) { for (const k of keys) rows.delete(k); },
};
const env = { PUSH_TOKEN: "agent-secret", SERVER_PUSH_TOKEN: "push-secret", ADMIN_KEY: "admin-key-123", PLAYER_LINK_SECRET: "link-secret-456", CONTROL_KEY: "control-key-789" };
const instance = new mod.ServerStore({
  storage, acceptWebSocket() {},
  getWebSockets: (tag) => sockets.filter((s) => !s.closed && (!tag || s.tag === tag)),
}, env);
env.SERVER_STORE = { idFromName: (n) => n, get: () => instance };

const call = (path, init) => {
  const url = new URL(`https://w.example${path}`);
  return mod.handleServer(new Request(url, init), env, url, url.pathname);
};
const ALICE = "11111111-1111-4111-8111-111111111111";
const BOB = "22222222-2222-4222-8222-222222222222";
const push = (players) => call("/server/status", {
  method: "POST", headers: { Authorization: "Bearer push-secret", "Content-Type": "application/json" },
  body: JSON.stringify({ server: { name: "Test", tps: 20 }, players }),
});

// --- keys: admin, signed player links, and everything else refused
assert.deepEqual(await mod.viewerFor(env, "admin-key-123"), { role: "admin" });
assert.deepEqual(await mod.viewerFor(env, "control-key-789"), { role: "admin", control: true });
const aliceKey = await mod.playerKey(env, ALICE);
assert.deepEqual(await mod.viewerFor(env, aliceKey), { role: "player", uuid: ALICE });
assert.equal(await mod.viewerFor(env, `${BOB}.${aliceKey.split(".")[1]}`), null, "a signature only works for its own uuid");
assert.equal(await mod.viewerFor(env, `${ALICE}.forged`), null);
assert.equal(await mod.viewerFor(env, "nope"), null);
assert.equal(await mod.viewerFor({ ...env, PLAYER_LINK_SECRET: "rotated" }, aliceKey), null, "rotating the secret revokes links");

// --- links are admin-only
assert.equal((await call(`/server/link?key=${encodeURIComponent(aliceKey)}&uuid=${BOB}`)).status, 401, "players can't mint links");
const link = await call(`/server/link?key=admin-key-123&uuid=${ALICE}`);
assert.equal(link.status, 200);
assert.equal((await link.json()).key, aliceKey);

// --- the server mod's /mcstatus command gets links with the push token
const links = (body, token = "push-secret") => call("/server/links", {
  method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify(body),
});
assert.equal((await links({ admin: true }, "wrong")).status, 401, "only the server can ask");
assert.equal((await links({ admin: true }, aliceKey)).status, 401, "a player key is not the push token");
assert.equal((await links({ admin: true }, "agent-secret")).status, 401, "the agent's token can't mint admin links");
assert.equal((await (await links({ admin: true })).json()).key, "admin-key-123");
assert.equal((await (await links({ control: true })).json()).key, "control-key-789");
assert.equal((await links({ control: true }, "agent-secret")).status, 401, "the agent's token can't get the control key");
assert.equal((await (await links({ uuid: ALICE })).json()).key, aliceKey);
assert.equal((await links({ uuid: "not-a-uuid" })).status, 400);

// --- pushes need the push token; players are stored as separate rows
assert.equal((await call("/server/status", { method: "POST", body: "{}" })).status, 401);
assert.equal((await call("/server/status", { method: "POST", headers: { Authorization: "Bearer agent-secret" }, body: "{}" })).status, 401,
  "the agent's token can't push server data");
const first = await (await push([{ uuid: ALICE, name: "Alice", online: true, position: [1, 2, 3] }, { uuid: BOB, name: "Bob", online: false }])).json();
assert.deepEqual(first, { ok: true, players: 2, written: 3, removed: 0 });
assert.ok(rows.has(`server:player:${ALICE}`) && rows.has(`server:player:${BOB}`));
const again = await (await push([{ uuid: ALICE, name: "Alice", online: true, position: [1, 2, 3] }, { uuid: BOB, name: "Bob", online: false }])).json();
assert.equal(again.written, 0, "unchanged players aren't rewritten, and the meta row waits a minute");
assert.ok((await instance.state()).received_at > JSON.parse(rows.get("server:meta")).received_at - 1, "state() serves the newest meta from memory");
instance.meta.writtenAt -= 61000;
assert.equal((await (await push([{ uuid: ALICE, name: "Alice", online: true, position: [1, 2, 3] }, { uuid: BOB, name: "Bob", online: false }])).json()).written, 1,
  "after a minute the meta row is written again");

// --- live views: admin sees everyone, a player only themselves
function socket(viewer, hashOverride, tag = "server-viewer") {
  const s = { tag, sent: [], closed: null, send(m) { this.sent.push(JSON.parse(m)); }, close(code) { this.closed = { code }; } };
  s.attachment = tag === "server-link" ? { kind: "server" } : { ...viewer };
  s.serializeAttachment = (value) => { s.attachment = value; };
  s.deserializeAttachment = () => (tag === "server-link" ? s.attachment : { ...s.attachment, secretsHash: hashOverride ?? s.hash });
  sockets.push(s);
  return s;
}
const secretsHash = async () => {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(`${env.CONTROL_KEY}|${env.ADMIN_KEY}|${env.PLAYER_LINK_SECRET}`));
  return btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
};
const admin = socket({ role: "admin" }); admin.hash = await secretsHash();
const alice = socket({ role: "player", uuid: ALICE }); alice.hash = await secretsHash();
const stale = socket({ role: "admin" }, "old-hash");
await push([{ uuid: ALICE, name: "Alice", online: true, position: [5, 6, 7] }, { uuid: BOB, name: "Bob", online: true, position: [9, 9, 9] }]);
assert.deepEqual(admin.sent.at(-1).players.map((p) => p.name), ["Alice", "Bob"]);
assert.equal(admin.sent.at(-1).role, "admin");
assert.deepEqual(alice.sent.at(-1).players.map((p) => p.name), ["Alice"], "a player never receives anyone else");
assert.equal(alice.sent.at(-1).you, ALICE);
assert.ok(!JSON.stringify(alice.sent).includes("Bob"));
assert.equal(stale.closed?.code, 4001, "sockets from before a key change are closed");

// --- players who drop out of the push are removed
const removed = await (await push([{ uuid: ALICE, name: "Alice", online: true }])).json();
assert.equal(removed.removed, 1);
assert.deepEqual((await instance.state()).players.map((p) => p.name), ["Alice"]);

// --- actions: checked, control key only, forwarded to the connected server, logged
assert.deepEqual(mod.checkAction({ id: "a", action: "kick", uuid: ALICE, reason: "bye\nnow" }), { id: "a", action: "kick", uuid: ALICE, reason: "byenow" });
assert.deepEqual(mod.checkAction({ id: "b", action: "command", command: "//time set day" }), { id: "b", action: "command", command: "time set day" });
assert.equal(mod.checkAction({ id: "c", action: "op", uuid: ALICE }).error, "unknown action", "only known actions");
assert.equal(mod.checkAction({ id: "d", action: "kick", uuid: "Alice" }).error, "missing player");
assert.equal(mod.checkAction({ id: "e", action: "gamemode", uuid: ALICE, mode: "god" }).error, "unknown game mode");
assert.equal(mod.checkAction({ id: "f", action: "command", command: "   " }).error, "empty command");
assert.equal(mod.checkAction({ action: "heal", uuid: ALICE }).error, "missing id");
assert.equal(mod.checkAction({ id: "g", action: "command", command: "x".repeat(5000) }).command.length, 1000);

const send = (s, body) => instance.webSocketMessage(s, JSON.stringify({ type: "action", ...body }));
const control = socket({ role: "admin", control: true }); control.hash = await secretsHash();
admin.sent.length = 0;
await send(admin, { id: "view-only", action: "kick", uuid: ALICE });
assert.equal(admin.sent.at(-1).ok, false, "the admin key can only look");
await send(control, { id: "nobody-home", action: "heal", uuid: ALICE });
assert.deepEqual(control.sent.at(-1), { type: "action", id: "nobody-home", ok: false, error: "The server isn't connected." });

const serverLink = socket(null, null, "server-link");
await send(control, { id: "k1", action: "kick", uuid: ALICE, reason: "test" });
assert.deepEqual(serverLink.sent.at(-1), { type: "action", id: "k1", action: "kick", uuid: ALICE, reason: "test" }, "the server gets exactly the checked action");
assert.equal(control.sent.find((m) => m.type === "action" && m.id === "k1").ok, true);
let entry = control.sent.findLast((m) => m.type === "log").entry;
assert.deepEqual([entry.action, entry.target, entry.status, entry.detail], ["kick", "Alice", "sent", "test"]);
assert.ok(!admin.sent.some((m) => m.type === "log"), "the log only goes to control viewers");
assert.ok(!alice.sent.some((m) => m.type === "log"));

await instance.webSocketMessage(serverLink, JSON.stringify({ type: "result", id: "k1", ok: true, output: "Kicked Alice:\ntest" }));
entry = control.sent.findLast((m) => m.type === "log").entry;
assert.deepEqual([entry.status, entry.output], ["done", "Kicked Alice:\ntest"]);
assert.equal(JSON.parse(rows.get("server:log")).length, 1, "one log entry, updated in place");

await send(control, { id: "bad", action: "command", command: "" });
assert.equal(serverLink.sent.at(-1).id, "k1", "rejected actions never reach the server");

// status over the server's own socket works like a push
await instance.webSocketMessage(serverLink, JSON.stringify({ type: "status", server: { name: "Test" }, players: [{ uuid: ALICE, name: "Alice", online: true, position: [1, 1, 1] }] }));
assert.deepEqual(control.sent.findLast((m) => m.type === "server").players[0].position, [1, 1, 1]);
assert.equal(control.sent.findLast((m) => m.type === "server").connected, true);
assert.equal(control.sent.findLast((m) => m.type === "server").control, true);
assert.equal(admin.sent.findLast((m) => m.type === "server").control, false);

// rate limit
for (let i = 0; i < 40; i++) await send(control, { id: `spam${i}`, action: "heal", uuid: ALICE });
assert.equal(control.sent.at(-1).error, "Too many actions; wait a minute.");

// the server's socket closing tells viewers
await instance.webSocketClose(serverLink, 1000, "bye");
serverLink.closed = { code: 1000 };
assert.deepEqual(control.sent.at(-1), { type: "link", connected: false });

// --- a player's view of the world: pushed by the server, fetched with a key
const pushScreens = (mode, players) => call("/server/status", {
  method: "POST", headers: { Authorization: "Bearer push-secret", "Content-Type": "application/json" },
  body: JSON.stringify({ server: { name: "Test", tps: 20, player_screens: mode }, players }),
});
const both = [{ uuid: ALICE, name: "Alice", online: true }, { uuid: BOB, name: "Bob", online: true }];
const putShot = (uuid, body, token = "push-secret") => call(`/server/shot?uuid=${uuid}`, {
  method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "image/jpeg" }, body,
});
const getShot = (uuid, viewerKey) => call(`/server/shot?key=${encodeURIComponent(viewerKey)}&uuid=${uuid}`);
const frame = new Uint8Array([0xff, 0xd8, 1, 2, 3]);

assert.equal((await putShot(ALICE, frame, "agent-secret")).status, 401, "the agent's token can't push frames");
assert.equal((await putShot(ALICE, frame, "admin-key-123")).status, 401, "nor can an admin key");
assert.equal((await putShot("not-a-uuid", frame)).status, 400);
assert.equal((await putShot(ALICE, new Uint8Array(0))).status, 400, "an empty frame is not a frame");
assert.equal((await putShot(ALICE, new Uint8Array(600 * 1024))).status, 413, "and one this big is a bug or an attempt");

// off: the server never collected any, and nothing is served even to the control key
await pushScreens("off", both);
await putShot(ALICE, frame);
assert.equal((await getShot(ALICE, "control-key-789")).status, 404, "with player_screens off there is nothing to see");

// control: the control key and the player themselves, nobody else
await pushScreens("control", both);
const stored = await putShot(ALICE, frame);
assert.equal(stored.status, 200);
assert.equal((await stored.json()).bytes, frame.byteLength);
const served = await getShot(ALICE, "control-key-789");
assert.equal(served.status, 200);
assert.equal(served.headers.get("Content-Type"), "image/jpeg");
assert.deepEqual(new Uint8Array(await served.arrayBuffer()), frame);
assert.equal((await getShot(ALICE, "admin-key-123")).status, 404, "an admin key can look but not watch, under control");
assert.equal((await getShot(ALICE, aliceKey)).status, 200, "a player may always see their own");
assert.equal((await getShot(BOB, aliceKey)).status, 404, "and only their own");
assert.equal((await call(`/server/shot?uuid=${ALICE}`)).status, 401, "no key, nothing");

// what the page is told: only a viewer who may see it learns a frame exists
assert.equal(mod.canSeeScreen({ role: "admin", control: true }, "control", ALICE), true);
assert.equal(mod.canSeeScreen({ role: "admin" }, "control", ALICE), false);
assert.equal(mod.canSeeScreen({ role: "admin" }, "admin", ALICE), true);
assert.equal(mod.canSeeScreen({ role: "player", uuid: ALICE }, "admin", BOB), false);
assert.equal(mod.canSeeScreen({ role: "admin", control: true }, "off", ALICE), false);
control.sent.length = 0; admin.sent.length = 0; alice.sent.length = 0;
await putShot(ALICE, frame);
assert.equal(control.sent.at(-1).type, "screen", "control is told a new frame arrived");
assert.equal(control.sent.at(-1).uuid, ALICE);
assert.deepEqual(admin.sent, [], "a look-only admin is told nothing under control");
assert.equal(alice.sent.at(-1).type, "screen", "and the player hears about their own");
const state = await instance.state();
assert.ok(mod.viewFor({ role: "admin", control: true }, state).players.find((p) => p.uuid === ALICE).screen_at > 0);
assert.equal(mod.viewFor({ role: "admin" }, state).players.find((p) => p.uuid === ALICE).screen_at, undefined);

// admin: every admin key too
await pushScreens("admin", both);
assert.equal((await getShot(ALICE, "admin-key-123")).status, 200);

// --- watching: the server is told which players someone actually has open
// the link from the action tests was closed; frames need one of their own
const frameLink = socket(null, null, "server-link");
const watchers = socket({ role: "admin", control: true }); watchers.hash = await secretsHash();
frameLink.sent.length = 0;
await instance.webSocketMessage(watchers, JSON.stringify({ type: "watch", uuid: ALICE }));
assert.deepEqual(frameLink.sent.at(-1), { type: "watch", uuids: [ALICE] });
await instance.webSocketMessage(watchers, JSON.stringify({ type: "watch", uuid: ALICE }));
assert.equal(frameLink.sent.length, 1, "saying the same thing twice doesn't wake the server");
await instance.webSocketMessage(watchers, JSON.stringify({ type: "watch", uuid: "not-a-uuid" }));
assert.deepEqual(frameLink.sent.at(-1), { type: "watch", uuids: [] }, "a bad uuid stops the watching rather than starting any");
await instance.webSocketMessage(watchers, JSON.stringify({ type: "watch", uuid: BOB }));
assert.deepEqual(frameLink.sent.at(-1), { type: "watch", uuids: [BOB] });
await instance.webSocketClose(watchers, 1000, "gone");
assert.deepEqual(frameLink.sent.at(-1), { type: "watch", uuids: [] }, "the last viewer leaving stops the asking");

// under control, a look-only admin can't make the server ask anyone for anything
await pushScreens("control", both);
const looker = socket({ role: "admin" }); looker.hash = await secretsHash();
frameLink.sent.length = 0;
await instance.webSocketMessage(looker, JSON.stringify({ type: "watch", uuid: ALICE }));
assert.deepEqual(frameLink.sent, [], "nobody who may not see a frame can start one being captured");

// --- a player the server stops reporting takes their frame with them
assert.ok(rows.has(`shot:${ALICE}`));
await pushScreens("control", [{ uuid: BOB, name: "Bob", online: true }]);
assert.ok(!rows.has(`shot:${ALICE}`), "their frame is deleted, not left sitting in storage");
assert.deepEqual(JSON.parse(rows.get("server:shots")), {});
assert.equal((await getShot(ALICE, "control-key-789")).status, 404);

// the routes: the server connects with its token only, and viewers can't pretend to be it
assert.equal((await call("/server/connect", { headers: { Upgrade: "websocket", Authorization: "Bearer agent-secret" } })).status, 401);
assert.equal((await call("/server/connect", { headers: { Upgrade: "websocket" } })).status, 401);
let forwarded;
instance.fetch = async (request) => { forwarded = request; return new Response(null, { status: 204 }); };
await call(`/server/live?key=admin-key-123`, { headers: { Upgrade: "websocket", "X-Server-Link": "1", "X-Viewer": '{"role":"admin","control":true}' } });
assert.equal(forwarded.headers.get("X-Server-Link"), null, "a client's X-Server-Link header is dropped");
assert.deepEqual(JSON.parse(forwarded.headers.get("X-Viewer")), { role: "admin" }, "the Worker decides the role, not the client");

// --- without its secrets the server tool is off
const bare = { PUSH_TOKEN: "agent-secret", SERVER_STORE: env.SERVER_STORE };
const bareCall = (path, init) => {
  const url = new URL(`https://w.example${path}`);
  return mod.handleServer(new Request(url, init), bare, url, url.pathname);
};
for (const token of ["", "agent-secret", "undefined"]) {
  assert.equal((await bareCall("/server/status", { method: "POST", headers: { Authorization: `Bearer ${token}` }, body: "{}" })).status, 401);
  assert.equal((await bareCall("/server/links", { method: "POST", headers: { Authorization: `Bearer ${token}` }, body: '{"admin":true}' })).status, 401);
}
assert.equal(await mod.viewerFor(bare, "undefined"), null);
assert.equal(await mod.viewerFor(bare, `${ALICE}.anything`), null);

console.log("ALL SERVER TOOL WORKER TESTS PASSED");
