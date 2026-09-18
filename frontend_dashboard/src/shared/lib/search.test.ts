/**
 * Qué coincide con lo que el operador escribe en los buscadores de las
 * sidebars (Chats, Órdenes, Ads).
 *
 * Bug reportado (2026-09-18): los tres buscadores eran un `<input>` suelto, sin
 * estado ni filtro — escribir no hacía nada. Esta es la regla única de
 * coincidencia para los tres, así "maria" encuentra a María en todos lados.
 */
import { describe, expect, it } from "vitest";
import { matchesSearch } from "./search";

describe("matchesSearch", () => {
  it("sin búsqueda (vacía o solo espacios) todo coincide", () => {
    expect(matchesSearch("", ["Velas"])).toBe(true);
    expect(matchesSearch("   ", ["Velas"])).toBe(true);
  });

  it("encuentra por fragmento, sin importar mayúsculas", () => {
    expect(matchesSearch("PADRE", ["Día del Padre"])).toBe(true);
    expect(matchesSearch("madre", ["Día del Padre"])).toBe(false);
  });

  it("ignora las tildes en los dos sentidos", () => {
    expect(matchesSearch("bogota", ["Bogotá"])).toBe(true);
    expect(matchesSearch("DÍA", ["dia del padre"])).toBe(true);
  });

  it("varias palabras: todas tienen que aparecer, en cualquiera de los campos", () => {
    expect(matchesSearch("maria #31", ["María Pérez", "#31"])).toBe(true);
    expect(matchesSearch("maria #32", ["María Pérez", "#31"])).toBe(false);
  });

  it("un teléfono se encuentra escrito con o sin espacios, + o guiones", () => {
    expect(matchesSearch("300 123 4567", ["573001234567"])).toBe(true);
    expect(matchesSearch("+57 300-123-4567", ["573001234567"])).toBe(true);
    expect(matchesSearch("3001234567", ["+57 300 123 4567"])).toBe(true);
  });

  it("los dígitos sueltos de un texto libre no se juntan en un número inventado", () => {
    // "2 velas de 31 cm" no menciona el número 231.
    expect(matchesSearch("231", ["2 velas de 31 cm"])).toBe(false);
  });

  it("'#12' busca el pedido 12, no cualquier teléfono con un 12 adentro", () => {
    expect(matchesSearch("#12", ["#12"])).toBe(true);
    expect(matchesSearch("#12", ["573001234567"])).toBe(false);
    // Sin el '#' es un número cualquiera: también cae en el teléfono.
    expect(matchesSearch("12", ["573001234567"])).toBe(true);
  });

  it("los campos vacíos o ausentes no rompen ni coinciden", () => {
    expect(matchesSearch("velas", [null, undefined, ""])).toBe(false);
    expect(matchesSearch("velas", [null, "Velas"])).toBe(true);
  });
});
