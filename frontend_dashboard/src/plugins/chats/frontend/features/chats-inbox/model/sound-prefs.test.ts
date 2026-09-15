/**
 * Preferencias de sonido de la bandeja (por navegador, en localStorage):
 * encendido, volumen y qué sonido usa cada evento — un preset sintetizado o un
 * archivo propio del operador.
 */

import { beforeEach, describe, expect, it } from "vitest";
import {
  DEFAULT_SOUND_PREFS,
  MAX_CUSTOM_SOUND_BYTES,
  SOUND_PREFS_STORAGE_KEY,
  customSoundFromFile,
  readSoundPrefs,
  writeSoundPrefs,
} from "./sound-prefs";

beforeEach(() => localStorage.clear());

describe("sound prefs", () => {
  it("sin nada guardado: encendido, y sonidos DISTINTOS para mensaje y humano", () => {
    const prefs = readSoundPrefs();
    expect(prefs).toEqual(DEFAULT_SOUND_PREFS);
    expect(prefs.enabled).toBe(true);
    expect(prefs.message).not.toEqual(prefs.human);
  });

  it("JSON corrupto o de otra forma → defaults (nunca rompe la bandeja)", () => {
    localStorage.setItem(SOUND_PREFS_STORAGE_KEY, "{no json");
    expect(readSoundPrefs()).toEqual(DEFAULT_SOUND_PREFS);
    localStorage.setItem(SOUND_PREFS_STORAGE_KEY, JSON.stringify({ volume: "alto" }));
    expect(readSoundPrefs()).toEqual(DEFAULT_SOUND_PREFS);
  });

  it("round-trip con un sonido personalizado", () => {
    const prefs = {
      enabled: true,
      volume: 0.4,
      message: { kind: "preset", id: "pop" },
      human: { kind: "custom", name: "campana.mp3", dataUrl: "data:audio/mpeg;base64,AAAA" },
    } as const;
    writeSoundPrefs(prefs);
    expect(readSoundPrefs()).toEqual(prefs);
  });

  it("customSoundFromFile convierte el archivo en data URL con su nombre", async () => {
    const file = new File([new Uint8Array([1, 2, 3])], "ding.wav", { type: "audio/wav" });
    const choice = await customSoundFromFile(file);
    expect(choice).toEqual({
      kind: "custom",
      name: "ding.wav",
      dataUrl: expect.stringMatching(/^data:audio\/wav;base64,/),
    });
  });

  it("customSoundFromFile rechaza lo que no es audio y lo demasiado pesado", async () => {
    const pdf = new File(["x"], "a.pdf", { type: "application/pdf" });
    await expect(customSoundFromFile(pdf)).rejects.toThrow(/audio/i);
    const big = new File([new Uint8Array(MAX_CUSTOM_SOUND_BYTES + 1)], "b.mp3", {
      type: "audio/mpeg",
    });
    await expect(customSoundFromFile(big)).rejects.toThrow(/pesado/i);
  });
});
