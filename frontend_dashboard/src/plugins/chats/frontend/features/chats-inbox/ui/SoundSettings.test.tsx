/**
 * Panel de sonidos de la bandeja: el operador enciende/apaga, elige qué suena
 * para "mensaje nuevo" y para "asignada al humano", sube un sonido propio y lo
 * prueba. Todo queda guardado en este navegador.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const playSound = vi.fn().mockResolvedValue(true);
vi.mock("@/shared/lib", async (orig) => ({
  ...(await orig<typeof import("@/shared/lib")>()),
  playSound: (...a: unknown[]) => playSound(...a),
}));

import { readSoundPrefs } from "../model/sound-prefs";
import { SoundSettings } from "./SoundSettings";

beforeEach(() => {
  localStorage.clear();
  playSound.mockClear();
});

const open = () => fireEvent.click(screen.getByRole("button", { name: /sonidos/i }));

describe("SoundSettings", () => {
  it("apagar el sonido queda guardado", () => {
    render(<SoundSettings />);
    open();
    fireEvent.click(screen.getByRole("checkbox", { name: /sonido activado/i }));
    expect(readSoundPrefs().enabled).toBe(false);
  });

  it("elegir otro sonido para 'asignada al humano' lo guarda y probarlo lo reproduce", () => {
    render(<SoundSettings />);
    open();
    fireEvent.change(screen.getByRole("combobox", { name: /asignada al humano/i }), {
      target: { value: "bell" },
    });
    expect(readSoundPrefs().human).toEqual({ kind: "preset", id: "bell" });

    fireEvent.click(screen.getByRole("button", { name: /probar asignada al humano/i }));
    expect(playSound).toHaveBeenCalledWith({ kind: "preset", id: "bell" }, readSoundPrefs().volume);
  });

  it("subir un archivo de audio lo deja como sonido de 'mensaje nuevo'", async () => {
    render(<SoundSettings />);
    open();
    const file = new File([new Uint8Array([1, 2])], "ding.mp3", { type: "audio/mpeg" });
    fireEvent.change(screen.getByLabelText(/archivo para mensaje nuevo/i), {
      target: { files: [file] },
    });
    await waitFor(() =>
      expect(readSoundPrefs().message).toMatchObject({ kind: "custom", name: "ding.mp3" }),
    );
  });

  it("un archivo que no es audio muestra el error y no cambia nada", async () => {
    render(<SoundSettings />);
    open();
    const file = new File(["x"], "a.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText(/archivo para mensaje nuevo/i), {
      target: { files: [file] },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent(/audio/i);
    expect(readSoundPrefs().message).toEqual({ kind: "preset", id: "chime" });
  });
});
