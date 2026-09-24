import { describe, expect, it } from "vitest";

import threadFixture from "@plugins/lab/frontend/entities/lab-run/fixtures/thread.json";
import { threadSchema, type LabThread } from "@plugins/lab/frontend/entities/lab-run";

import { buildThreadView } from "./thread-view";

/**
 * Hilo de una conversación del banco, como lo muestra la sección
 * Laboratorio (plan §11): burbujas como en Chats, cada ráfaga agrupada (un
 * botón que abre el hilo del turno) y, al final de la respuesta de cada turno,
 * el botón "Ver hilo del turno".
 *
 * - A0 (producción): los mensajes reales, en orden.
 * - A1/B/C (simulados): los mensajes del cliente de cada turno y lo que ESE
 *   bot respondió; si todavía no corrió, lo dice.
 */

const thread: LabThread = threadSchema.parse(threadFixture);

function brief(items: ReturnType<typeof buildThreadView>) {
  return items.map((it) => {
    switch (it.type) {
      case "day":
        return `day ${it.day}`;
      case "msg":
        return `${it.dir} ${it.text || (it.hasImage ? "[foto]" : "")}`.trim();
      case "burst":
        return `burst t${it.turn.turn} x${it.messages.length}`;
      case "chip":
        return `chip t${it.turn.turn} ${it.tone}`;
      case "note":
        return `note ${it.text}`;
    }
  });
}

describe("buildThreadView", () => {
  it("producción: mensajes reales, la ráfaga agrupada y el botón al final de cada turno", () => {
    const items = buildThreadView(thread, "A0");

    expect(brief(items)).toEqual([
      "day 2026-09-23",
      "in Buenas tardes",
      "out ¡Buenas tardes! Te damos la bienvenida a Hubara 🤍",
      "chip t1 neutral",
      "burst t2 x2",
      "comp 📦 Tarifas de envío\nBogotá: $X.XXX · 2 a 3 días hábiles",
      "chip t2 warn",
      "in [foto]",
    ]);
  });

  it("la ráfaga dice cuántos segundos duró y la hora de cada mensaje (Bogotá)", () => {
    const burst = buildThreadView(thread, "A0").find((it) => it.type === "burst");

    expect(burst).toMatchObject({ type: "burst", spanS: 7 });
    expect(burst && burst.type === "burst" ? burst.messages.map((m) => m.time) : []).toEqual(["10:41", "10:41"]);
  });

  it("un bot simulado que todavía no corrió: el cliente y una nota por turno", () => {
    expect(brief(buildThreadView(thread, "B"))).toEqual([
      "day 2026-09-23",
      "in Buenas tardes",
      "note Este bot todavía no respondió este turno.",
      "chip t1 neutral",
      "burst t2 x2",
      "note Este bot todavía no respondió este turno.",
      "chip t2 neutral",
    ]);
  });

  it("un bot simulado que sí respondió: sus textos como burbujas del bot", () => {
    const withB: LabThread = {
      ...thread,
      turns: thread.turns.map((t) => ({
        ...t,
        outputs: { ...t.outputs, B: { sent_texts: [`respuesta B al turno ${t.turn}`], discarded_narration: [], guards: [], suppressed_reason: null, llm_text: null } },
      })),
    };

    expect(brief(buildThreadView(withB, "B"))).toEqual([
      "day 2026-09-23",
      "in Buenas tardes",
      "out respuesta B al turno 1",
      "chip t1 neutral",
      "burst t2 x2",
      "out respuesta B al turno 2",
      "chip t2 neutral",
    ]);
  });

  it("un turno simulado que no envió nada lo explica", () => {
    const silent: LabThread = {
      ...thread,
      turns: [{ ...thread.turns[0], outputs: { B: { sent_texts: [], discarded_narration: [], guards: [], suppressed_reason: "tag_closure", llm_text: null } } }],
    };

    expect(brief(buildThreadView(silent, "B"))).toContain("note El bot no envió nada (tag_closure).");
  });
});
