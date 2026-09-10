/**
 * Helpers de día calendario en hora de Colombia (America/Bogota).
 *
 * El resto del dashboard usaba `toISOString().slice(0,10)` (UTC), lo que corta
 * el día a las 19:00 hora Colombia. Para Chats eso es inaceptable: un mensaje
 * de las 20:00 del lunes aparecía como martes.
 */
import { describe, expect, it } from "vitest";
import {
  bogotaDayIsoFromMs,
  bogotaDayIsoFromUnix,
  formatBogotaHourMinute,
  formatDayLabelEs,
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
