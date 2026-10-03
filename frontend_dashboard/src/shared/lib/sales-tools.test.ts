import { describe, expect, it } from "vitest";

import { describeTool } from "./sales-tools";

/**
 * Las herramientas del bot de ventas en palabras del operador (revisión del
 * laboratorio 2026-09-29): el hilo del turno decía `search_products`,
 * `resultado` y `rechazada: url_not_whitelisted`; ahora dice qué hizo el bot,
 * con qué y qué encontró.
 */

describe("describeTool", () => {
  it("la búsqueda en el catálogo dice qué buscó y cuánto encontró", () => {
    expect(describeTool({ name: "search_products", args: { q: "jesús", limit: 10 }, ok: true, notes: ["count:0"] })).toEqual({
      action: "Buscar en el catálogo",
      shown: null,
      detail: "«jesús»",
      result: "no encontró nada",
      component: false,
      failed: false,
    });
    const byCategory = describeTool({ name: "search_products", args: { category: "religiosas", limit: 30 }, ok: true, notes: ["count:8"] });
    expect([byCategory.detail, byCategory.result]).toEqual(["categoría religiosas", "8 productos"]);
    expect(describeTool({ name: "search_products", args: { q: "gorrión" }, ok: true, notes: ["count:1"] }).result).toBe("1 producto");
  });

  it("lo que el cliente ve en WhatsApp queda marcado como componente, con su producto", () => {
    expect(describeTool({ name: "present_product_detail", args: { handle: "sagrado-rostro" }, ok: true })).toMatchObject({
      action: "Mostrar un producto",
      shown: "Tarjeta del producto",
      detail: "Sagrado rostro",
      component: true,
    });
    expect(describeTool({ name: "present_products", args: { handles: '["a", "b"]' }, ok: true, notes: ["count:8"] })).toMatchObject({
      action: "Mostrar productos",
      result: "8 productos",
      component: true,
    });
    // El texto de send_reply ya viaja como burbuja: no es un componente aparte.
    expect(describeTool({ name: "send_reply", args: { text: "Hola" }, ok: true }).component).toBe(false);
  });

  it("la lista de productos dice cuáles mostró (el operador no veía que eran las religiosas)", () => {
    const shown = describeTool({
      name: "present_products",
      args: { handles: '["sagrado-rostro", "sacrificio-de-amor", "plegaria-de-luz", "luz-serena", "angel"]' },
      ok: true,
      notes: ["count:5"],
    });
    expect(shown.detail).toBe("Sagrado rostro, Sacrificio de amor, Plegaria de luz +2");
    expect(describeTool({ name: "present_products", args: { handles: ["calabaza", "momia"] }, ok: true }).detail).toBe("Calabaza, Momia");
  });

  it("anotar en el pedido dice qué anotó", () => {
    expect(describeTool({ name: "set_order_slot", args: { producto: "Velón Gorrión", color: "lila" }, ok: true }).detail).toBe(
      "producto: Velón Gorrión · color: lila",
    );
  });

  it("una herramienta rechazada dice por qué en palabras", () => {
    expect(describeTool({ name: "send_cta_url", args: { button_text: "Ver catálogo" }, ok: false, error: "url_not_whitelisted" })).toMatchObject({
      action: "Enviar un botón con enlace",
      detail: "Ver catálogo",
      result: "rechazada: el enlace no está permitido",
      failed: true,
    });
    expect(describeTool({ name: "request_shipping_details", ok: false, error: "motivo_raro" }).result).toBe("rechazada: motivo_raro");
  });

  it("una respuesta que send_reply devolvió al modelo dice que no salió y por qué", () => {
    // Laboratorio 2026-09-30: las retenciones de send_reply (una por mensaje
    // del cliente) salían como «rechazada: verified_photos».
    const reasons = ["verified_photos", "promise_later", "option_list", "internal_text"].map(
      (error) => describeTool({ name: "send_reply", args: { text: "…" }, ok: false, error }).result,
    );

    expect(reasons).toEqual([
      "no salió: negaba un producto que las fotos ya confirmaron",
      "no salió: prometía revisar y responder después",
      "no salió: escribió la lista de opciones como texto",
      "no salió: era una nota interna, no para el cliente",
    ]);
  });

  it("una herramienta que no conoce se muestra con su nombre", () => {
    expect(describeTool({ name: "tool_nueva", ok: true })).toEqual({
      action: "tool_nueva",
      shown: null,
      detail: null,
      result: "listo",
      component: false,
      failed: false,
    });
  });
});
