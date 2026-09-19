package mcstatus.server;

import java.io.IOException;
import java.io.Reader;
import java.io.Writer;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Properties;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.stream.Stream;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import mcstatus.common.ScreenPayloads;
import mcstatus.common.Snapshots;
import net.fabricmc.api.DedicatedServerModInitializer;
import net.fabricmc.fabric.api.command.v2.CommandRegistrationCallback;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.server.players.NameAndId;
import net.minecraft.stats.ServerStatsCounter;
import net.minecraft.world.level.storage.LevelResource;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * The server side of mc-status: every player on a Fabric server, for an admin
 * page and per-player pages. Does nothing until worker_url and push_token are set.
 *
 * <p>Every few seconds, on the server thread, it snapshots the server and each
 * online player (vitals, position, inventory, statistics, advancements), plus
 * players who aren't online from their saved stats and advancements files. The
 * JSON is sent to the Worker's /server/status off the server thread.
 */
public class McStatusServer implements DedicatedServerModInitializer {
	public static final Logger LOG = LoggerFactory.getLogger("mc-status-server");
	private static final Gson GSON = new Gson();
	private static final long OFFLINE_REFRESH_MS = 5 * 60 * 1000;

	private final HttpClient http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(10)).build();
	private final AtomicBoolean sending = new AtomicBoolean();
	private final Map<UUID, JsonObject> offline = new HashMap<>();
	private volatile ServerLink link;
	private volatile PlayerScreens screens;
	private long offlineReadAt;
	private volatile long nextPushAt;
	private String workerUrl;
	private String pushToken;
	private String serverName;
	private String siteUrl;
	private int intervalSeconds;
	private boolean shareItemNames;

	@Override
	public void onInitializeServer() {
		Path file = FabricLoader.getInstance().getConfigDir().resolve("mc-status-server.properties");
		Properties props = loadConfig(file);
		workerUrl = props.getProperty("worker_url", "").replaceAll("/+$", "");
		pushToken = props.getProperty("push_token", "");
		serverName = props.getProperty("server_name", "Minecraft server");
		siteUrl = props.getProperty("site_url", "").trim();
		intervalSeconds = Math.max(10, parseInt(props.getProperty("interval_seconds", "30"), 30));
		shareItemNames = Boolean.parseBoolean(props.getProperty("share_item_names", "false").trim());
		if (workerUrl.isEmpty() || pushToken.isEmpty()) {
			LOG.warn("set worker_url and push_token in {} to start publishing", file);
			return;
		}
		ScreenPayloads.register();
		screens = new PlayerScreens(http, workerUrl, pushToken, screensMode(props),
			Math.max(15, parseInt(props.getProperty("player_screen_interval_seconds", "30"), 30)),
			Math.clamp(parseInt(props.getProperty("player_screen_width", "480"), 480), 160, 960));
		screens.register();
		ServerTickEvents.END_SERVER_TICK.register(this::tick);
		LinkCommands links = new LinkCommands(http, workerUrl, pushToken, siteUrl);
		CommandRegistrationCallback.EVENT.register((dispatcher, context, selection) -> links.register(dispatcher));
		ServerLifecycleEvents.SERVER_STARTED.register(server -> {
			ServerActions actions = new ServerActions(server, result -> {
				link.send(GSON.toJson(result));
				nextPushAt = 0;  // show the effect (kicked, banned, healed) right away
			});
			link = new ServerLink(http, workerUrl, pushToken, message -> {
				String type = message.get("type").getAsString();
				if ("action".equals(type)) actions.handle(message);
				else if ("watch".equals(type)) screens.watch(message);
			});
			link.start();
		});
		ServerLifecycleEvents.SERVER_STOPPING.register(server -> {
			if (link != null) link.stop();
		});
		LOG.info("publishing {} to {} every {}s", serverName, workerUrl, intervalSeconds);
	}

	private void tick(MinecraftServer server) {
		screens.tick(server);
		long now = System.currentTimeMillis();
		if (now < nextPushAt || sending.get()) return;
		nextPushAt = now + intervalSeconds * 1000L;
		JsonObject snapshot;
		try {
			snapshot = snapshot(server, now);
		} catch (RuntimeException err) {
			LOG.warn("snapshot failed: {}", err.toString());
			return;
		}
		// over the admin link when it's up; a plain HTTPS push otherwise
		snapshot.addProperty("type", "status");
		String body = GSON.toJson(snapshot);
		// WebSocket messages are capped at 1 MiB; a very big server pushes over HTTPS instead
		if (link != null && body.length() < 900_000 && link.send(body)) return;
		sending.set(true);
		HttpRequest request = HttpRequest.newBuilder(URI.create(workerUrl + "/server/status"))
			.timeout(Duration.ofSeconds(20))
			.header("Authorization", "Bearer " + pushToken)
			.header("Content-Type", "application/json")
			.POST(HttpRequest.BodyPublishers.ofString(body, StandardCharsets.UTF_8))
			.build();
		http.sendAsync(request, HttpResponse.BodyHandlers.discarding()).whenComplete((response, err) -> {
			sending.set(false);
			if (err != null) LOG.warn("push failed: {}", err.toString());
			else if (response.statusCode() >= 300) LOG.warn("push refused: HTTP {}", response.statusCode());
			else LOG.debug("pushed {} bytes", body.length());
		});
	}

	private JsonObject snapshot(MinecraftServer server, long now) {
		JsonObject out = new JsonObject();
		JsonObject meta = new JsonObject();
		meta.addProperty("name", serverName);
		meta.addProperty("version", server.getServerVersion());
		meta.addProperty("players_online", server.getPlayerCount());
		meta.addProperty("max_players", server.getMaxPlayers());
		double mspt = server.getAverageTickTimeNanos() / 1e6;
		meta.addProperty("mspt", Math.round(mspt * 10) / 10.0);
		meta.addProperty("tps", Math.round(Math.min(20, 1000 / Math.max(mspt, 50)) * 10) / 10.0);
		ServerLevel overworld = server.overworld();
		long time = overworld.getOverworldClockTime();
		meta.addProperty("day", time / 24000);
		meta.addProperty("time", Math.floorMod(time, 24000L));
		meta.addProperty("weather", overworld.isThundering() ? "thunder" : overworld.isRaining() ? "rain" : "clear");
		meta.addProperty("whitelist", server.getPlayerList().isUsingWhitelist());
		// The Worker needs this to know who may be shown a frame at all.
		meta.addProperty("player_screens", screens.mode());
		meta.addProperty("generated_at", now);
		out.add("server", meta);

		JsonArray players = new JsonArray();
		Set<UUID> online = new HashSet<>();
		for (ServerPlayer player : server.getPlayerList().getPlayers()) {
			online.add(player.getUUID());
			JsonObject json = onlinePlayer(server, player);
			addFlags(server, json, new NameAndId(player.getGameProfile()));
			players.add(json);
		}
		if (now - offlineReadAt > OFFLINE_REFRESH_MS) {
			offlineReadAt = now;
			readOfflinePlayers(server);
		}
		offline.forEach((id, player) -> {
			if (online.contains(id)) return;
			JsonObject json = player.deepCopy();
			addFlags(server, json, new NameAndId(id, json.get("name").getAsString()));
			players.add(json);
		});
		out.add("players", players);
		return out;
	}

	// Ban, whitelist and op status, read fresh every push, so the admin page's buttons match.
	private static void addFlags(MinecraftServer server, JsonObject json, NameAndId who) {
		var list = server.getPlayerList();
		json.addProperty("banned", list.getBans().isBanned(who));
		// PlayerList.isWhiteListed is true for everyone while the whitelist is off; ask the list itself
		json.addProperty("whitelisted", list.getWhiteList().isWhiteListed(who));
		json.addProperty("op", list.isOp(who));
	}

	private JsonObject onlinePlayer(MinecraftServer server, ServerPlayer player) {
		JsonObject json = new JsonObject();
		json.addProperty("uuid", player.getUUID().toString());
		json.addProperty("name", player.getGameProfile().name());
		json.addProperty("online", true);
		json.addProperty("health", player.getHealth());
		json.addProperty("foodlevel", player.getFoodData().getFoodLevel());
		json.addProperty("xplevel", player.experienceLevel);
		json.addProperty("xpp", player.experienceProgress);
		json.addProperty("game_mode", player.gameMode().getName());
		json.addProperty("ping", player.connection.latency());
		// So the page can say "they don't have the mod" rather than showing a blank box.
		json.addProperty("screen_capable", screens.capable(player));
		json.addProperty("dimension", player.level().dimension().identifier().toString());
		JsonArray position = new JsonArray();
		position.add(Math.round(player.getX() * 10) / 10.0);
		position.add(Math.round(player.getY() * 10) / 10.0);
		position.add(Math.round(player.getZ() * 10) / 10.0);
		json.add("position", position);
		JsonObject world = new JsonObject();
		world.addProperty("armor", player.getArmorValue());
		json.add("world", world);
		Snapshots.addInventory(json, player, shareItemNames);
		JsonObject death = Snapshots.lastDeath(player);
		if (death != null) json.add("last_death", death);
		json.add("stats", Snapshots.stats(player.getStats()));
		json.add("advancements", Snapshots.advancements(server, holder -> Snapshots.live(player.getAdvancements().getOrStartProgress(holder))));
		return json;
	}

	/** Players who aren't here: from world/stats and world/advancements, named via usercache. */
	private void readOfflinePlayers(MinecraftServer server) {
		offline.clear();
		Path statsDir = server.getWorldPath(LevelResource.PLAYER_STATS_DIR);
		Path advancementsDir = server.getWorldPath(LevelResource.PLAYER_ADVANCEMENTS_DIR);
		List<Path> files = new ArrayList<>();
		try (Stream<Path> stream = Files.list(statsDir)) {
			stream.filter(path -> path.toString().endsWith(".json")).forEach(files::add);
		} catch (IOException err) {
			return; // no stats yet
		}
		for (Path statsFile : files) {
			String name = statsFile.getFileName().toString().replace(".json", "");
			UUID id;
			try {
				id = UUID.fromString(name);
			} catch (IllegalArgumentException err) {
				continue;
			}
			JsonObject json = new JsonObject();
			json.addProperty("uuid", id.toString());
			json.addProperty("name", server.services().nameToIdCache().get(id).map(profile -> profile.name()).orElse(id.toString().substring(0, 8)));
			json.addProperty("online", false);
			try {
				json.addProperty("last_seen", Files.getLastModifiedTime(statsFile).toMillis());
				json.add("stats", Snapshots.stats(new ServerStatsCounter(server, statsFile)));
				Path advancementsFile = advancementsDir.resolve(id + ".json");
				if (Files.isRegularFile(advancementsFile)) {
					try (Reader reader = Files.newBufferedReader(advancementsFile, StandardCharsets.UTF_8)) {
						JsonObject saved = JsonParser.parseReader(reader).getAsJsonObject();
						json.add("advancements", Snapshots.advancements(server, Snapshots.fromFile(saved)));
					}
				}
			} catch (IOException | RuntimeException err) {
				LOG.debug("could not read saved data for {}: {}", id, err.toString());
			}
			offline.put(id, json);
		}
	}

	/** Anything that isn't a mode we know is off: this one is not a typo to guess at. */
	private static String screensMode(Properties props) {
		String mode = props.getProperty("player_screens", PlayerScreens.OFF).trim().toLowerCase(Locale.ROOT);
		if (mode.equals("control") || mode.equals("admin") || mode.equals(PlayerScreens.OFF)) return mode;
		LOG.warn("player_screens={} isn't off, control or admin; treating it as off", mode);
		return PlayerScreens.OFF;
	}

	private static int parseInt(String value, int fallback) {
		try {
			return Integer.parseInt(value.trim());
		} catch (NumberFormatException err) {
			return fallback;
		}
	}

	private static Properties loadConfig(Path file) {
		Properties props = new Properties();
		if (Files.isRegularFile(file)) {
			try (Reader reader = Files.newBufferedReader(file, StandardCharsets.UTF_8)) {
				props.load(reader);
			} catch (IOException err) {
				LOG.warn("could not read {}: {}", file, err.getMessage());
			}
			return props;
		}
		try (Writer writer = Files.newBufferedWriter(file, StandardCharsets.UTF_8)) {
			writer.write("""
				# mc-status server tool. `python setup.py server` writes a filled-in copy of this file.
				# The Worker to publish to, and its SERVER_PUSH_TOKEN secret.
				worker_url=
				push_token=
				# The server page (site/server.html) for /mcstatus link and /mcstatus admin.
				site_url=
				# Shown at the top of the admin page.
				server_name=Minecraft server
				# Seconds between pushes (at least 10). 30 keeps a busy server inside Cloudflare's free plan.
				interval_seconds=30
				# Custom item names can contain anything players type.
				share_item_names=false
				# Show a player's view of the world on the admin page. Off, or who may see it:
				#   control  only the control key
				#   admin    every admin key too
				# Only players who have the mod and turned share_screen_with_server on ever
				# send one, and they are told in game the first time one is asked for.
				player_screens=off
				player_screen_interval_seconds=30
				player_screen_width=480
				""");
		} catch (IOException err) {
			LOG.warn("could not write {}: {}", file, err.getMessage());
		}
		return props;
	}
}
