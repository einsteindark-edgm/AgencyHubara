/**
 * Detector puro del sonido de la bandeja: dada la foto anterior (último
 * inbound + ¿en humano? por chat) y la lista actual, ¿qué suena?
 *
 *   - "message": escribió un cliente en un chat que atiende el bot.
 *   - "human":   escribió un cliente en un chat asignado al humano, o un chat
 *                ACABA de pasar a humano. Gana sobre "message" (es lo urgente).
 *   - null:      nada nuevo del cliente (respuestas del bot/operador no suenan).
 */

import { describe, expect, it } from "vitest";
import { decideChatSound, type SoundSnapshot } from "./sound-notify";

type Item = { id: string; human?: boolean; lastInboundMs: number | null };

const snap = (...entries: [string, number | null, boolean][]): SoundSnapshot =>
  new Map(entries.map(([id, lastInboundMs, human]) => [id, { lastInboundMs, human }]));

describe("decideChatSound", () => {
  it("primera carga: no suena (evita el ding al abrir el dashboard)", () => {
    const { sound, nextSnapshot } = decideChatSound(new Map(), [
      { id: "wa_1", lastInboundMs: 1_000, human: true },
    ] satisfies Item[]);
    expect(sound).toBeNull();
    expect(nextSnapshot.get("wa_1")).toEqual({ lastInboundMs: 1_000, human: true });
  });

  it("mensaje nuevo del cliente en chat del bot → 'message'", () => {
    const { sound } = decideChatSound(snap(["wa_1", 1_000, false]), [
      { id: "wa_1", lastInboundMs: 2_000, human: false },
    ]);
    expect(sound).toBe("message");
  });

  it("mensaje nuevo del cliente en chat asignado al humano → 'human'", () => {
    const { sound } = decideChatSound(snap(["wa_1", 1_000, true]), [
      { id: "wa_1", lastInboundMs: 2_000, human: true },
    ]);
    expect(sound).toBe("human");
  });

  it("sin inbound nuevo (respondió el bot o el operador) → no suena", () => {
    const { sound } = decideChatSound(snap(["wa_1", 1_000, false]), [
      { id: "wa_1", lastInboundMs: 1_000, human: false },
    ]);
    expect(sound).toBeNull();
  });

  it("un chat que ACABA de pasar a humano suena 'human' aunque no haya inbound nuevo", () => {
    const { sound } = decideChatSound(snap(["wa_1", 1_000, false]), [
      { id: "wa_1", lastInboundMs: 1_000, human: true },
    ]);
    expect(sound).toBe("human");
  });

  it("conversación nueva que aparece con un inbound → 'message'", () => {
    const { sound } = decideChatSound(snap(["wa_1", 1_000, false]), [
      { id: "wa_1", lastInboundMs: 1_000, human: false },
      { id: "wa_2", lastInboundMs: 5_000, human: false },
    ]);
    expect(sound).toBe("message");
  });

  it("varios a la vez: 'human' gana sobre 'message' (un solo sonido por tick)", () => {
    const { sound } = decideChatSound(
      snap(["wa_1", 1_000, false], ["wa_2", 1_000, true]),
      [
        { id: "wa_1", lastInboundMs: 2_000, human: false },
        { id: "wa_2", lastInboundMs: 2_000, human: true },
      ],
    );
    expect(sound).toBe("human");
  });

  it("un inbound MÁS VIEJO (historial reescrito) no suena", () => {
    const { sound } = decideChatSound(snap(["wa_1", 5_000, false]), [
      { id: "wa_1", lastInboundMs: 1_000, human: false },
    ]);
    expect(sound).toBeNull();
  });

  it("lista vacía transitoria del refetch: conserva la foto previa", () => {
    const prev = snap(["wa_1", 1_000, false]);
    const { sound, nextSnapshot } = decideChatSound(prev, []);
    expect(sound).toBeNull();
    expect(nextSnapshot).toBe(prev);
  });
});
