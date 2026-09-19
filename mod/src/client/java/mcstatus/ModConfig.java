package mcstatus;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Properties;

final class ModConfig {
	int captureIntervalSeconds = 60;
	int captureWidth = 1920;
	int stateIntervalTicks = 20;
	boolean shareItemNames = false;
	boolean shareMods = true;
	boolean shareServerWorld = false;
	boolean shareScreenWithServer = false;

	static ModConfig load(Path path) {
		Properties props = new Properties();
		ModConfig config = new ModConfig();
		props.setProperty("capture_interval_seconds", String.valueOf(config.captureIntervalSeconds));
		props.setProperty("capture_width", String.valueOf(config.captureWidth));
		props.setProperty("state_interval_ticks", String.valueOf(config.stateIntervalTicks));
		props.setProperty("share_item_names", String.valueOf(config.shareItemNames));
		props.setProperty("share_mods", String.valueOf(config.shareMods));
		props.setProperty("share_server_world", String.valueOf(config.shareServerWorld));
		props.setProperty("share_screen_with_server", String.valueOf(config.shareScreenWithServer));

		if (Files.isRegularFile(path)) {
			try (InputStream in = Files.newInputStream(path)) {
				props.load(in);
			} catch (IOException err) {
				McStatusClient.LOG.warn("could not read {}, using defaults: {}", path, err.getMessage());
			}
		} else {
			try (OutputStream out = Files.newOutputStream(path)) {
				props.store(out, "mc-status: share_item_names publishes custom item names, which can contain anything; "
					+ "share_server_world allows frames and coordinates from servers, where other players are in shot; "
					+ "share_screen_with_server lets a server that asks show your view of the world on its admin page");
			} catch (IOException err) {
				McStatusClient.LOG.warn("could not write default config {}: {}", path, err.getMessage());
			}
		}

		config.captureIntervalSeconds = Math.max(10, integer(props, "capture_interval_seconds", 60));
		config.captureWidth = Math.clamp(integer(props, "capture_width", 1920), 160, 1920);
		config.stateIntervalTicks = Math.clamp(integer(props, "state_interval_ticks", 20), 5, 200);
		config.shareItemNames = Boolean.parseBoolean(props.getProperty("share_item_names", "false").trim());
		config.shareMods = Boolean.parseBoolean(props.getProperty("share_mods", "true").trim());
		config.shareServerWorld = Boolean.parseBoolean(props.getProperty("share_server_world", "false").trim());
		config.shareScreenWithServer = Boolean.parseBoolean(props.getProperty("share_screen_with_server", "false").trim());
		return config;
	}

	private static int integer(Properties props, String key, int fallback) {
		try {
			return Integer.parseInt(props.getProperty(key, "").trim());
		} catch (NumberFormatException err) {
			return fallback;
		}
	}
}
