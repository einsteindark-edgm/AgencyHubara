/**
 * Tests de la lógica pura de compresión. El reencode por canvas
 * (`compressImage`) depende de `createImageBitmap`/`<canvas>` que jsdom no
 * implementa, así que acá cubrimos `computeTargetDimensions` (la matemática
 * de reescalado) y el fallback de `compressImage` cuando no hay canvas.
 */

import { describe, it, expect } from "vitest";
import { computeTargetDimensions, compressImage } from "./image-compress";

describe("computeTargetDimensions", () => {
  it("no agranda imágenes ya pequeñas", () => {
    expect(computeTargetDimensions(800, 600, 1600)).toEqual({
      width: 800,
      height: 600,
    });
  });

  it("escala al lado más largo preservando aspect ratio (landscape)", () => {
    // 4000×3000 → lado largo 4000 > 1600 → escala 0.4 → 1600×1200.
    expect(computeTargetDimensions(4000, 3000, 1600)).toEqual({
      width: 1600,
      height: 1200,
    });
  });

  it("escala por el alto cuando la imagen es portrait", () => {
    // 3000×4000 → lado largo 4000 → escala 0.4 → 1200×1600.
    expect(computeTargetDimensions(3000, 4000, 1600)).toEqual({
      width: 1200,
      height: 1600,
    });
  });

  it("respeta un maxSide custom", () => {
    expect(computeTargetDimensions(2000, 1000, 1000)).toEqual({
      width: 1000,
      height: 500,
    });
  });

  it("devuelve 0×0 ante dimensiones inválidas (no explota)", () => {
    expect(computeTargetDimensions(0, 0, 1600)).toEqual({ width: 0, height: 0 });
  });
});

describe("compressImage fallback (sin canvas en jsdom)", () => {
  it("devuelve un blob + previewUrl aunque no haya canvas", async () => {
    const file = new File([new Uint8Array([1, 2, 3])], "foto.jpg", {
      type: "image/jpeg",
    });
    const result = await compressImage(file);
    expect(result.blob).toBeInstanceOf(Blob);
    expect(result.mime).toContain("image/");
    expect(typeof result.previewUrl).toBe("string");
  });
});
