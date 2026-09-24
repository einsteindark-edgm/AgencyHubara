import { describe, expect, it } from "vitest";

import { threadSchema } from "@plugins/lab/frontend/entities/lab-run";

import { buildThreadView } from "./thread-view";

/**
 * Keys únicas aunque un turno se reabra: un mensaje del cliente que no está en
 * el banco (entre dos mensajes de la misma ráfaga) cierra el chip del turno, y
 * al volver la ráfaga el chip se agregaba otra vez con la MISMA key (React
 * mezclaba los dos y el botón del hilo abría el turno equivocado).
 */
describe("buildThreadView", () => {
  it("un turno que se reabre no repite la key de su chip", () => {
    const t0 = Date.parse("2026-09-24T15:00:00Z");
    const iso = (ms: number) => new Date(ms).toISOString();
    const thread = threadSchema.parse({
      session_id: "wa_573001234567",
      episode_id: "ep_001",
      messages: [
        { role: "user", content: "hola", timestamp: iso(t0), wamid: "w1" },
        { role: "user", content: "¿sigues ahí?", timestamp: iso(t0 + 30_000), wamid: "w-fuera-del-banco" },
        { role: "user", content: "y el catálogo", timestamp: iso(t0 + 60_000), wamid: "w2" },
      ],
      turns: [
        {
          case_id: "c1", turn_key: "t1", episode_id: "ep_001", turn: 1, at_ms: t0,
          burst: [{ text: "hola", ts_ms: t0, wamid: "w1" }, { text: "y el catálogo", ts_ms: t0 + 60_000, wamid: "w2" }],
        },
      ],
    });

    const keys = buildThreadView(thread, "A0").map((i) => i.key);

    expect(new Set(keys).size).toBe(keys.length);
  });

  it("la medianoche de Bogotá se lee 00:xx, no 24:xx", () => {
    const midnight = Date.parse("2026-09-24T05:05:00Z"); // 00:05 en Bogotá
    const thread = threadSchema.parse({
      session_id: "wa_573001234567",
      messages: [{ role: "assistant", content: "¡Hola!", timestamp: new Date(midnight).toISOString() }],
    });

    const [, msg] = buildThreadView(thread, "A0");

    expect(msg.type === "msg" && msg.time).toBe("00:05");
  });
});
