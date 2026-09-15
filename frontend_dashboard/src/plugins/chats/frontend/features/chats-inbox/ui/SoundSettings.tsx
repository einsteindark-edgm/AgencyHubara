/**
 * Ajustes de sonido de la bandeja (plegable, como el calendario): encendido,
 * volumen y qué suena para cada evento — un preset o un archivo propio.
 *
 * Todo es por navegador (localStorage, ver `sound-prefs`). Si el navegador aún
 * bloquea el audio (no hubo click en la página desde que cargó), se avisa: si
 * no, el operador cree que tiene sonido y se pierde mensajes.
 */

import { useEffect, useState, type ChangeEvent } from "react";

import {
  installAudioUnlock,
  isAudioUnlocked,
  playSound,
  SOUND_PRESETS,
  type SoundPresetId,
} from "@/shared/lib";
import { Icon } from "@/shared/ui";

import { toSoundSource } from "../model/sound-notify";
import {
  customSoundFromFile,
  useSoundPrefs,
  writeSoundPrefs,
  type SoundEvent,
  type SoundPrefs,
} from "../model/sound-prefs";

const EVENTS: { key: SoundEvent; label: string }[] = [
  { key: "message", label: "Mensaje nuevo" },
  { key: "human", label: "Asignada al humano" },
];

const CUSTOM = "__custom__";

export function SoundSettings() {
  const prefs = useSoundPrefs();
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [unlocked, setUnlocked] = useState(isAudioUnlocked);

  useEffect(() => installAudioUnlock(() => setUnlocked(true)), []);

  const save = (next: SoundPrefs) => {
    try {
      writeSoundPrefs(next);
      setError(null);
    } catch {
      setError("No hay espacio para guardar ese sonido en este navegador.");
    }
  };

  const onPick = (event: SoundEvent) => (e: ChangeEvent<HTMLSelectElement>) => {
    if (e.target.value === CUSTOM) return; // se elige subiendo el archivo
    save({ ...prefs, [event]: { kind: "preset", id: e.target.value as SoundPresetId } });
  };

  const onFile = (event: SoundEvent) => async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    try {
      save({ ...prefs, [event]: await customSoundFromFile(file) });
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo usar ese archivo.");
    }
  };

  return (
    <div className="cal-filter">
      <button
        className={"cal-trigger" + (prefs.enabled ? "" : " off")}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label="Sonidos de la bandeja"
      >
        <Icon.bell />
        <span className="cal-trigger-label">
          {!prefs.enabled
            ? "Sonido apagado"
            : unlocked
              ? "Sonido activado"
              : "Sonido: hacé clic en la página para activarlo"}
        </span>
        <span className={"cal-caret" + (open ? " open" : "")}>
          <Icon.caret />
        </span>
      </button>

      {open && (
        <div className="cal-panel" style={{ fontSize: 11.5, gap: 8 }}>
          <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <input
              type="checkbox"
              checked={prefs.enabled}
              onChange={(e) => save({ ...prefs, enabled: e.target.checked })}
            />
            Sonido activado
          </label>

          <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
            Volumen
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={prefs.volume}
              onChange={(e) => save({ ...prefs, volume: Number(e.target.value) })}
              style={{ flex: 1 }}
            />
          </label>

          {EVENTS.map(({ key, label }) => {
            const choice = prefs[key];
            const selectId = `sound-${key}`;
            return (
              <div key={key} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <label htmlFor={selectId} style={{ color: "var(--fg-soft)" }}>
                  {label}
                </label>
                <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
                  <select
                    id={selectId}
                    value={choice.kind === "preset" ? choice.id : CUSTOM}
                    onChange={onPick(key)}
                    style={{ flex: 1, minWidth: 0 }}
                  >
                    {(Object.keys(SOUND_PRESETS) as SoundPresetId[]).map((id) => (
                      <option key={id} value={id}>
                        {SOUND_PRESETS[id].label}
                      </option>
                    ))}
                    {choice.kind === "custom" && (
                      <option value={CUSTOM}>Propio: {choice.name}</option>
                    )}
                  </select>
                  <button
                    className="cal-nav"
                    aria-label={`Probar ${label.toLowerCase()}`}
                    title="Probar"
                    onClick={() => void playSound(toSoundSource(choice), prefs.volume)}
                  >
                    ▶
                  </button>
                </div>
                <label style={{ color: "var(--fg-muted)", cursor: "pointer" }}>
                  <input
                    type="file"
                    accept="audio/*"
                    aria-label={`Archivo para ${label.toLowerCase()}`}
                    onChange={onFile(key)}
                    style={{ display: "none" }}
                  />
                  Subir sonido propio…
                </label>
              </div>
            );
          })}

          {error && (
            <span role="alert" style={{ color: "var(--danger, #ff6b6b)" }}>
              {error}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
