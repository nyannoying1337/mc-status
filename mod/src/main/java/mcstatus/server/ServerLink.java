package mcstatus.server;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.WebSocket;
import java.time.Duration;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

/**
 * The server's own connection to the Worker (/server/connect). The server
 * connects out, so nothing has to reach in: status goes up it, and messages for
 * the server — actions from the admin page, and whose page someone has open —
 * come back down. Reconnects by itself, backing off to a minute.
 */
final class ServerLink implements WebSocket.Listener {
	private final HttpClient http;
	private final URI uri;
	private final String token;
	private final Consumer<JsonObject> onMessage;
	private final ScheduledExecutorService retry = Executors.newSingleThreadScheduledExecutor(runnable -> {
		Thread thread = new Thread(runnable, "mc-status link");
		thread.setDaemon(true);
		return thread;
	});
	private final StringBuilder partial = new StringBuilder();
	private volatile WebSocket socket;
	private volatile boolean stopped;
	private int failures;

	ServerLink(HttpClient http, String workerUrl, String token, Consumer<JsonObject> onMessage) {
		this.http = http;
		this.uri = URI.create(workerUrl.replaceFirst("^http", "ws") + "/server/connect");
		this.token = token;
		this.onMessage = onMessage;
	}

	void start() {
		connect();
	}

	void stop() {
		stopped = true;
		retry.shutdownNow();
		WebSocket current = socket;
		if (current != null) current.sendClose(WebSocket.NORMAL_CLOSURE, "server stopping");
	}

	boolean connected() {
		WebSocket current = socket;
		return current != null && !current.isOutputClosed();
	}

	/** Sends if connected; false means the caller should use plain HTTP instead. */
	boolean send(String text) {
		WebSocket current = socket;
		if (current == null || current.isOutputClosed()) return false;
		current.sendText(text, true).exceptionally(err -> {
			McStatusServer.LOG.debug("link send failed: {}", err.toString());
			return null;
		});
		return true;
	}

	private void connect() {
		if (stopped) return;
		http.newWebSocketBuilder()
			.header("Authorization", "Bearer " + token)
			.connectTimeout(Duration.ofSeconds(15))
			.buildAsync(uri, this)
			.whenComplete((ws, err) -> {
				if (err != null) scheduleReconnect("could not connect: " + rootMessage(err));
			});
	}

	private void scheduleReconnect(String why) {
		socket = null;
		if (stopped) return;
		failures++;
		long delay = Math.min(60, 2L << Math.min(failures, 5));
		if (failures == 1 || failures % 10 == 0) McStatusServer.LOG.warn("admin link {}; retrying in {}s", why, delay);
		try {
			retry.schedule(this::connect, delay, TimeUnit.SECONDS);
		} catch (RuntimeException ignored) {
			// shutting down
		}
	}

	@Override
	public void onOpen(WebSocket webSocket) {
		socket = webSocket;
		if (failures > 0) McStatusServer.LOG.info("admin link connected");
		failures = 0;
		webSocket.request(1);
	}

	@Override
	public CompletionStage<?> onText(WebSocket webSocket, CharSequence data, boolean last) {
		partial.append(data);
		if (last) {
			String text = partial.toString();
			partial.setLength(0);
			try {
				JsonObject message = JsonParser.parseString(text).getAsJsonObject();
				if (message.has("type")) onMessage.accept(message);
			} catch (RuntimeException err) {
				McStatusServer.LOG.debug("ignored link message: {}", err.toString());
			}
		}
		webSocket.request(1);
		return null;
	}

	@Override
	public CompletionStage<?> onClose(WebSocket webSocket, int statusCode, String reason) {
		if (socket == webSocket) scheduleReconnect("closed (" + statusCode + (reason.isEmpty() ? "" : " " + reason) + ")");
		return CompletableFuture.completedFuture(null);
	}

	@Override
	public void onError(WebSocket webSocket, Throwable error) {
		if (socket == webSocket) scheduleReconnect("failed: " + rootMessage(error));
	}

	private static String rootMessage(Throwable err) {
		Throwable cause = err;
		while (cause.getCause() != null) cause = cause.getCause();
		return cause.getMessage() != null ? cause.getMessage() : cause.getClass().getSimpleName();
	}
}
