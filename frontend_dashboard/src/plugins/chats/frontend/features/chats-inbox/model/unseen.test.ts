/**
 * Contador de NO LEÍDOS como WhatsApp: lo que decide es si el operador ABRIÓ
 * el chat desde que el cliente escribió — no quién habló último. Un "gracias,
 * hasta luego" del cliente después de la respuesta del bot también es un no
 * leído mientras nadie abra la conversación.
 */
import { describe, expect, it } from "vitest";
import { EMPTY_SEEN, reconcileSeen, unseenCount } from "./unseen";

const c = (id: string, inboundCount: number) => ({ id, inboundCount });

describe("no leídos", () => {
  it("primera carga: lo que ya existía cuenta como visto (no inunda la bandeja)", () => {
    const seen = reconcileSeen(EMPTY_SEEN, [c("a", 5), c("b", 2)], null);
    expect(unseenCount(seen, c("a", 5))).toBe(0);
    expect(unseenCount(seen, c("b", 2))).toBe(0);
  });

  it("sin chats cargados todavía no fija la línea base", () => {
    expect(reconcileSeen(EMPTY_SEEN, [], null)).toBe(EMPTY_SEEN);
  });

  it("mensajes nuevos del cliente cuentan hasta que se abre el chat", () => {
    let seen = reconcileSeen(EMPTY_SEEN, [c("a", 5)], null);
    seen = reconcileSeen(seen, [c("a", 8)], null);
    expect(unseenCount(seen, c("a", 8))).toBe(3);

    seen = reconcileSeen(seen, [c("a", 8)], "a");
    expect(unseenCount(seen, c("a", 8))).toBe(0);
  });

  it("el chat abierto no acumula: lo que llega mientras está abierto ya se ve", () => {
    let seen = reconcileSeen(EMPTY_SEEN, [c("a", 1)], "a");
    seen = reconcileSeen(seen, [c("a", 4)], "a");
    expect(unseenCount(seen, c("a", 4))).toBe(0);
  });

  it("un chat que aparece después de la línea base cuenta todos sus mensajes", () => {
    let seen = reconcileSeen(EMPTY_SEEN, [c("a", 1)], null);
    seen = reconcileSeen(seen, [c("a", 1), c("nuevo", 2)], null);
    expect(unseenCount(seen, c("nuevo", 2))).toBe(2);
  });

  it("sin cambios devuelve el mismo estado (no re-persiste en cada tick)", () => {
    const seen = reconcileSeen(EMPTY_SEEN, [c("a", 1)], "a");
    expect(reconcileSeen(seen, [c("a", 1)], "a")).toBe(seen);
  });
});
