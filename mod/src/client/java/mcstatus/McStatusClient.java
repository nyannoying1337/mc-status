package mcstatus;

import java.nio.file.Path;

import mcstatus.common.ScreenPayloads;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientLifecycleEvents;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayConnectionEvents;
import net.fabricmc.fabric.api.client.rendering.v1.level.LevelRenderEvents;
import net.fabricmc.fabric.api.client.screen.v1.ScreenEvents;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.PauseScreen;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * The game side of mc-status. Everything it shares goes through files in
 * {@code <gameDir>/mc-status/}, which the agent reads and uploads:
 *
 * <ul>
 *   <li>{@code state.json} — health, hunger, XP, full inventory, position</li>
 *   <li>{@code latest.png} — a frame of the world, without the HUD</li>
 *   <li>{@code panorama.png} + {@code .json} — a 360° view, taken when pausing in singleplayer</li>
 *   <li>{@code commands/*.json} — written by the agent (curses), run here in singleplayer</li>
 * </ul>
 *
 * <p>One thing doesn't go through a file: with {@code share_screen_with_server}
 * on, a server you join may ask for your view of the world for its admin page,
 * and {@link ScreenShare} answers over the game connection.
 */
public class McStatusClient implements ClientModInitializer {
	public static final Logger LOG = LoggerFactory.getLogger("mc-status");

	@Override
	public void onInitializeClient() {
		ModConfig config = ModConfig.load(FabricLoader.getInstance().getConfigDir().resolve("mc-status.properties"));
		Path dir = Minecraft.getInstance().gameDirectory.toPath().resolve("mc-status");

		ScreenPayloads.register();

		Progress progress = new Progress();
		StateWriter state = new StateWriter(dir.resolve("state.json"), config, progress);
		CommandQueue commands = new CommandQueue(dir.resolve("commands"));
		FrameCapture capture = new FrameCapture(dir.resolve("latest.png"), config);
		PanoramaCapture panorama = new PanoramaCapture(dir.resolve("panorama.png"), dir.resolve("panorama.json"));
		ScreenShare screens = new ScreenShare(config, capture);
		screens.register();

		ClientTickEvents.END_CLIENT_TICK.register(client -> {
			progress.tick(client);
			state.tick(client);
			commands.tick(client);
			panorama.tick(client);
			screens.tick(client);
		});
		ClientPlayConnectionEvents.DISCONNECT.register((handler, client) -> {
			state.markOffline();
			screens.reset();
		});
		ClientLifecycleEvents.CLIENT_STOPPING.register(client -> state.markOffline());

		LevelRenderEvents.END_MAIN.register(context -> capture.onLevelRendered(Minecraft.getInstance()));
		// Pausing (and Save & Quit, which always goes through the pause menu)
		// grabs the last thing you were looking at, and a 360° view for when you're gone.
		ScreenEvents.AFTER_INIT.register((client, screen, width, height) -> {
			if (screen instanceof PauseScreen) {
				capture.requestSoon();
				panorama.requestSoon();
			}
		});

		LOG.info("writing to {} (capture every {}s at {}px)", dir, config.captureIntervalSeconds, config.captureWidth);
	}
}
