/**
 * Tests del detector puro de handoffs nuevos: dada la foto anterior de rutas
 * por sesión y la lista actual del inbox, ¿qué chats ACABAN de pasar a manos
 * del humano? Es la base de la notificación al operador (móvil/desktop).
 */

import { describe, it, expect } from "vitest";
import { diffNewHandoffs } from "./handoff-notify";

type Item = { id: string; name: string; human?: boolean };

const inbox = (...items: Item[]) => items;

describe("diffNewHandoffs", () => {
  it("detecta una transición bot → humano", () => {
    const prev = new Map([["wa_1", false]]);
    const { newlyAssigned } = diffNewHandoffs(
      prev,
      inbox({ id: "wa_1", name: "Cliente 1", human: true }),
    );
    expect(newlyAssigned.map((c) => c.id)).toEqual(["wa_1"]);
  });

  it("NO notifica chats que YA estaban en humano", () => {
    const prev = new Map([["wa_1", true]]);
    const { newlyAssigned } = diffNewHandoffs(
      prev,
      inbox({ id: "wa_1", name: "Cliente 1", human: true }),
    );
    expect(newlyAssigned).toEqual([]);
  });

  it("NO notifica en la primera carga (sin foto previa) — evita la ráfaga al abrir la app", () => {
    const prev = new Map<string, boolean>(); // vacío = primer snapshot
    const { newlyAssigned, isFirstSnapshot } = diffNewHandoffs(
      prev,
      inbox(
        { id: "wa_1", name: "a", human: true },
        { id: "wa_2", name: "b", human: true },
      ),
    );
    expect(isFirstSnapshot).toBe(true);
    expect(newlyAssigned).toEqual([]);
  });

  it("una sesión NUEVA que aparece directamente en humano SÍ notifica", () => {
    const prev = new Map([["wa_1", false]]);
    const { newlyAssigned } = diffNewHandoffs(
      prev,
      inbox(
        { id: "wa_1", name: "a", human: false },
        { id: "wa_9", name: "nueva", human: true },
      ),
    );
    expect(newlyAssigned.map((c) => c.id)).toEqual(["wa_9"]);
  });

  it("devolver al bot (humano → ventas) no notifica y actualiza la foto", () => {
    const prev = new Map([["wa_1", true]]);
    const { newlyAssigned, nextSnapshot } = diffNewHandoffs(
      prev,
      inbox({ id: "wa_1", name: "a", human: false }),
    );
    expect(newlyAssigned).toEqual([]);
    expect(nextSnapshot.get("wa_1")).toBe(false);
  });

  it("múltiples transiciones simultáneas se reportan todas", () => {
    const prev = new Map([
      ["wa_1", false],
      ["wa_2", false],
    ]);
    const { newlyAssigned } = diffNewHandoffs(
      prev,
      inbox(
        { id: "wa_1", name: "a", human: true },
        { id: "wa_2", name: "b", human: true },
      ),
    );
    expect(newlyAssigned.map((c) => c.id).sort()).toEqual(["wa_1", "wa_2"]);
  });
});
