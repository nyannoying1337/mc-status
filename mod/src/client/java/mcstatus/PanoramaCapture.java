package mcstatus;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import com.mojang.blaze3d.pipeline.RenderTarget;
import com.mojang.blaze3d.pipeline.TextureTarget;
import com.mojang.blaze3d.platform.NativeImage;
import com.mojang.blaze3d.platform.Window;
import com.mojang.blaze3d.systems.CommandEncoder;
import com.mojang.blaze3d.systems.RenderSystem;
import net.minecraft.client.DeltaTracker;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.client.renderer.GameRenderer;
import net.minecraft.util.Util;
import org.lwjgl.system.MemoryUtil;

/**
 * A 360° view of where you are, for the page to show once you've logged out.
 *
 * <p>Taken when the pause menu opens in singleplayer. Save &amp; Quit always goes
 * through that menu and nothing moves while paused, so the last one is where you
 * logged out, and the brief hitch of drawing the world six times is invisible.
 *
 * <p>Follows vanilla's {@code Minecraft.grabPanoramixScreenshot} (six 90° faces
 * with the camera in panoramic mode) but at 1024² instead of 4096², read back
 * asynchronously, and written as one 6×1 strip: front, right, back, left, up, down.
 */
final class PanoramaCapture {
	static final int FACE = 1024;
	private static final long MIN_INTERVAL_MS = 120_000;

	private static boolean rendering;

	private final Path image;
	private final Path meta;
	private boolean requested;
	private boolean inFlight;
	private long lastCaptureAt;
	private boolean timingsLogged;

	PanoramaCapture(Path image, Path meta) {
		this.image = image;
		this.meta = meta;
	}

	/** True while the faces are being drawn, so the regular frame capture stays out of the way. */
	static boolean isRendering() {
		return rendering;
	}

	void requestSoon() {
		requested = true;
	}

	/** Client thread, end of tick: outside any frame, like vanilla's F2-style captures. */
	void tick(Minecraft client) {
		if (!requested) return;
		requested = false;
		LocalPlayer player = client.player;
		if (inFlight || player == null || client.level == null) return;
		if (client.getSingleplayerServer() == null) return; // never on servers
		long now = System.currentTimeMillis();
		if (now - lastCaptureAt < MIN_INTERVAL_MS) return;
		lastCaptureAt = now;
		capture(client, player, now);
	}

	private void capture(Minecraft client, LocalPlayer player, long at) {
		Window window = client.getWindow();
		GameRenderer renderer = client.gameRenderer;
		RenderTarget main = renderer.mainRenderTarget();
		int windowWidth = window.getWidth();
		int windowHeight = window.getHeight();
		int targetWidth = main.width;
		int targetHeight = main.height;
		float xRot = player.getXRot();
		float yRot = player.getYRot();
		float xRotO = player.xRotO;
		float yRotO = player.yRotO;
		TextureTarget[] faces = new TextureTarget[6];

		long started = System.nanoTime();
		rendering = true;
		try {
			renderer.setRenderBlockOutline(false);
			renderer.mainCamera().enablePanoramicMode();
			window.setWidth(FACE);
			window.setHeight(FACE);
			main.resize(FACE, FACE);
			CommandEncoder encoder = RenderSystem.getDevice().createCommandEncoder();
			for (int face = 0; face < 6; face++) {
				float yaw = switch (face) {
					case 1 -> (yRot + 90) % 360;
					case 2 -> (yRot + 180) % 360;
					case 3 -> (yRot - 90) % 360;
					default -> yRot;
				};
				float pitch = face == 4 ? -90 : face == 5 ? 90 : 0;
				player.setYRot(yaw);
				player.setXRot(pitch);
				player.yRotO = yaw;
				player.xRotO = pitch;
				renderer.update(DeltaTracker.ONE);
				renderer.extract(DeltaTracker.ONE, true);
				renderer.renderLevel(DeltaTracker.ONE);
				// Copy out before the next face draws over the main target.
				//
				// A straight copy, not blitAndBlendToTexture: that blends using the
				// source's alpha, and the main target only has alpha where something
				// was actually drawn. Sky and terrain are fine; the stretch below the
				// horizon that is only the frame's clear colour has alpha 0, so it
				// blended to nothing and came out as the black this used to clear to.
				// On screen you never see it, because nothing blends the frame there.
				// The main target is FACE×FACE for the duration, so no scaling is
				// needed and the copy is exact.
				faces[face] = new TextureTarget("mc-status panorama " + face, FACE, FACE, false, main.getColorTexture().getFormat());
				encoder.copyTextureToTexture(main.getColorTexture(), faces[face].getColorTexture(), 0, 0, 0, 0, 0, FACE, FACE);
			}
		} catch (RuntimeException err) {
			McStatusClient.LOG.warn("panorama failed: {}", err.toString());
			for (TextureTarget face : faces) if (face != null) face.destroyBuffers();
			return;
		} finally {
			player.setXRot(xRot);
			player.setYRot(yRot);
			player.xRotO = xRotO;
			player.yRotO = yRotO;
			renderer.setRenderBlockOutline(true);
			window.setWidth(windowWidth);
			window.setHeight(windowHeight);
			main.resize(targetWidth, targetHeight);
			renderer.mainCamera().disablePanoramicMode();
			rendering = false;
		}
		double drawMs = (System.nanoTime() - started) / 1e6;
		readBack(client, faces, at, drawMs);
	}

	private void readBack(Minecraft client, TextureTarget[] faces, long at, double drawMs) {
		NativeImage[] images = new NativeImage[6];
		int[] pending = {6};
		boolean[] failed = {false};
		inFlight = true;
		for (int face = 0; face < 6; face++) {
			int index = face;
			boolean queued = GpuReadback.read(faces[face].getColorTexture(), FACE, FACE, (image, timings) -> {
				// io pool; faces arrive in any order
				synchronized (images) {
					images[index] = image;
					if (image == null) failed[0] = true;
					if (--pending[0] > 0) return;
				}
				client.execute(() -> {
					for (TextureTarget target : faces) target.destroyBuffers();
					inFlight = false;
				});
				if (failed[0]) {
					for (NativeImage part : images) if (part != null) part.close();
					McStatusClient.LOG.warn("panorama readback failed");
					return;
				}
				writeStrip(images, at, drawMs);
			});
			if (!queued) {
				for (TextureTarget target : faces) target.destroyBuffers();
				inFlight = false;
				return;
			}
		}
	}

	/** io pool. */
	private void writeStrip(NativeImage[] faces, long at, double drawMs) {
		long started = System.nanoTime();
		try (NativeImage strip = new NativeImage(FACE * 6, FACE, false)) {
			long rowBytes = FACE * 4L;
			for (int face = 0; face < 6; face++) {
				try (NativeImage part = faces[face]) {
					for (int y = 0; y < FACE; y++) {
						MemoryUtil.memCopy(part.getPointer() + y * rowBytes, strip.getPointer() + y * rowBytes * 6 + face * rowBytes, rowBytes);
					}
				}
			}
			AtomicFiles.write(image, strip::writeToFile);
			AtomicFiles.write(meta, temp -> Files.writeString(temp, "{\"at\":" + at + "}", StandardCharsets.UTF_8));
			if (!timingsLogged) {
				timingsLogged = true;
				McStatusClient.LOG.info(String.format("panorama %d×%d faces: %.1f ms drawing while paused, %.0f ms saving off the render thread",
					FACE, FACE, drawMs, (System.nanoTime() - started) / 1e6));
			}
		} catch (IOException err) {
			McStatusClient.LOG.warn("could not write {}: {}", image, err.getMessage());
		}
	}
}
