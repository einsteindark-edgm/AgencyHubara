/**
 * Agrupación y filtro por fecha del inbox.
 *
 * Bug reportado (2026-09-10): la sección "Hoy" era literalmente
 * `rest.slice(0, 6)` — los primeros seis chats de la lista, sin mirar una sola
 * fecha. Un chat de hace tres semanas aparecía bajo "Hoy" y uno de esta mañana
 * bajo "Anteriores" con solo tener siete conversaciones en la bandeja.
 */
import { describe, expect, it } from "vitest";
import { act, renderHook } from "@testing-library/react";
import type { ChatInboxItem } from "@plugins/chats/frontend/entities/chat";
import { useInboxFilters } from "./useInboxFilters";

const TODAY = "2026-09-10";

function chat(overrides: Partial<ChatInboxItem> & { id: string }): ChatInboxItem {
  return {
    name: "573001112233",
    short: "33",
    snippet: "…",
    time: "10:00",
    timestamp: 0,
    dayIso: TODAY,
    tag: "INTERESADO",
    tagClass: "t-int",
    color: "blue",
    presence: "online",
    unread: 0,
    ...overrides,
  };
}

/** Nueve chats: 2 de hoy, 7 repartidos en días anteriores. Con la lógica vieja
 *  (`slice(0,6)`) los primeros 6 caían en "Hoy" sin importar la fecha. */
const CHATS: ChatInboxItem[] = [
  chat({ id: "a", dayIso: "2026-08-20", timestamp: 100 }),
  chat({ id: "b", dayIso: "2026-09-01", timestamp: 200 }),
  chat({ id: "c", dayIso: "2026-09-08", timestamp: 300 }),
  chat({ id: "d", dayIso: "2026-09-09", timestamp: 400 }),
  chat({ id: "e", dayIso: "2026-09-09", timestamp: 500 }),
  chat({ id: "f", dayIso: "2026-09-09", timestamp: 600 }),
  chat({ id: "g", dayIso: "2026-09-10", timestamp: 700 }),
  chat({ id: "h", dayIso: "2026-09-10", timestamp: 800 }),
  chat({ id: "i", dayIso: "2026-09-05", timestamp: 900 }),
];

function run(chats: ChatInboxItem[] = CHATS) {
  return renderHook(() => useInboxFilters(chats, { today: TODAY }));
}

/** Ids de la sección `key`, o [] si la sección no se renderiza. */
function idsOf(sections: { key: string; items: ChatInboxItem[] }[], key: string) {
  return sections.find((s) => s.key === key)?.items.map((c) => c.id) ?? [];
}

describe("sección 'Hoy' (regresión: era slice(0,6))", () => {
  it("contiene SOLO los chats cuyo día colombiano es hoy", () => {
    const { result } = run();
    act(() => result.current.setActiveFilter("Todas"));
    expect(idsOf(result.current.sections, "today").sort()).toEqual(["g", "h"]);
  });

  it("el resto cae en 'Anteriores', incluidos los que estaban en el top-6", () => {
    const { result } = run();
    act(() => result.current.setActiveFilter("Todas"));
    expect(idsOf(result.current.sections, "earlier").sort()).toEqual([
      "a", "b", "c", "d", "e", "f", "i",
    ]);
  });

  it("ordena cada sección por recencia (más nuevo primero)", () => {
    const { result } = run();
    act(() => result.current.setActiveFilter("Todas"));
    expect(idsOf(result.current.sections, "earlier")).toEqual([
      "i", "f", "e", "d", "c", "b", "a",
    ]);
  });

  it("si no hay chats de hoy, no se renderiza la sección 'Hoy' vacía", () => {
    const { result } = run(CHATS.filter((c) => c.dayIso !== TODAY));
    act(() => result.current.setActiveFilter("Todas"));
    expect(result.current.sections.some((s) => s.key === "today")).toBe(false);
  });
});

