/**
 * Reproductor de sonidos de notificación. Best-effort como `notify.ts`: sin
 * soporte de audio (jsdom, webview raro) o bloqueado por autoplay, devuelve
 * false y NUNCA lanza.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { playSound, SOUND_PRESETS } from "./sound";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("playSound", () => {
  it("un archivo personalizado se reproduce con el volumen pedido", async () => {
    const play = vi.fn().mockResolvedValue(undefined);
    const created: { src: string; volume: number }[] = [];
    vi.stubGlobal(
      "Audio",
      vi.fn(function (this: { src: string; volume: number; play: typeof play }, src: string) {
        this.src = src;
        this.volume = 1;
        this.play = play;
        created.push(this);
      }),
    );

    const ok = await playSound({ kind: "custom", dataUrl: "data:audio/wav;base64,AA" }, 0.3);

    expect(ok).toBe(true);
    expect(play).toHaveBeenCalledOnce();
    expect(created[0]).toMatchObject({ src: "data:audio/wav;base64,AA", volume: 0.3 });
  });

  it("autoplay bloqueado (play() rechaza) → false, sin lanzar", async () => {
    vi.stubGlobal(
      "Audio",
      vi.fn(function (this: { play: () => Promise<void> }) {
        this.play = () => Promise.reject(new DOMException("blocked", "NotAllowedError"));
      }),
    );
    await expect(playSound({ kind: "custom", dataUrl: "data:," }, 1)).resolves.toBe(false);
  });

  it("preset sin Web Audio disponible → false, sin lanzar", async () => {
    vi.stubGlobal("AudioContext", undefined);
    await expect(playSound({ kind: "preset", id: "chime" }, 1)).resolves.toBe(false);
  });

  it("los presets tienen notas (ningún preset mudo)", () => {
    for (const preset of Object.values(SOUND_PRESETS)) {
      expect(preset.notes.length).toBeGreaterThan(0);
      expect(preset.label).not.toBe("");
    }
  });
});
