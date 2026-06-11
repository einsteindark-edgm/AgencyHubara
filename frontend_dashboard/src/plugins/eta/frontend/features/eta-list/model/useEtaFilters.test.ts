/**
 * Tests de `useEtaFilters` — en particular la distinción `cod` vs `codToday`.
 *
 * Regresión: el banner "N contra entrega hoy" del sidebar cuenta COD en la
 * calle (shipping/out), pero su click ruteaba al filtro `cod` genérico →
 * aparecían también los COD en preparación/listos (que aún no se cobran) y
 * el número del banner no coincidía con la lista mostrada.
 */

import { describe, expect, it } from "vitest";
import { act, renderHook } from "@testing-library/react";
import type { TrackedOrder } from "@plugins/eta/frontend/entities/tracked-order";
import { isCodToday, useEtaFilters } from "./useEtaFilters";

function order(over: Partial<TrackedOrder> = {}): TrackedOrder {
  return {
    id: "#1",
    customer: "Cliente WhatsApp",
    short: "CW",
    color: "a",
    phone: "573001112233",
    city: "",
    current: "preparing",
    channel: "WhatsApp",
    needs: false,
    payType: "confirmed",
    total: 10000,
    messagesUnread: 0,
    events: [],
    ...over,
  };
}

const ORDERS: TrackedOrder[] = [
  order({ id: "#6", payType: "cod", current: "shipping", total: 97000 }),
  order({ id: "#8", payType: "cod", current: "ready", total: 61000 }),
  order({ id: "#5", payType: "confirmed", current: "delivered" }),
  order({ id: "#4", payType: "confirmed", current: "shipping" }),
];

describe("useEtaFilters", () => {
  it("`cod` lista TODOS los contra entrega, en cualquier etapa", () => {
    const { result } = renderHook(() => useEtaFilters(ORDERS));
    act(() => result.current.setFilter("cod"));
    expect(result.current.list.map((o) => o.id)).toEqual(["#6", "#8"]);
  });

  it("`codToday` lista SOLO los contra entrega en la calle (lo que cuenta el banner)", () => {
    const { result } = renderHook(() => useEtaFilters(ORDERS));
    act(() => result.current.setFilter("codToday"));
    expect(result.current.list.map((o) => o.id)).toEqual(["#6"]);
  });

  it("`delivered` lista solo las entregadas (filtro nuevo del sidebar)", () => {
    const { result } = renderHook(() => useEtaFilters(ORDERS));
    act(() => result.current.setFilter("delivered"));
    expect(result.current.list.map((o) => o.id)).toEqual(["#5"]);
  });

  it("volver a `all` des-filtra (toggle del sidebar sin chip 'Todas')", () => {
    const { result } = renderHook(() => useEtaFilters(ORDERS));
    act(() => result.current.setFilter("cod"));
    expect(result.current.list).toHaveLength(2);
    act(() => result.current.setFilter("all"));
    expect(result.current.list).toHaveLength(ORDERS.length);
  });

  it("isCodToday acepta `out` y rechaza COD que no salió ni pagos anticipados", () => {
    expect(isCodToday(order({ payType: "cod", current: "out" }))).toBe(true);
    expect(isCodToday(order({ payType: "cod", current: "preparing" }))).toBe(false);
    expect(isCodToday(order({ payType: "confirmed", current: "shipping" }))).toBe(false);
  });

  it("el banner y el filtro `codToday` usan el mismo conjunto (no se desincronizan)", () => {
    const banner = ORDERS.filter(isCodToday);
    const { result } = renderHook(() => useEtaFilters(ORDERS));
    act(() => result.current.setFilter("codToday"));
    expect(result.current.list).toEqual(banner);
  });
});