describe("filtro por rango de fechas (calendario)", () => {
  it("un solo día deja únicamente los chats de ese día", () => {
    const { result } = run();
    act(() => result.current.setActiveFilter("Todas"));
    act(() => result.current.setDateRange({ from: "2026-09-09", to: "2026-09-09" }));
    expect(result.current.filtered.map((c) => c.id).sort()).toEqual(["d", "e", "f"]);
  });

  it("un rango incluye ambos extremos", () => {
    const { result } = run();
    act(() => result.current.setActiveFilter("Todas"));
    act(() => result.current.setDateRange({ from: "2026-09-08", to: "2026-09-10" }));
    expect(result.current.filtered.map((c) => c.id).sort()).toEqual([
      "c", "d", "e", "f", "g", "h",
    ]);
  });

  it("con rango activo colapsa en UNA sección de resultados (no Hoy/Anteriores)", () => {
    const { result } = run();
    act(() => result.current.setActiveFilter("Todas"));
    act(() => result.current.setDateRange({ from: "2026-09-09", to: "2026-09-09" }));
    expect(result.current.sections.map((s) => s.key)).toEqual(["results"]);
  });

  it("se combina con el filtro de tag, no lo reemplaza", () => {
    const chats = [
      chat({ id: "x", dayIso: "2026-09-09", tag: "CLIENTE", timestamp: 1 }),
      chat({ id: "y", dayIso: "2026-09-09", tag: "INTERESADO", timestamp: 2 }),
    ];
    const { result } = run(chats);
    act(() => result.current.setActiveFilter("Cliente"));
    act(() => result.current.setDateRange({ from: "2026-09-09", to: "2026-09-09" }));
    expect(result.current.filtered.map((c) => c.id)).toEqual(["x"]);
  });

  it("clearDateRange devuelve la bandeja completa y las secciones por día", () => {
    const { result } = run();
    act(() => result.current.setActiveFilter("Todas"));
    act(() => result.current.setDateRange({ from: "2026-09-09", to: "2026-09-09" }));
    act(() => result.current.clearDateRange());
    expect(result.current.filtered).toHaveLength(CHATS.length);
    expect(result.current.sections.map((s) => s.key)).toEqual(["today", "earlier"]);
  });

  it("un rango sin actividad da cero resultados (no cae al comportamiento sin filtro)", () => {
    const { result } = run();
    act(() => result.current.setActiveFilter("Todas"));
    act(() => result.current.setDateRange({ from: "2026-01-01", to: "2026-01-31" }));
    expect(result.current.filtered).toHaveLength(0);
  });

  it("un rango a medio elegir (solo 'from') filtra por ese día suelto", () => {
    const { result } = run();
    act(() => result.current.setActiveFilter("Todas"));
    act(() => result.current.setDateRange({ from: "2026-09-08", to: null }));
    expect(result.current.filtered.map((c) => c.id)).toEqual(["c"]);
  });

  it("los contadores de los pills reflejan el rango activo", () => {
    const { result } = run();
    act(() => result.current.setDateRange({ from: "2026-09-10", to: "2026-09-10" }));
    const todas = result.current.filters.find((f) => f.key === "Todas");
    expect(todas?.count).toBe(2);
  });

  it("rotula un día suelto en lenguaje humano", () => {
    const { result } = run();
    act(() => result.current.setDateRange({ from: "2026-09-09", to: "2026-09-09" }));
    expect(result.current.dateRangeLabel).toBe("Ayer");
  });

  it("rotula un rango de forma COMPACTA (entra en los 280px de la sidebar)", () => {
    const { result } = run();
    act(() => result.current.setDateRange({ from: "2026-09-08", to: "2026-09-09" }));
    // "8 de septiembre de 2026 – 9 de septiembre de 2026" se trunca con elipsis
    // y el operador deja de ver qué rango tiene puesto.
    expect(result.current.dateRangeLabel).toBe("8 – 9 sep 2026");
  });

  it("un rango que cruza meses nombra ambos", () => {
    const { result } = run();
    act(() => result.current.setDateRange({ from: "2026-08-20", to: "2026-09-09" }));
    expect(result.current.dateRangeLabel).toBe("20 ago – 9 sep 2026");
  });

  it("un rango que cruza años lleva ambos años", () => {
    const { result } = run();
    act(() => result.current.setDateRange({ from: "2025-12-30", to: "2026-01-02" }));
    expect(result.current.dateRangeLabel).toBe("30 dic 2025 – 2 ene 2026");
  });

  it("sin rango el disparador dice que no hay filtro", () => {
    const { result } = run();
    expect(result.current.dateRangeLabel).toBe("Todas las fechas");
  });

  it("expone los días CON actividad para que el calendario los marque", () => {
    const { result } = run();
    act(() => result.current.setActiveFilter("Todas"));
    expect([...result.current.activeDays].sort()).toEqual([
      "2026-08-20", "2026-09-01", "2026-09-05", "2026-09-08", "2026-09-09", "2026-09-10",
    ]);
  });
});

describe("fijadas y filtro Humano", () => {
  it("las fijadas siguen en su propia sección, fuera de Hoy/Anteriores", () => {
    const chats = [
      chat({ id: "p", dayIso: TODAY, pinned: true, timestamp: 10 }),
      chat({ id: "q", dayIso: TODAY, timestamp: 20 }),
    ];
    const { result } = run(chats);
    act(() => result.current.setActiveFilter("Todas"));
    expect(idsOf(result.current.sections, "pinned")).toEqual(["p"]);
    expect(idsOf(result.current.sections, "today")).toEqual(["q"]);
  });

  it("el filtro Humano es una cola única: no parte por día", () => {
    const chats = [
      chat({ id: "r", dayIso: "2026-08-01", human: true, timestamp: 10 }),
      chat({ id: "s", dayIso: TODAY, human: true, timestamp: 20 }),
    ];
    const { result } = run(chats);
    expect(result.current.sections.map((s) => s.key)).toEqual(["waiting"]);
    expect(idsOf(result.current.sections, "waiting")).toEqual(["s", "r"]);
  });
});
