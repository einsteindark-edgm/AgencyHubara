/**
 * Sonidos de notificación, agnósticos de dominio.
 *
 * Dos fuentes:
 *   - `preset`: tonos sintetizados con Web Audio (sin assets en el bundle).
 *   - `custom`: un archivo de audio propio (data URL / URL) vía `HTMLAudioElement`.
 *
 * Autoplay: los navegadores bloquean audio hasta el primer gesto del usuario
 * en la página (click / tecla). `installAudioUnlock` escucha ese primer gesto y
 * reanuda el `AudioContext`; hasta entonces `playSound` devuelve false. Igual
 * que `notify.ts`, es best-effort: NUNCA lanza.
 */

export type SoundPresetId = "chime" | "pop" | "bell" | "alert";

export interface SoundPreset {
  label: string;
  /** Notas en orden: frecuencia (Hz), inicio y duración (s) relativos al disparo. */
  notes: { freq: number; at: number; dur: number }[];
  wave: OscillatorType;
}

export const SOUND_PRESETS: Record<SoundPresetId, SoundPreset> = {
  chime: {
    label: "Campanita",
    wave: "sine",
    notes: [
      { freq: 880, at: 0, dur: 0.18 },
      { freq: 1318.5, at: 0.12, dur: 0.3 },
    ],
  },
  pop: {
    label: "Pop",
    wave: "triangle",
    notes: [{ freq: 660, at: 0, dur: 0.12 }],
  },
  bell: {
    label: "Timbre",
    wave: "sine",
    notes: [
      { freq: 1046.5, at: 0, dur: 0.25 },
      { freq: 784, at: 0.2, dur: 0.25 },
      { freq: 1046.5, at: 0.4, dur: 0.45 },
    ],
  },
  alert: {
    label: "Alerta",
    wave: "square",
    notes: [
      { freq: 740, at: 0, dur: 0.14 },
      { freq: 988, at: 0.18, dur: 0.14 },
      { freq: 740, at: 0.36, dur: 0.14 },
      { freq: 988, at: 0.54, dur: 0.22 },
    ],
  },
};

export type SoundSource =
  | { kind: "preset"; id: SoundPresetId }
  | { kind: "custom"; dataUrl: string };

let ctx: AudioContext | null = null;

function getAudioContext(): AudioContext | null {
  if (ctx) return ctx;
  const Ctor =
    (globalThis as { AudioContext?: typeof AudioContext }).AudioContext ??
    (globalThis as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (typeof Ctor !== "function") return null;
  try {
    ctx = new Ctor();
  } catch {
    return null;
  }
  return ctx;
}

/** True cuando el navegador ya permite reproducir (hubo un gesto en la página). */
export function isAudioUnlocked(): boolean {
  return ctx?.state === "running";
}

/**
 * Escucha el primer gesto del usuario para desbloquear el audio. Idempotente;
 * devuelve el cleanup. `onUnlock` avisa a la UI para ocultar el aviso.
 */
export function installAudioUnlock(onUnlock?: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  const events = ["pointerdown", "keydown", "touchstart"] as const;
  const unlock = () => {
    const c = getAudioContext();
    if (!c) return;
    void c.resume().then(() => {
      if (c.state !== "running") return;
      events.forEach((e) => window.removeEventListener(e, unlock));
      onUnlock?.();
    }).catch(() => {});
  };
  events.forEach((e) => window.addEventListener(e, unlock));
  return () => events.forEach((e) => window.removeEventListener(e, unlock));
}

function playPreset(preset: SoundPreset, volume: number): boolean {
  const c = getAudioContext();
  if (!c || c.state !== "running") return false;
  const t0 = c.currentTime + 0.01;
  // Los square suenan mucho más fuerte que un sine al mismo gain.
  const peak = Math.max(0, Math.min(1, volume)) * (preset.wave === "square" ? 0.12 : 0.35);
  for (const n of preset.notes) {
    const osc = c.createOscillator();
    const gain = c.createGain();
    osc.type = preset.wave;
    osc.frequency.value = n.freq;
    const start = t0 + n.at;
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.exponentialRampToValueAtTime(Math.max(peak, 0.0002), start + 0.015);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + n.dur);
    osc.connect(gain).connect(c.destination);
    osc.start(start);
    osc.stop(start + n.dur + 0.02);
  }
  return true;
}

/** Reproduce un sonido. Devuelve false si no se pudo (bloqueado / sin soporte). */
export async function playSound(source: SoundSource, volume: number): Promise<boolean> {
  try {
    if (source.kind === "custom") {
      if (typeof Audio === "undefined") return false;
      const audio = new Audio(source.dataUrl);
      audio.volume = Math.max(0, Math.min(1, volume));
      await audio.play();
      return true;
    }
    return playPreset(SOUND_PRESETS[source.id], volume);
  } catch {
    return false;
  }
}
