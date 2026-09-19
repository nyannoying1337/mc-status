package mcstatus.server;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import mcstatus.common.ScreenPayloads;
import net.fabricmc.fabric.api.networking.v1.ServerPlayNetworking;
import net.minecraft.ChatFormatting;
import net.minecraft.network.chat.Component;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;

/**
 * A player's view of the world, for the admin page.
 *
 * <p>Only players whose own client opted in can send one: the mod registers its
 * receiver only when {@code share_screen_with_server} is on, so
 * {@code canSend} below is exactly "this player agreed". A vanilla client never
 * can, and this never asks it for anything. What arrives is the world as their
 * character sees it, taken before their game draws any GUI — never their chat,
 * their HUD or anything outside the game window.
 *
 * <p>Frames are only asked for while somebody actually has that player's page
 * open: the Worker sends the list down the admin link. With nobody looking,
 * nothing is captured on anyone's machine and nothing is stored.
 */
final class PlayerScreens {
	static final String OFF = "off";
	private static final int QUALITY = 70;
	/** A frame that stops arriving halfway is dropped rather than kept waiting. */
	private static final long ASSEMBLY_TIMEOUT_MS = 30_000;
	private static final long SWEEP_MS = 60_000;

	/** The pieces of one frame, until they're all here. */
	private static final class Assembly {
		private final int frame;
		private final byte[][] parts;
		private final long startedAt = System.currentTimeMillis();
		private int have;
		private int bytes;

		private Assembly(int frame, int total) {
			this.frame = frame;
			this.parts = new byte[total][];
		}
	}

	private final HttpClient http;
	private final String workerUrl;
	private final String pushToken;
	private final String mode;
	private final long intervalMs;
	private final int width;

	private final Map<UUID, Assembly> building = new HashMap<>();
	private final Map<UUID, Long> askedAt = new HashMap<>();
	private final Map<UUID, Integer> askedFrame = new HashMap<>();
	private final Set<UUID> told = new HashSet<>();
	private volatile Set<UUID> wanted = Set.of();
	private long sweptAt;
	private int frames;

	PlayerScreens(HttpClient http, String workerUrl, String pushToken, String mode, int intervalSeconds, int width) {
		this.http = http;
		this.workerUrl = workerUrl;
		this.pushToken = pushToken;
		this.mode = mode;
		this.intervalMs = intervalSeconds * 1000L;
		this.width = width;
	}

	boolean off() {
		return OFF.equals(mode);
	}

	String mode() {
		return mode;
	}

	/** Whether this player's client agreed to send frames at all. */
	boolean capable(ServerPlayer player) {
		return !off() && ServerPlayNetworking.canSend(player, ScreenPayloads.Request.TYPE);
	}

	void register() {
		if (off()) return;
		ServerPlayNetworking.registerGlobalReceiver(ScreenPayloads.Chunk.TYPE,
			(payload, context) -> onChunk(context.player(), payload));
		McStatusServer.LOG.info("player screens are on ({} may see them); only players who opted in are ever asked", mode);
	}

	/** From the Worker: whose page somebody currently has open. */
	void watch(JsonObject message) {
		JsonElement uuids = message.get("uuids");
		if (uuids == null || !uuids.isJsonArray()) return;
		Set<UUID> found = new HashSet<>();
		for (JsonElement element : uuids.getAsJsonArray()) {
			try {
				found.add(UUID.fromString(element.getAsString()));
			} catch (RuntimeException ignored) {
				// not a uuid; the Worker checks these, but this is the side that acts
			}
		}
		wanted = Set.copyOf(found);
	}

	/** Server thread. Asks the watched players' clients for a frame, on their turn. */
	void tick(MinecraftServer server) {
		if (off()) return;
		long now = System.currentTimeMillis();
		building.values().removeIf(assembly -> now - assembly.startedAt > ASSEMBLY_TIMEOUT_MS);
		if (now - sweptAt > SWEEP_MS) {
			sweptAt = now;
			forget(server);
		}
		for (UUID id : wanted) {
			ServerPlayer player = server.getPlayerList().getPlayer(id);
			if (player == null || !capable(player)) continue;
			Long last = askedAt.get(id);
			if (last != null && now - last < intervalMs) continue;
			askedAt.put(id, now);
			int frame = ++frames;
			askedFrame.put(id, frame);
			tell(player);
			ServerPlayNetworking.send(player, new ScreenPayloads.Request(frame, width, QUALITY));
		}
	}

