/**
 * Compresión de imágenes CLIENT-SIDE antes de subirlas al chat.
 *
 * Una foto de cámara Android pesa 3–12 MB; subirla cruda en red celular es
 * lento y se cuelga. Reencodeamos a JPEG con lado máximo acotado → 150–500 KB
 * (10–20× menos bytes) y, de yapa, el reencode por canvas **stripea EXIF/GPS**
 * (privacidad gratis). WhatsApp igual recomprime del lado de Meta.
 *
 * JPEG y no WebP a propósito: WhatsApp trata `image/webp` como sticker.
 *
 * `computeTargetDimensions` es la lógica pura (testeable en jsdom). El
 * orquestador `compressImage` usa `createImageBitmap` + `<canvas>`; si el
 * entorno no los soporta (jsdom, webview viejo) cae al archivo original —
 * el backend igual valida tamaño/tipo, así que un fallback nunca rompe el
 * envío, solo se pierde la optimización.
 */

export interface CompressOptions {
  /** Lado más largo permitido en px. Default 1600 (buena calidad, poco peso). */
  maxSide?: number;
  /** Calidad JPEG 0..1. Default 0.8. */
  quality?: number;
}

export interface CompressedImage {
  blob: Blob;
  mime: string;
  /** URL de objeto para preview optimista. El caller DEBE revocarla. */
  previewUrl: string;
}

const DEFAULT_MAX_SIDE = 1600;
const DEFAULT_QUALITY = 0.8;
const OUTPUT_MIME = "image/jpeg";

/**
 * Dimensiones destino preservando aspect ratio, con el lado más largo acotado
 * a `maxSide`. Nunca agranda (si la imagen ya es chica, la deja igual).
 */
export function computeTargetDimensions(
  width: number,
  height: number,
  maxSide: number = DEFAULT_MAX_SIDE,
): { width: number; height: number } {
  if (width <= 0 || height <= 0) return { width: 0, height: 0 };
  const longest = Math.max(width, height);
  if (longest <= maxSide) return { width, height };
  const scale = maxSide / longest;
  return {
    width: Math.round(width * scale),
    height: Math.round(height * scale),
  };
}

/** Object URL para preview; safe en entornos sin `URL.createObjectURL` (jsdom). */
function safeObjectUrl(blob: Blob): string {
  try {
    return URL.createObjectURL(blob);
  } catch {
    return "";
  }
}

/** True si el entorno puede reencodear por canvas (browser real, no jsdom). */
function canEncodeViaCanvas(): boolean {
  return (
    typeof createImageBitmap === "function" &&
    typeof document !== "undefined" &&
    typeof document.createElement === "function"
  );
}

/**
 * Comprime `file` a JPEG. Si el entorno no soporta canvas, devuelve el archivo
 * original envuelto (con su preview) — el envío sigue funcionando.
 */
export async function compressImage(
  file: File | Blob,
  opts: CompressOptions = {},
): Promise<CompressedImage> {
  const maxSide = opts.maxSide ?? DEFAULT_MAX_SIDE;
  const quality = opts.quality ?? DEFAULT_QUALITY;

  if (!canEncodeViaCanvas()) {
    return {
      blob: file,
      mime: file.type || OUTPUT_MIME,
      previewUrl: safeObjectUrl(file),
    };
  }

  // `imageOrientation: "from-image"` respeta el EXIF de orientación (fotos de
  // cámara en portrait) ANTES de descartarlo en el reencode.
  const bitmap = await createImageBitmap(file, {
    imageOrientation: "from-image",
  });
  const { width, height } = computeTargetDimensions(
    bitmap.width,
    bitmap.height,
    maxSide,
  );

  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    bitmap.close?.();
    return {
      blob: file,
      mime: file.type || OUTPUT_MIME,
      previewUrl: safeObjectUrl(file),
    };
  }
  ctx.drawImage(bitmap, 0, 0, width, height);
  bitmap.close?.();

  const blob = await new Promise<Blob | null>((resolve) =>
    canvas.toBlob(resolve, OUTPUT_MIME, quality),
  );
  const finalBlob = blob ?? file;
  return {
    blob: finalBlob,
    mime: OUTPUT_MIME,
    previewUrl: safeObjectUrl(finalBlob),
  };
}
