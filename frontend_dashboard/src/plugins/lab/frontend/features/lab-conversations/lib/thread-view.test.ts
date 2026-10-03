import { describe, expect, it } from "vitest";

import threadFixture from "@plugins/lab/frontend/entities/lab-run/fixtures/thread.json";
import { threadSchema, type LabThread } from "@plugins/lab/frontend/entities/lab-run";

import { buildThreadView, turnReplies } from "./thread-view";

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
        return `chip t${it.turn.turn} ${it.verdict ?? "sin evaluar"}`;
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
      "chip t1 sin evaluar",
      "burst t2 x2",
      "comp 📦 Tarifas de envío\nBogotá: $X.XXX · 2 a 3 días hábiles",
      "chip t2 sin evaluar",
      "in [foto]",
    ]);
  });

  it("el botón de cada turno lleva el resultado de la evaluación de ese turno, no la narración descartada", () => {
    // Caso real (conversación ···6543, producción): el turno 1 solo falló un
    // check menor (texto descartado junto a una tool) → PASA, y se pintaba en
    // ámbar; el turno 2 falló dos checks mayores → ALERTA, y se pintaba gris.
    const verdicts: Record<number, "PASA" | "ALERTA"> = { 1: "PASA", 2: "ALERTA" };
    const verdictOf = (turn: { turn: number }) => verdicts[turn.turn] ?? null;

    const chips = brief(buildThreadView(thread, "A0", verdictOf)).filter((s) => s.startsWith("chip"));

    expect(chips).toEqual(["chip t1 PASA", "chip t2 ALERTA"]);
    expect(brief(buildThreadView(thread, "B", verdictOf)).filter((s) => s.startsWith("chip"))).toEqual(["chip t1 PASA", "chip t2 ALERTA"]);
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
      "chip t1 sin evaluar",
      "burst t2 x2",
      "note Este bot todavía no respondió este turno.",
      "chip t2 sin evaluar",
    ]);
  });

  it("un bot simulado que sí respondió: sus textos como burbujas del bot", () => {
    const withB: LabThread = {
      ...thread,
      turns: thread.turns.map((t) => ({
        ...t,
        outputs: { ...t.outputs, B: { sent_texts: [`respuesta B al turno ${t.turn}`], tools: [], complement_texts: [], discarded_narration: [], guards: [], suppressed_reason: null, llm_text: null, selector_text: null } },
      })),
    };

    expect(brief(buildThreadView(withB, "B"))).toEqual([
      "day 2026-09-23",
      "in Buenas tardes",
      "out respuesta B al turno 1",
      "chip t1 sin evaluar",
      "burst t2 x2",
      "out respuesta B al turno 2",
      "chip t2 sin evaluar",
    ]);
  });

  it("un turno simulado que no envió nada lo explica", () => {
    const silent: LabThread = {
      ...thread,
      turns: [{ ...thread.turns[0], outputs: { B: { sent_texts: [], tools: [], complement_texts: [], discarded_narration: [], guards: [], suppressed_reason: "tag_closure", llm_text: null, selector_text: null } } }],
    };

    expect(brief(buildThreadView(silent, "B"))).toContain("note El bot no envió nada: cerró la conversación con una etiqueta.");
  });

  it("un bot simulado muestra también lo que mandó aparte del texto y lo que le rechazaron", () => {
    // Como llega del API: el contrato completa lo que falta (error, notas).
    const withTools: LabThread = threadSchema.parse({
      ...thread,
      turns: [
        {
          ...thread.turns[0],
          outputs: {
            B: {
              sent_texts: ["Mira esta"],
              discarded_narration: [],
              guards: [],
              suppressed_reason: null,
              llm_text: null,
              tools: [
                { name: "search_products", ok: true, args: { q: "jesús" }, notes: ["count:0"] },
                { name: "present_product_detail", ok: true, args: { handle: "sagrado-rostro" } },
                { name: "send_cta_url", ok: false, error: "url_not_whitelisted", args: { button_text: "Ver catálogo" } },
              ],
            },
          },
        },
      ],
    });

    expect(brief(buildThreadView(withTools, "B"))).toEqual([
      "day 2026-09-23",
      "in Buenas tardes",
      "out Mira esta",
      "comp 🧩 Tarjeta del producto · Sagrado rostro",
      "note No salió: Botón con enlace · Ver catálogo (el enlace no está permitido).",
      "chip t1 sin evaluar",
    ]);
  });
});