	/**
	 * Told once a session, the first time their client is actually asked — so it
	 * says "somebody is looking now", not "somebody might one day".
	 */
	private void tell(ServerPlayer player) {
		if (!told.add(player.getUUID())) return;
		player.sendSystemMessage(Component.literal(
			"mc-status: an admin is looking at your view of the world. Turn it off with "
				+ "share_screen_with_server=false in config/mc-status.properties.")
			.withStyle(ChatFormatting.GRAY));
	}

	/** Players who left keep nothing here, and are told again when they come back. */
	private void forget(MinecraftServer server) {
		Set<UUID> online = new HashSet<>();
		for (ServerPlayer player : server.getPlayerList().getPlayers()) online.add(player.getUUID());
		told.retainAll(online);
		askedAt.keySet().retainAll(online);
		askedFrame.keySet().retainAll(online);
		building.keySet().retainAll(online);
	}

	/**
	 * Server thread, from a client. Everything about the chunk is checked here:
	 * a client can send whatever it likes, and this is the side that acts on it.
	 */
	private void onChunk(ServerPlayer player, ScreenPayloads.Chunk chunk) {
		if (off()) return;
		UUID id = player.getUUID();
		// nothing was asked of them, or it wasn't this frame
		if (!wanted.contains(id) || !Integer.valueOf(chunk.frame()).equals(askedFrame.get(id))) return;
		if (chunk.total() < 1 || chunk.total() > ScreenPayloads.MAX_CHUNKS) return;
		if (chunk.index() < 0 || chunk.index() >= chunk.total()) return;
		if (chunk.data().length == 0 || chunk.data().length > ScreenPayloads.MAX_CHUNK_BYTES) return;

		Assembly assembly = building.get(id);
		if (assembly == null || assembly.frame != chunk.frame() || assembly.parts.length != chunk.total()) {
			assembly = new Assembly(chunk.frame(), chunk.total());
			building.put(id, assembly);
		}
		if (assembly.parts[chunk.index()] != null) return;  // a repeat; take the first
		assembly.parts[chunk.index()] = chunk.data();
		assembly.have++;
		assembly.bytes += chunk.data().length;
		if (assembly.bytes > ScreenPayloads.MAX_FRAME_BYTES) {
			building.remove(id);
			return;
		}
		if (assembly.have < assembly.parts.length) return;
		building.remove(id);
		push(id, join(assembly));
	}

	private static byte[] join(Assembly assembly) {
		byte[] frame = new byte[assembly.bytes];
		int at = 0;
		for (byte[] part : assembly.parts) {
			System.arraycopy(part, 0, frame, at, part.length);
			at += part.length;
		}
		return frame;
	}

	/** Off the server thread: the frame goes up as its own request, not in the status. */
	private void push(UUID id, byte[] frame) {
		HttpRequest request = HttpRequest.newBuilder(URI.create(workerUrl + "/server/shot?uuid=" + id))
			.timeout(Duration.ofSeconds(20))
			.header("Authorization", "Bearer " + pushToken)
			.header("Content-Type", "image/jpeg")
			.POST(HttpRequest.BodyPublishers.ofByteArray(frame))
			.build();
		http.sendAsync(request, HttpResponse.BodyHandlers.discarding()).whenComplete((response, err) -> {
			if (err != null) McStatusServer.LOG.warn("frame push failed: {}", err.toString());
			else if (response.statusCode() >= 300) McStatusServer.LOG.warn("frame refused: HTTP {}", response.statusCode());
			else McStatusServer.LOG.debug("pushed a {} byte frame for {}", frame.length, id);
		});
	}
}
