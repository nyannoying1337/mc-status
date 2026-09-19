package mcstatus;

import java.io.IOException;
import java.nio.file.Path;

import com.mojang.blaze3d.pipeline.RenderTarget;
import com.mojang.blaze3d.pipeline.TextureTarget;
import com.mojang.blaze3d.platform.NativeImage;
import com.mojang.blaze3d.systems.RenderSystem;
import com.mojang.blaze3d.textures.GpuTexture;
import net.minecraft.client.Minecraft;
import org.joml.Vector4f;

/**
 * Grabs a frame of the world without touching the game's own frame.
 *
 * <p>Vanilla screenshots read back the full frame and loop over every pixel on
 * the render thread; measured on Intel OpenGL at 854×480 that froze the game for
 * ~600 ms per capture. This runs right after the world is drawn (so before the
 * HUD and chat), scales it on the GPU to at most capture_width, and reads it
 * back with {@link GpuReadback}: about 1 ms on the render thread, the rest off it.
 *
 * <p>Two things ask for frames: the file the agent publishes to your own page,
 * and — only if you turned it on — a server asking for your view for its admin
 * page ({@link ScreenShare}). They are different sizes and different switches,
 * so each keeps its own scaled target and at most one readback is ever in flight.
 */
final class FrameCapture {
	private static final int TIMINGS_LOGGED_AT_INFO = 3;

	/** What a finished capture is handed to. The image is yours to close. */
	@FunctionalInterface
	interface FrameConsumer {
		/** {@code image} is null when the capture failed. */
		void accept(NativeImage image, int width, int height);
	}

	/** A reusable scaled render target, one per purpose since the sizes differ. */
	private static final class Slot {
		private TextureTarget target;

		TextureTarget sized(int width, int height, GpuTexture source) {
			if (target == null || target.width != width || target.height != height) {
				if (target != null) target.destroyBuffers();
				target = new TextureTarget("mc-status capture", width, height, false, source.getFormat());
			}
			return target;
		}
	}

	private final Path file;
	private final ModConfig config;
	private final Slot page = new Slot();
	private final Slot share = new Slot();
	private long nextCaptureAt = System.currentTimeMillis() + 10_000;
	private boolean requested;
	private boolean inFlight;
	private int captures;
	// asked for on the client thread, picked up on the render thread
	private volatile int shareWidth;
	private volatile FrameConsumer shareDone;

	FrameCapture(Path file, ModConfig config) {
		this.file = file;
		this.config = config;
	}

	/** Capture on the next rendered frame, e.g. when the pause menu opens. */
	void requestSoon() {
		requested = true;
	}

	/**
	 * Client thread: hand the next rendered frame to {@code done} at this width.
	 * Nothing is written to disk. This path deliberately isn't gated by
	 * share_server_world, which is about what reaches your own page.
	 */
	void requestForServer(int width, FrameConsumer done) {
		shareWidth = width;
		shareDone = done;
	}

	/** Render thread, after the level pass and before any GUI. */
	void onLevelRendered(Minecraft client) {
		if (inFlight || PanoramaCapture.isRendering() || client.level == null || client.player == null) return;

		RenderTarget main = client.gameRenderer.mainRenderTarget();
		GpuTexture source = main.getColorTexture();
		if (source == null || main.width <= 0 || main.height <= 0) return;

		// A server that asked goes first: it asked for one frame, once, and the
		// page's own capture comes round again on its interval anyway.
		FrameConsumer waiting = shareDone;
		if (waiting != null) {
			shareDone = null;
			grab(main, source, share, Math.min(shareWidth, main.width), waiting, false);
			return;
		}

		long now = System.currentTimeMillis();
		// No frames from servers unless you've said so: other people's builds and
		// nametags are in the shot, and they aren't ours to publish by default.
		if (client.getSingleplayerServer() == null && !config.shareServerWorld) return;
		if (!requested && now < nextCaptureAt) return;
		requested = false;
		nextCaptureAt = now + config.captureIntervalSeconds * 1000L;
		grab(main, source, page, Math.min(config.captureWidth, main.width), this::writeToDisk, true);
	}

	private void writeToDisk(NativeImage image, int width, int height) {
		if (image == null) return;
		try (image) {
			AtomicFiles.write(file, image::writeToFile);
		} catch (IOException err) {
			McStatusClient.LOG.warn("could not write {}: {}", file, err.getMessage());
		}
	}

	private void grab(RenderTarget main, GpuTexture source, Slot slot, int width, FrameConsumer done, boolean logTimings) {
		int height = Math.max(1, Math.round(main.height * (float) width / main.width));
		try {
			TextureTarget scaled = slot.sized(width, height, source);
			RenderSystem.getDevice().createCommandEncoder().clearColorTexture(scaled.getColorTexture(), new Vector4f(0, 0, 0, 1));
			main.blitAndBlendToTexture(scaled.getColorTextureView(), null);
			inFlight = GpuReadback.read(scaled.getColorTexture(), width, height, (image, timings) -> {
				Minecraft.getInstance().execute(() -> inFlight = false);
				if (image != null) {
					String line = String.format("capture %dx%d: %.2f ms on the render thread, %.2f ms copying off it",
						width, height, timings.renderThreadMs(), timings.copyMs());
					if (logTimings && captures++ < TIMINGS_LOGGED_AT_INFO) McStatusClient.LOG.info(line);
					else McStatusClient.LOG.debug(line);
				}
				done.accept(image, width, height);
			});
			if (!inFlight) done.accept(null, width, height);
		} catch (RuntimeException err) {
			// e.g. mid-resize; the next interval tries again
			inFlight = false;
			McStatusClient.LOG.debug("skipped capture: {}", err.toString());
			done.accept(null, width, height);
		}
	}
}
