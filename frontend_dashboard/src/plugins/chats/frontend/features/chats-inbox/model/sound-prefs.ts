/**
 * Preferencias de sonido de la bandeja — por navegador (localStorage), no por
 * cuenta: cada puesto del operador decide si suena y con qué.
 *
 * UI state del operador, no server state: vive fuera de TanStack Query. Store
 * mínimo con `useSyncExternalStore` para que el panel de ajustes y el hook que
 * reproduce lean la misma foto sin prop-drilling (y se sincronicen entre
 * pestañas vía el evento `storage`).
 */

import { useSyncExternalStore } from "react";
import { z } from "zod";

import { SOUND_PRESETS, type SoundPresetId } from "@/shared/lib";

export const SOUND_PREFS_STORAGE_KEY = "hubara.chats.soundPrefs.v1";
/** localStorage ronda 5 MB por origen; dos sonidos de hasta ~1 MB (≈1.33 MB
 *  en base64) entran holgados. Un "ding" real pesa decenas de KB. */
export const MAX_CUSTOM_SOUND_BYTES = 1024 * 1024;

const presetIds = Object.keys(SOUND_PRESETS) as [SoundPresetId, ...SoundPresetId[]];

const soundChoiceSchema = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("preset"), id: z.enum(presetIds) }),
  z.object({
    kind: z.literal("custom"),
    name: z.string(),
    dataUrl: z.string().startsWith("data:"),
  }),
]);

const soundPrefsSchema = z.object({
  enabled: z.boolean(),
  volume: z.number().min(0).max(1),
  message: soundChoiceSchema,
  human: soundChoiceSchema,
});

export type SoundChoice = z.infer<typeof soundChoiceSchema>;
export type SoundPrefs = z.infer<typeof soundPrefsSchema>;
export type SoundEvent = "message" | "human";

export const DEFAULT_SOUND_PREFS: SoundPrefs = {
  enabled: true,
  volume: 0.7,
  message: { kind: "preset", id: "chime" },
  human: { kind: "preset", id: "alert" },
};

export function readSoundPrefs(): SoundPrefs {
  try {
    const raw = localStorage.getItem(SOUND_PREFS_STORAGE_KEY);
    if (!raw) return DEFAULT_SOUND_PREFS;
    const parsed = soundPrefsSchema.safeParse(JSON.parse(raw));
    return parsed.success ? parsed.data : DEFAULT_SOUND_PREFS;
  } catch {
    return DEFAULT_SOUND_PREFS;
  }
}

const listeners = new Set<() => void>();
let cached: SoundPrefs | null = null;

/** Persiste y avisa a los suscriptores. Lanza si el storage está lleno — el
 *  panel lo muestra (un sonido custom demasiado grande). */
export function writeSoundPrefs(prefs: SoundPrefs): void {
  localStorage.setItem(SOUND_PREFS_STORAGE_KEY, JSON.stringify(prefs));
  cached = prefs;
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  const onStorage = (e: StorageEvent) => {
    if (e.key !== SOUND_PREFS_STORAGE_KEY) return;
    cached = null;
    listener();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

function getSnapshot(): SoundPrefs {
  if (cached === null) cached = readSoundPrefs();
  return cached;
}

export function useSoundPrefs(): SoundPrefs {
  return useSyncExternalStore(subscribe, getSnapshot, () => DEFAULT_SOUND_PREFS);
}

/** Archivo elegido por el operador → sonido personalizado (data URL). */
export function customSoundFromFile(file: File): Promise<SoundChoice> {
  if (!file.type.startsWith("audio/")) {
    return Promise.reject(new Error("El archivo debe ser de audio (mp3, wav, ogg…)."));
  }
  if (file.size > MAX_CUSTOM_SOUND_BYTES) {
    return Promise.reject(new Error("El sonido es muy pesado (máximo 1 MB)."));
  }
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () =>
      resolve({ kind: "custom", name: file.name, dataUrl: String(reader.result) });
    reader.onerror = () => reject(new Error("No se pudo leer el archivo."));
    reader.readAsDataURL(file);
  });
}
