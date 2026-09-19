package mcstatus;

import java.io.IOException;
import java.util.ArrayDeque;
import java.util.Arrays;
import java.util.Deque;

import mcstatus.common.ScreenPayloads;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayNetworking;
import net.minecraft.client.Minecraft;

/**
 * Sending a server your view of the world, when you've said it may have it.
 *
 * <p>Registering the receiver is the opt-in. Without
 * {@code share_screen_with_server} nothing here is registered, so the server's
 * {@code canSend} is false and it never asks — the same state a vanilla client
 * is in. A server can ask no faster than {@link #MIN_INTERVAL_MS}, whatever it
 * says, and the answer goes out a couple of chunks per tick so it doesn't
 * elbow your own game traffic off the connection.
 *
 * <p>What's sent is the world as your character sees it, taken before the game
 * draws any GUI: never chat, the HUD, an open screen, or anything outside the
 * game window.
 */
final class ScreenShare {
	/** However often a server asks, this is the most it gets. */
	private static final long MIN_INTERVAL_MS = 10_000;
	/** A frame that never arrives shouldn't wedge the next one. */
	private static final long CAPTURE_TIMEOUT_MS = 30_000;
	private static final int CHUNKS_PER_TICK = 2;

	private final ModConfig config;
	private final FrameCapture capture;
	/** Client thread only; the encoder hands its chunks over through the client executor. */
	private final Deque<ScreenPayloads.Chunk> outbox = new ArrayDeque<>();
	private long startedAt;
	private boolean busy;

	ScreenShare(ModConfig config, FrameCapture capture) {
		this.config = config;
		this.capture = capture;
	}

	/** The opt-in itself: a server can only ask because we registered this. */
	void register() {
		if (!config.shareScreenWithServer) return;
		ClientPlayNetworking.registerGlobalReceiver(ScreenPayloads.Request.TYPE, (payload, context) -> onRequest(payload));
		McStatusClient.LOG.info("share_screen_with_server is on: a server may ask for your view of the world");
	}

	/** Leaving a server drops anything still queued for it. */
	void reset() {
		outbox.clear();
		busy = false;
	}

	void tick(Minecraft client) {
		if (busy && outbox.isEmpty() && System.currentTimeMillis() - startedAt > CAPTURE_TIMEOUT_MS) {
			McStatusClient.LOG.debug("gave up on a frame the server asked for");
			busy = false;
		}
		if (outbox.isEmpty()) return;
		if (!ClientPlayNetworking.canSend(ScreenPayloads.Chunk.TYPE)) {
			reset();
			return;
		}
		for (int sent = 0; sent < CHUNKS_PER_TICK && !outbox.isEmpty(); sent++) {
			ClientPlayNetworking.send(outbox.poll());
		}
		if (outbox.isEmpty()) busy = false;
	}

	private void onRequest(ScreenPayloads.Request request) {
		long now = System.currentTimeMillis();
		if (busy || now - startedAt < MIN_INTERVAL_MS) return;
		busy = true;
		startedAt = now;
		int width = Math.clamp(request.width(), 160, 960);
		int quality = Math.clamp(request.quality(), 10, 95);
		capture.requestForServer(width, (image, actualWidth, actualHeight) -> {
			if (image == null) {
				Minecraft.getInstance().execute(() -> busy = false);
				return;
			}
			byte[] jpeg;
			try (image) {
				jpeg = Jpeg.encode(image, actualWidth, actualHeight, quality);
			} catch (IOException | RuntimeException err) {
				McStatusClient.LOG.debug("could not encode the frame a server asked for: {}", err.toString());
				Minecraft.getInstance().execute(() -> busy = false);
				return;
			}
			Minecraft.getInstance().execute(() -> queue(request.frame(), jpeg));
		});
	}

	/** Client thread: split the frame up and let {@link #tick} dribble it out. */
	private void queue(int frame, byte[] jpeg) {
		int total = Math.ceilDiv(jpeg.length, ScreenPayloads.MAX_CHUNK_BYTES);
		if (jpeg.length == 0 || jpeg.length > ScreenPayloads.MAX_FRAME_BYTES || total > ScreenPayloads.MAX_CHUNKS) {
			McStatusClient.LOG.debug("frame of {} bytes is too big to send", jpeg.length);
			busy = false;
			return;
		}
		for (int index = 0; index < total; index++) {
			int from = index * ScreenPayloads.MAX_CHUNK_BYTES;
			int to = Math.min(jpeg.length, from + ScreenPayloads.MAX_CHUNK_BYTES);
			outbox.add(new ScreenPayloads.Chunk(frame, index, total, Arrays.copyOfRange(jpeg, from, to)));
		}
	}
}
