package mcstatus;

import java.awt.image.BufferedImage;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.util.Iterator;

import javax.imageio.IIOImage;
import javax.imageio.ImageIO;
import javax.imageio.ImageWriteParam;
import javax.imageio.ImageWriter;
import javax.imageio.stream.MemoryCacheImageOutputStream;

import com.mojang.blaze3d.platform.NativeImage;
import org.lwjgl.system.MemoryUtil;

/**
 * A captured frame as JPEG bytes, for sending to a server that asked for it.
 *
 * <p>The frame the agent publishes is written as PNG and re-encoded by the
 * agent; there's no agent on this path, so the encoding happens here. Called on
 * the io pool with an image {@link GpuReadback} has already flipped upright and
 * made opaque, so the bytes are plain RGBA in reading order.
 */
final class Jpeg {
	private Jpeg() {}

	static byte[] encode(NativeImage image, int width, int height, int quality) throws IOException {
		byte[] rgba = new byte[Math.multiplyExact(Math.multiplyExact(width, height), 4)];
		ByteBuffer pixels = MemoryUtil.memByteBuffer(image.getPointer(), rgba.length);
		pixels.get(rgba);

		int[] rgb = new int[width * height];
		for (int i = 0, at = 0; i < rgb.length; i++, at += 4) {
			rgb[i] = (rgba[at] & 0xFF) << 16 | (rgba[at + 1] & 0xFF) << 8 | (rgba[at + 2] & 0xFF);
		}
		BufferedImage frame = new BufferedImage(width, height, BufferedImage.TYPE_INT_RGB);
		frame.setRGB(0, 0, width, height, rgb, 0, width);
		return write(frame, quality);
	}

	private static byte[] write(BufferedImage frame, int quality) throws IOException {
		Iterator<ImageWriter> writers = ImageIO.getImageWritersByFormatName("jpeg");
		if (!writers.hasNext()) throw new IOException("no JPEG encoder in this JVM");
		ImageWriter writer = writers.next();
		ByteArrayOutputStream bytes = new ByteArrayOutputStream(64 * 1024);
		try (MemoryCacheImageOutputStream out = new MemoryCacheImageOutputStream(bytes)) {
			writer.setOutput(out);
			ImageWriteParam params = writer.getDefaultWriteParam();
			if (params.canWriteCompressed()) {
				params.setCompressionMode(ImageWriteParam.MODE_EXPLICIT);
				params.setCompressionQuality(Math.clamp(quality, 10, 95) / 100f);
			}
			writer.write(null, new IIOImage(frame, null, null), params);
		} finally {
			writer.dispose();
		}
		return bytes.toByteArray();
	}
}
