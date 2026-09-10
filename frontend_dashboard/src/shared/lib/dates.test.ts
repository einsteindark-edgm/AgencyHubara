/**
 * Helpers de día calendario en hora de Colombia (America/Bogota).
 *
 * El resto del dashboard usaba `toISOString().slice(0,10)` (UTC), lo que corta
 * el día a las 19:00 hora Colombia. Para Chats eso es inaceptable: un mensaje
 * de las 20:00 del lunes aparecía como martes.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  addDaysBogotaIso,
  bogotaDayIsoFromMs,
  bogotaDayIsoFromUnix,
  formatBogotaHourMinute,
  formatDayLabelEs,
  nextDaysBogotaIsoSet,
  shiftIsoDay,
  todayBogotaIso,
} from "./dates";

describe("día calendario en America/Bogota", () => {
  it("un instante UTC de madrugada pertenece al día ANTERIOR en Bogotá", () => {
    // 2026-09-08T02:00:00Z === 2026-09-07 21:00 en Bogotá (UTC-5)
    const ms = Date.UTC(2026, 8, 8, 2, 0, 0);
    expect(bogotaDayIsoFromMs(ms)).toBe("2026-09-07");
  });

  it("acepta unix epoch en SEGUNDOS (formato del backend)", () => {
    const unix = Date.UTC(2026, 8, 8, 2, 0, 0) / 1000;
    expect(bogotaDayIsoFromUnix(unix)).toBe("2026-09-07");
  });

  it("un instante de mediodía UTC cae el mismo día en Bogotá", () => {
    expect(bogotaDayIsoFromMs(Date.UTC(2026, 8, 8, 12, 0, 0))).toBe("2026-09-08");
  });

  it("timestamp ausente/0 no produce un día falso", () => {
    expect(bogotaDayIsoFromUnix(0)).toBe("");
  });

  it("todayBogotaIso devuelve un YYYY-MM-DD bien formado", () => {
    expect(todayBogotaIso()).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});

describe("formatBogotaHourMinute", () => {
  it("formatea en hora Colombia, 24h, sin depender del TZ del navegador", () => {
    const unix = Date.UTC(2026, 8, 8, 2, 5, 0) / 1000;
    expect(formatBogotaHourMinute(unix)).toBe("21:05");
  });

  it("timestamp vacío → string vacío", () => {
    expect(formatBogotaHourMinute(0)).toBe("");
  });
});

describe("shiftIsoDay", () => {
  it("resta días cruzando el borde de mes", () => {
    expect(shiftIsoDay("2026-09-01", -1)).toBe("2026-08-31");
  });
  it("suma días cruzando el borde de año", () => {
    expect(shiftIsoDay("2026-12-31", 1)).toBe("2027-01-01");
  });
});

describe("formatDayLabelEs (separadores estilo WhatsApp)", () => {
  const today = "2026-09-10"; // jueves

  it("hoy → 'Hoy'", () => {
    expect(formatDayLabelEs("2026-09-10", today)).toBe("Hoy");
  });

  it("ayer → 'Ayer'", () => {
    expect(formatDayLabelEs("2026-09-09", today)).toBe("Ayer");
  });

  it("dentro de la última semana → nombre del día capitalizado", () => {
    expect(formatDayLabelEs("2026-09-07", today)).toBe("Lunes");
  });

  it("más viejo → fecha larga en español", () => {
    expect(formatDayLabelEs("2026-08-21", today)).toBe("21 de agosto de 2026");
  });
});

/* ── Borde de las 19:00 hora Colombia ────────────────────────────────
 *
 * Con el reloj congelado a las 20:00 del 10 de septiembre en Bogotá
 * (01:00Z del 11), el día UTC ya es el 11 y el colombiano sigue siendo el 10.
 * Ahí vivía el bug de Órdenes: "Para hoy" mostraba las entregas de mañana y
 * "Retrasadas" pintaba de rojo las de hoy.
 */
describe("helpers relativos a hoy, en el borde de las 19:00", () => {
  const VEINTE_HORAS_BOGOTA = new Date("2026-09-11T01:00:00.000Z");

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(VEINTE_HORAS_BOGOTA);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("todayBogotaIso NO adelanta el día a las 20:00 (el UTC sí)", () => {
    expect(new Date().toISOString().slice(0, 10)).toBe("2026-09-11"); // día UTC
    expect(todayBogotaIso()).toBe("2026-09-10");
  });

  it("addDaysBogotaIso(1) es mañana en Colombia, no pasado mañana", () => {
    expect(addDaysBogotaIso(1)).toBe("2026-09-11");
  });

  it("addDaysBogotaIso acepta negativos", () => {
    expect(addDaysBogotaIso(-1)).toBe("2026-09-09");
  });

  it("addDaysBogotaIso(0) es hoy", () => {
    expect(addDaysBogotaIso(0)).toBe("2026-09-10");
  });

  it("nextDaysBogotaIsoSet(7) arranca HOY e incluye los 6 siguientes", () => {
    expect([...nextDaysBogotaIsoSet(7)]).toEqual([
      "2026-09-10", "2026-09-11", "2026-09-12", "2026-09-13",
      "2026-09-14", "2026-09-15", "2026-09-16",
    ]);
  });

  it("formatDayLabelEs sin `today` explícito usa el día colombiano", () => {
    expect(formatDayLabelEs("2026-09-10")).toBe("Hoy");
    expect(formatDayLabelEs("2026-09-09")).toBe("Ayer");
  });
});
