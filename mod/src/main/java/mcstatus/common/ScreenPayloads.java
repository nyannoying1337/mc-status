package mcstatus.common;

import net.fabricmc.fabric.api.networking.v1.PayloadTypeRegistry;
import net.minecraft.network.RegistryFriendlyByteBuf;
import net.minecraft.network.codec.ByteBufCodecs;
import net.minecraft.network.codec.StreamCodec;
import net.minecraft.network.protocol.common.custom.CustomPacketPayload;
import net.minecraft.resources.ResourceLocation;

/**
 * The two messages behind "show me what they're looking at": a server asking one
 * player's client for a frame, and that client sending it back in pieces.
 *
 * <p>Both sides register these, so a server can tell a client that has the mod
 * and has opted in from one that hasn't: a client only registers the receiver
 * when its own {@code share_screen_with_server} is on, and
 * {@code ServerPlayNetworking.canSend} is then exactly "this player can send a
 * frame". A vanilla client never can, and nothing is ever asked of it.
 *
 * <p>Frames are chunked because a serverbound custom payload is capped well
 * below the size of a JPEG, and because dribbling it out over several ticks
 * keeps it from competing with the player's own game traffic.
 */
public final class ScreenPayloads {
	/** Comfortably inside vanilla's serverbound payload limit, headers included. */
	public static final int MAX_CHUNK_BYTES = 16 * 1024;
	/** A frame bigger than this is a bug or an attempt; either way it's dropped. */
	public static final int MAX_FRAME_BYTES = 512 * 1024;
	public static final int MAX_CHUNKS = MAX_FRAME_BYTES / MAX_CHUNK_BYTES + 1;

	private static boolean registered;

	private ScreenPayloads() {}

	/** Called from both entrypoints; the codecs have to exist on both sides. */
	public static synchronized void register() {
		if (registered) return;
		registered = true;
		PayloadTypeRegistry.playS2C().register(Request.TYPE, Request.CODEC);
		PayloadTypeRegistry.playC2S().register(Chunk.TYPE, Chunk.CODEC);
	}

	/** Server to client: send one frame, this wide, tagged with this id. */
	public record Request(int frame, int width, int quality) implements CustomPacketPayload {
		public static final Type<Request> TYPE =
			new Type<>(ResourceLocation.fromNamespaceAndPath("mc-status", "screen_request"));
		public static final StreamCodec<RegistryFriendlyByteBuf, Request> CODEC = StreamCodec.composite(
			ByteBufCodecs.VAR_INT, Request::frame,
			ByteBufCodecs.VAR_INT, Request::width,
			ByteBufCodecs.VAR_INT, Request::quality,
			Request::new);

		@Override
		public Type<Request> type() {
			return TYPE;
		}
	}

	/** Client to server: one piece of the frame that was asked for. */
	public record Chunk(int frame, int index, int total, byte[] data) implements CustomPacketPayload {
		public static final Type<Chunk> TYPE =
			new Type<>(ResourceLocation.fromNamespaceAndPath("mc-status", "screen_chunk"));
		public static final StreamCodec<RegistryFriendlyByteBuf, Chunk> CODEC = StreamCodec.composite(
			ByteBufCodecs.VAR_INT, Chunk::frame,
			ByteBufCodecs.VAR_INT, Chunk::index,
			ByteBufCodecs.VAR_INT, Chunk::total,
			ByteBufCodecs.byteArray(MAX_CHUNK_BYTES), Chunk::data,
			Chunk::new);

		@Override
		public Type<Chunk> type() {
			return TYPE;
		}
	}
}
