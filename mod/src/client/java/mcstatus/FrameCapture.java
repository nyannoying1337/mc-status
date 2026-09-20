package mcstatus;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.file.Path;

import com.mojang.blaze3d.pipeline.RenderTarget;
import com.mojang.blaze3d.pipeline.TextureTarget;
import com.mojang.blaze3d.platform.NativeImage;
import com.mojang.blaze3d.systems.RenderSystem;
import com.mojang.blaze3d.textures.GpuTexture;
import net.minecraft.client.Minecraft;
import org.lwjgl.system.MemoryUtil;

/**
 * Grabs a frame of the world without touching the game's own frame.
 *
 * <p>Vanilla screenshots read back the full frame and loop over every pixel on
 * the render thread; measured on Intel OpenGL at 854×480 that froze the game for
 * ~600 ms per capture. This runs right after the world is drawn (so before the
 * HUD and chat) and reads the frame back with {@link GpuReadback}: about 1 ms on
 * the render thread, the rest off it. Anything that wants the frame smaller gets
 * it shrunk off the render thread too.
 *
 * <p>Two things ask for frames: the file the agent publishes to your own page,
 * and — only if you turned it on — a server asking for your view for its admin
 * page ({@link ScreenShare}). They are different sizes and different switches,
 * but both are read back whole, and at most one readback is ever in flight, so
 * one target serves both.
 */
final class FrameCapture {
	private static final int TIMINGS_LOGGED_AT_INFO = 3;

	/** What a finished capture is handed to. The image is yours to close. */
	@FunctionalInterface
	interface FrameConsumer {
		/** {@code image} is null when the capture failed. */
		void accept(NativeImage image, int width, int height);
	}

	/** The frame is copied here to be read back, rather than read from under the game. */
	private TextureTarget target;
	private final Path file;
	private final ModConfig config;
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
			grab(main, source, Math.min(shareWidth, main.width), waiting, false);
			return;
		}

		long now = System.currentTimeMillis();
		// No frames from servers unless you've said so: other people's builds and
		// nametags are in the shot, and they aren't ours to publish by default.
		if (client.getSingleplayerServer() == null && !config.shareServerWorld) return;
		if (!requested && now < nextCaptureAt) return;
		requested = false;
		nextCaptureAt = now + config.captureIntervalSeconds * 1000L;
		grab(main, source, Math.min(config.captureWidth, main.width), this::writeToDisk, true);
	}

	private void writeToDisk(NativeImage image, int width, int height) {
		if (image == null) return;
		try (image) {
			AtomicFiles.write(file, image::writeToFile);
		} catch (IOException err) {
			McStatusClient.LOG.warn("could not write {}: {}", file, err.getMessage());
		}
	}

	/**
	 * Reads the frame back and hands it over at {@code width}.
	 *
	 * <p>This used to scale on the GPU, by clearing a smaller target to opaque
	 * black and calling {@code blitAndBlendToTexture}. That blends, weighted by
	 * the source's alpha, and the main render target only has alpha where
	 * something was actually drawn: the stretch past the render distance below
	 * the horizon is only the frame's clear colour, so it blended to nothing and
	 * came out as the black the target was cleared to. You never see it on
	 * screen, because nothing blends the frame on its way to the monitor.
	 *
	 * <p>So the frame is copied out whole — the same straight copy the panorama
	 * capture uses — and shrunk afterwards on the io pool if anything asked for it
	 * smaller. capture_width defaults to 1920, so for most windows that is no
	 * shrinking at all; the one caller that always wants less is a server watching
	 * a player, at most every 15 seconds. Measured at 1280×685 down to 480×257:
	 * about 3 ms, none of it on the render thread.
	 */
	private void grab(RenderTarget main, GpuTexture source, int width, FrameConsumer done, boolean logTimings) {
		int sourceWidth = main.width;
		int sourceHeight = main.height;
		int height = Math.max(1, Math.round(sourceHeight * (float) width / sourceWidth));
		try {
			if (target == null || target.width != sourceWidth || target.height != sourceHeight) {
				if (target != null) target.destroyBuffers();
				target = new TextureTarget("mc-status capture", sourceWidth, sourceHeight, false, source.getFormat());
			}
			RenderSystem.getDevice().createCommandEncoder().copyTextureToTexture(
				source, target.getColorTexture(), 0, 0, 0, 0, 0, sourceWidth, sourceHeight);
			inFlight = GpuReadback.read(target.getColorTexture(), sourceWidth, sourceHeight, (image, timings) -> {
				Minecraft.getInstance().execute(() -> inFlight = false);
				if (image != null) {
					String line = String.format("capture %dx%d: %.2f ms on the render thread, %.2f ms copying off it",
						width, height, timings.renderThreadMs(), timings.copyMs());
					if (logTimings && captures++ < TIMINGS_LOGGED_AT_INFO) McStatusClient.LOG.info(line);
					else McStatusClient.LOG.debug(line);
				}
				if (image == null || (width == sourceWidth && height == sourceHeight)) {
					done.accept(image, width, height);
					return;
				}
				NativeImage smaller = null;
				try (image) {
					smaller = shrink(image, sourceWidth, sourceHeight, width, height);
				} catch (RuntimeException err) {
					McStatusClient.LOG.debug("could not shrink capture: {}", err.toString());
				}
				done.accept(smaller, width, height);
			});
			if (!inFlight) done.accept(null, width, height);
		} catch (RuntimeException err) {
			// e.g. mid-resize; the next interval tries again
			inFlight = false;
			McStatusClient.LOG.debug("skipped capture: {}", err.toString());
			done.accept(null, width, height);
		}
	}

	/**
	 * Box-averages an upright RGBA image down to {@code width}×{@code height}.
	 *
	 * <p>Reads and writes the pixels through the image's own memory rather than
	 * NativeImage's accessors, the same way {@link Jpeg} does, so nothing here
	 * depends on which colour order those accessors use this version.
	 */
	private static NativeImage shrink(NativeImage source, int sourceWidth, int sourceHeight, int width, int height) {
		NativeImage out = new NativeImage(width, height, false);
		try {
			ByteBuffer src = MemoryUtil.memByteBuffer(source.getPointer(),
				Math.multiplyExact(Math.multiplyExact(sourceWidth, sourceHeight), 4));
			ByteBuffer dst = MemoryUtil.memByteBuffer(out.getPointer(),
				Math.multiplyExact(Math.multiplyExact(width, height), 4));
			for (int y = 0; y < height; y++) {
				int fromY = y * sourceHeight / height;
				int toY = Math.max(fromY + 1, (y + 1) * sourceHeight / height);
				for (int x = 0; x < width; x++) {
					int fromX = x * sourceWidth / width;
					int toX = Math.max(fromX + 1, (x + 1) * sourceWidth / width);
					int red = 0, green = 0, blue = 0, counted = 0;
					for (int sourceY = fromY; sourceY < toY; sourceY++) {
						int row = sourceY * sourceWidth * 4;
						for (int sourceX = fromX; sourceX < toX; sourceX++) {
							int at = row + sourceX * 4;
							red += src.get(at) & 0xFF;
							green += src.get(at + 1) & 0xFF;
							blue += src.get(at + 2) & 0xFF;
							counted++;
						}
					}
					int at = (y * width + x) * 4;
					dst.put(at, (byte) (red / counted));
					dst.put(at + 1, (byte) (green / counted));
					dst.put(at + 2, (byte) (blue / counted));
					dst.put(at + 3, (byte) 0xFF);
				}
			}
		} catch (RuntimeException err) {
			out.close();
			throw err;
		}
		return out;
	}
}