describe("turnReplies (lo que respondió cada bot en un turno)", () => {
  const brief = (items: ReturnType<typeof turnReplies>) => items.map((it) => `${it.dir} ${it.text}`);

  it("producción: los mensajes reales que siguieron a la ráfaga del turno", () => {
    expect(brief(turnReplies(thread, thread.turns[0], "A0"))).toEqual(["out ¡Buenas tardes! Te damos la bienvenida a Hubara 🤍"]);
    expect(brief(turnReplies(thread, thread.turns[1], "A0"))).toEqual(["comp 📦 Tarifas de envío\nBogotá: $X.XXX · 2 a 3 días hábiles"]);
  });

  it("un bot simulado: lo que ESE bot respondió, o por qué no hay nada", () => {
    expect(brief(turnReplies(thread, thread.turns[1], "B"))).toEqual(["note Este bot todavía no respondió este turno."]);
  });

  it("un bot simulado: lo que presentó dice cuáles productos y con qué texto", () => {
    const withList: LabThread = threadSchema.parse({
      ...thread,
      turns: [{
        ...thread.turns[0],
        outputs: { B: { sent_texts: ["¡Buenos días! Bienvenido a *Hubara*"], tools: [
          { name: "present_products", ok: true, notes: ["count:4"],
            args: { handles: '["calabaza", "momia", "fantasma", "trilogia-del-terror"]', intro_text: "Esta es la colección de Halloween 🎃" } },
        ] } },
      }],
    });

    expect(brief(turnReplies(withList, withList.turns[0], "B"))).toEqual([
      "out ¡Buenos días! Bienvenido a *Hubara*",
      "comp 🧩 Lista de productos · Calabaza, Momia, Fantasma +1\n«Esta es la colección de Halloween 🎃»",
    ]);
  });

  it("el selector que armó la protección con la lista del bot se ve, con lo que dijo antes y después", () => {
    // Laboratorio caso-fotos-0930-r7, 4567 t19: salía «El bot no envió nada»
    // aunque el cliente recibió «Jengibre no está…» con el selector de aromas.
    const picker = "Jengibre no está entre los aromas de la Luz Serena. Los que maneja son:\n\n🌿 Lavanda\n\n¿Alguno de esos te llama la atención?";
    const withPicker: LabThread = threadSchema.parse({
      ...thread,
      turns: [{
        ...thread.turns[0],
        outputs: { B: { sent_texts: [], tools: [], suppressed_reason: "variant_enumeration_guard", selector_text: picker } },
      }],
    });

    expect(brief(turnReplies(withPicker, withPicker.turns[0], "B"))).toEqual([
      `comp 🧩 Selector de opciones (armado con la lista que escribió el bot)\n${picker}`,
    ]);
  });

  it("el mensaje de complemento del bot nuevo se ve después, marcado", () => {
    const withComplement: LabThread = threadSchema.parse({
      ...thread,
      turns: [{
        ...thread.turns[0],
        outputs: { B: { sent_texts: ["Tenemos 4 piezas de la colección de Halloween"], tools: [],
          complement_texts: ["Buenas noches 🤍 Bienvenido a *Hubara*"] } },
      }],
    });

    const replies = turnReplies(withComplement, withComplement.turns[0], "B");
    expect(brief(replies)).toEqual([
      "out Tenemos 4 piezas de la colección de Halloween",
      "note Después mandó un mensaje de complemento (Jev notó que faltaba algo):",
      "out Buenas noches 🤍 Bienvenido a *Hubara*",
    ]);
    expect(replies[2]).toMatchObject({ complement: true });
  });

  it("producción sin respuesta propia: el cliente volvió a escribir antes y la respuesta salió con el turno siguiente", () => {
    const [first, second] = thread.turns;
    const quick: LabThread = {
      ...thread,
      messages: [
        { role: "user", content: "Buenas tardes", timestamp: "2026-09-23T15:40:00+00:00", sender: null, kind: null, component_kind: null, wamid: first.burst[0].wamid, has_image: false },
        ...second.burst.map((m) => ({ role: "user", content: m.text, timestamp: null, sender: null, kind: null, component_kind: null, wamid: m.wamid, has_image: false })),
        { role: "assistant", content: "Te respondo las dos cosas", timestamp: null, sender: null, kind: null, component_kind: null, wamid: null, has_image: false },
      ],
    };

    expect(brief(turnReplies(quick, first, "A0"))).toEqual([
      "note El cliente volvió a escribir antes de que el bot respondiera: la respuesta salió con el turno siguiente.",
    ]);
  });

  it("lo que escribió una persona del equipo queda marcado", () => {
    const withHuman: LabThread = {
      ...thread,
      messages: thread.messages.map((m, k) => (k === 1 ? { ...m, sender: "human" } : m)),
    };

    expect(turnReplies(withHuman, thread.turns[0], "A0")[0]).toMatchObject({ dir: "out", byHuman: true });
    const item = buildThreadView(withHuman, "A0").find((it) => it.type === "msg" && it.dir === "out");
    expect(item).toMatchObject({ byHuman: true });
  });
});
