// The demo fixture: one invented status, and the frame archive that goes with it.
//
// Nothing in here touches the browser — no location, no fetch, no Image — because
// it has two readers. The page imports it through demo.js for ?demo, and
// tools/fake_agent.py imports it through Node to push the same data at a local
// Worker. One fixture, so a card that works in one can't quietly break in the other.
//
// None of it is real.

// Kept in step with mod/gradle.properties by hand: the fixture is what the page is
// meant to look like, so a stale version here quietly advertises an old jar.
export const MOD_VERSION = "1.4.0";

// Where the imagery sits, relative to the page. `demo` is injected at deploy from
// the `demo` branch; the local preview pushes real images to the Worker instead.
export const DEMO_BASE = "demo";

const ago = (minutes) => Date.now() - minutes * 60000;

const item = (id, count, slot, extra = {}) => ({ id: `minecraft:${id}`, count, slot, ...extra });

export function demoStatus({ loggedOut = false, have = { frames: true, panorama: true },
                             base = DEMO_BASE } = {}) {
  const seen = ago(9);
  return {
    generated_at: Date.now(),
    ...(have.frames ? { screenshot_at: ago(2), demo_shot: `${base}/frame-7.webp` } : {}),
    ...(loggedOut ? {
      // one timestamp for both: main.js only shows the panorama when the logout and
      // the panorama are within PANORAMA_WINDOW_MS of each other
      last_seen: { mode: "singleplayer", at: seen, dimension: "minecraft:overworld",
                   position: [128.4, 71, -338.9] },
      ...(have.panorama ? { panorama_at: seen, demo_panorama: `${base}/panorama.webp` } : {}),
    } : {}),
    system: {
      cpu: "13th Gen Intel Core i5-13600K", cpu_cores: 20, cpu_percent: 9,
      gpu: "Intel Arc B580 Graphics", gpu_percent: 67,
      mem_used: 19 * 2 ** 30, mem_total: 64 * 2 ** 30,
      vram_used: 2.2 * 2 ** 30, vram_total: 12 * 2 ** 30,
      os: "Windows 11", uptime_seconds: 20880, cpu_temp: 54, gpu_temp: 71,
    },
    player: {
      online: !loggedOut, name: "nyannoying", mode: "singleplayer",
      health: 18, foodlevel: 17, xplevel: 34, xpp: 0.42,
      dimension: "minecraft:overworld", position: [128.4, 71, -338.9], rotation: [117.5, 8.2],
      mod_version: MOD_VERSION,
      game: {
        fps: 142, mspt: 4.2, tps: 20, mem_used_mb: 1288, mem_max_mb: 4096,
        entities: 1843, chunks: 729, render_distance: 16,
      },
      world: {
        name: "New World", weather: "clear", biome: "minecraft:forest",
        difficulty: "hard", hardcore: true, game_mode: "survival",
        armor: 14, day: 132, time: 6180,
      },
      hotbar: [
        item("diamond_pickaxe", 1, 0, { damage: 412, max_damage: 1561,
          enchantments: [{ id: "minecraft:efficiency", level: 5 }, { id: "minecraft:mending", level: 1 }] }),
        item("diamond_sword", 1, 1, { damage: 103, max_damage: 1561 }),
        item("torch", 64, 2), item("cobblestone", 64, 3), item("oak_planks", 37, 4),
        item("cooked_beef", 12, 5), item("bucket", 1, 6), item("ender_pearl", 3, 7),
        item("shield", 1, 8),
      ],
      inventory: [
        item("iron_ingot", 34, 9), item("gold_ingot", 11, 10), item("redstone", 48, 11),
        item("lapis_lazuli", 22, 12), item("coal", 61, 13), item("raw_copper", 27, 14),
        item("glass", 16, 20), item("wheat", 9, 21), item("bone", 14, 22),
        // colours come from the stack, not the id: every potion is minecraft:potion
        item("potion", 1, 23, { color: 0xF82423 }),          // healing, red
        item("potion", 1, 24, { color: 0x1F1FA1 }),          // night vision, blue
        item("leather_chestplate", 1, 25, { color: 0x4AA02C, damage: 12, max_damage: 81 }),
      ],
      armor: {
        head: item("diamond_helmet", 1, 0, { damage: 40, max_damage: 363 }),
        chest: item("diamond_chestplate", 1, 0, { damage: 88, max_damage: 528 }),
        legs: item("diamond_leggings", 1, 0, { damage: 12, max_damage: 495 }),
        feet: item("diamond_boots", 1, 0, { damage: 150, max_damage: 429,
          enchantments: [{ id: "minecraft:feather_falling", level: 4 }] }),
      },
      offhand: item("totem_of_undying", 1, 0),
      mods: [
        { id: "fabric-api", name: "Fabric API", version: "0.160.0+26.2" },
        { id: "sodium", name: "Sodium", version: "0.6.13" },
        { id: "iris", name: "Iris Shaders", version: "1.8.8" },
        { id: "create", name: "Create", version: "6.0.4" },
        { id: "byg", name: "Oh The Biomes You'll Go", version: "3.0.1" },
        { id: "mc-status", name: "mc-status", version: MOD_VERSION },
      ],
      stats: {
        play_time: 20 * 3600 * 20, deaths: 7, time_since_death: 4 * 3600 * 20,
        mob_kills: 1204, damage_dealt: 18400, damage_taken: 9100, jumps: 24310,
        fish_caught: 18, animals_bred: 22, traded: 41, enchanted: 16, slept: 48, raids_won: 2,
        travel_cm: 51_400_000, flown_cm: 9_100_000,
        blocks_mined: 38210, items_crafted: 4102,
        top_mined: { id: "minecraft:stone", count: 14203 },
        top_crafted: { id: "minecraft:oak_planks", count: 980 },
        top_killed: { id: "minecraft:zombie", count: 312 },
        top_killed_by: { id: "minecraft:creeper", count: 4 },
      },
      advancements: {
        done: 68, total: 126,
        tabs: { story: [16, 16], nether: [12, 23], end: [3, 9], adventure: [24, 47], husbandry: [13, 31] },
        recent: [
          { id: "minecraft:nether/find_fortress", title: "A Terrible Fortress",
            description: "Break your way into a Nether Fortress", icon: "minecraft:nether_bricks", at: ago(22) },
          { id: "minecraft:adventure/trade", title: "What a Deal!",
            description: "Successfully trade with a Villager", icon: "minecraft:emerald", at: ago(140) },
        ],
        in_progress: [
          { id: "minecraft:adventure/adventuring_time", title: "Adventuring Time",
            description: "Discover every biome", icon: "minecraft:diamond_boots", percent: 41.8, progress: "23/55" },
        ],
        checklists: [
          { id: "minecraft:adventure/adventuring_time", title: "Adventuring Time",
            description: "Discover every biome", icon: "minecraft:diamond_boots",
            done: ["minecraft:plains", "minecraft:forest", "minecraft:desert", "minecraft:taiga"],
            missing: ["minecraft:jungle", "minecraft:badlands", "minecraft:deep_dark", "minecraft:lush_caves"] },
          { id: "minecraft:husbandry/balanced_diet", title: "A Balanced Diet",
            description: "Eat everything that is edible", icon: "minecraft:apple",
            done: ["minecraft:apple", "minecraft:bread", "minecraft:cooked_beef"],
            missing: ["minecraft:pufferfish", "minecraft:spider_eye", "minecraft:chorus_fruit"] },
        ],
      },
    },
    events: [
      { at: ago(98), kind: "session", text: "Started playing", icon: "minecraft:grass_block" },
      { at: ago(74), kind: "milestone", text: "1,000 mobs defeated", icon: "minecraft:iron_sword" },
      { at: ago(51), kind: "dimension", text: "Entered the Nether", icon: "minecraft:netherrack" },
      { at: ago(22), kind: "advancement", text: "Unlocked A Terrible Fortress", icon: "minecraft:nether_bricks" },
      { at: ago(14), kind: "death", text: "Died", icon: "minecraft:bone" },
      { at: ago(9), kind: "dimension", text: "Entered the Overworld", icon: "minecraft:grass_block" },
    ],
    playtime: [
      { date: "2026-09-09", seconds: 0 }, { date: "2026-09-10", seconds: 3120 },
      { date: "2026-09-11", seconds: 900 }, { date: "2026-09-12", seconds: 0 },
      { date: "2026-09-13", seconds: 5880 }, { date: "2026-09-14", seconds: 1440 },
      { date: "2026-09-15", seconds: 2940 },
    ],
    curses: [
      { name: "gpu on fire", metric: "gpu_temp", value: 83.2, at: ago(35) },
      { name: "go to bed", metric: "uptime_hours", value: 13, at: ago(210) },
    ],
  };
}

const today = () => new Date().toLocaleDateString("en-CA");  // the YYYY-MM-DD archive.py uses

export function demoShots({ base = DEMO_BASE } = {}) {
  const day = today();
  const frames = [0, 1, 2, 3, 4, 5, 6, 7].map((index) => ({
    at: ago((8 - index) * 45),
    day,
    file: `frame-${index}.webp`,
    thumb: `frame-${index}.t.webp`,
    // Matches the committed captures, which are all Overworld. The caption sits under
    // the picture, so a dimension that disagrees with it is visible; keep the two in
    // step if you replace site/demo/ with frames from somewhere else.
    dimension: "minecraft:overworld",
    position: [128 + index * 40, 71, -338 - index * 17],
  }));
  return { frames, panoramas: { [day]: "panorama.webp" }, base };
}
