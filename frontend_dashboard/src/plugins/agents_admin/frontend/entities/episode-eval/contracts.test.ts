import { describe, expect, it } from "vitest";

import {
  conversationEvalsSchema,
  evalTranscriptSchema,
} from "./contracts";

/**
 * Regresión del contrato del boundary (L-10): el backend puede crecer
 * (closing_tag nuevos, trend values nuevos, campos extra) sin que el parse
 * mate la sección entera. Lo que valida acá es lo que la UI necesita.
 */
describe("conversationEvalsSchema", () => {
  it("parsea la respuesta completa del backend", () => {
    const payload = {
      threshold: 0.7,
      suite: "online",
      count: 1,
      conversations: [
        {
          session_id: "wa_+573001112233",
          episode_id: "ep_002",
          evals: [
            {
              date: "2026-06-09",
              ts: "2026-06-09T10:00:00+00:00",
              avg: 0.55,
              passed: false,
              is_candidate: true,
              metrics: { greeting_compliance: 0.0, style_compliance: 1.0 },
              failed: [
                { metric: "greeting_compliance", score: 0.0, reason: "no saludó" },
              ],
            },
            {
              date: "2026-06-10",
              ts: "2026-06-10T10:00:00+00:00",
              avg: 0.8,
              passed: true,
              is_candidate: false,
              metrics: { greeting_compliance: 0.8 },
              failed: [],
            },
          ],
          evals_count: 2,
          first_avg: 0.55,
          last_avg: 0.8,
          last_date: "2026-06-10",
          last_ts: "2026-06-10T10:00:00+00:00",
          last_passed: true,
          is_candidate: false,
          trend: "up",
          failed_metrics: [],
          candidate_id: "wa__573001112233__ep_002",
          candidate_status: "pending",
          closing_tag: "COMPRA_EXITOSA",
          order_id: "order_123",
        },
      ],
    };
    const parsed = conversationEvalsSchema.parse(payload);
    expect(parsed.conversations[0].trend).toBe("up");
    expect(parsed.conversations[0].evals).toHaveLength(2);
    expect(parsed.conversations[0].evals[0].failed[0].reason).toBe("no saludó");
  });

  it("tolera valores de dominio nuevos sin matar la respuesta", () => {
    const parsed = conversationEvalsSchema.parse({
      conversations: [
        {
          session_id: "wa_x",
          episode_id: "",
          trend: "algo_nuevo_del_backend", // → catch: degrada a "single"
          closing_tag: "UN_TAG_NUEVO", // string libre, no enum
          last_avg: 0.4,
        },
      ],
    });
    expect(parsed.conversations[0].trend).toBe("single");
    expect(parsed.conversations[0].closing_tag).toBe("UN_TAG_NUEVO");
    expect(parsed.conversations[0].episode_id).toBe(""); // legacy sesión entera
  });
});

describe("evalTranscriptSchema", () => {
  it("parsea el transcript con tools y episodio", () => {
    const parsed = evalTranscriptSchema.parse({
      session_id: "wa_x",
      episode_id: "ep_001",
      turns: [
        { role: "user", content: "hola" },
        { role: "assistant", content: "Buenas", tools: ["send_quick_replies"] },
      ],
      truncated_at_human_takeover: true,
      episode: {
        closing_tag: "RECHAZO",
        closing_motivo: null,
        started_at_ms: 1,
        closed_at_ms: 2,
        order_id: null,
      },
    });
    expect(parsed.turns[0].tools).toEqual([]); // default cuando falta
    expect(parsed.turns[1].tools).toEqual(["send_quick_replies"]);
    expect(parsed.truncated_at_human_takeover).toBe(true);
  });

  it("tolera episode null (sesión legacy)", () => {
    const parsed = evalTranscriptSchema.parse({
      session_id: "wa_x",
      turns: [],
    });
    expect(parsed.episode).toBeNull();
    expect(parsed.episode_id).toBe("");
  });
});
