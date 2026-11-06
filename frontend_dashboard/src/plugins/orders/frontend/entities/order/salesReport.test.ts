/**
 * Reporte de ventas por fechas (tarjeta del ciclo #18): las cifras del rango
 * salen de los mismos pedidos y las mismas reglas que la vista Orders, así que
 * cuadran con ella por construcción.
 *   - Cada pedido cae en su día (entrega agendada, o creación si no se agendó).
 *   - Los pedidos de prueba no cuentan en nada.
 *   - Los cancelados no suman ventas: van en su propia línea.
 *   - «Cobrado» es la misma cuenta del KPI «Ingresos» (entregado + pagado).
 * Los montos salen de los pedidos sintéticos de esta prueba, no de la config.
 */
import { describe, expect, it } from "vitest";
import { NO_DATE_RANGE } from "@/shared/lib";
import { isCollectedRevenue, orderDayIso, salesReport, type Order } from "./index";

function order(overrides: Partial<Order> & { id: string }): Order {
  return {
    customer: "Cliente",
    short: "CL",
    color: "a",
    phone: "—",
    city: "Bogotá",
    channel: "WhatsApp",
    status: "preparing",
    payStatus: "pending",
    payType: "cod",
    items: 1,
    total: 10000,
    shipping: 0,
    dueIso: "",
    dueTime: "—",
    pieces: 1,
    agent: "—",
    priority: "normal",
    isDraft: false,
    isDueEstimated: false,
    createdIso: "",
    ...overrides,
  };
}

const ORDERS: Order[] = [
  order({ id: "#1", dueIso: "2026-11-02", total: 30000, shipping: 5000, status: "delivered", payStatus: "paid", payType: "confirmed", city: "Medellín" }),
  order({ id: "#2", createdIso: "2026-11-03", total: 20000, shipping: 4000, status: "shipping", payType: "cod" }),
  order({ id: "#3", dueIso: "2026-11-03", total: 15000, status: "new", payType: "cod", channel: "Web" }),
  // Fuera del rango.
  order({ id: "#4", dueIso: "2026-10-28", total: 99000, status: "delivered", payStatus: "paid" }),
  // De prueba: no cuenta en nada.
  order({ id: "#5", dueIso: "2026-11-02", total: 77000, status: "delivered", payStatus: "paid", isTest: true }),
  // Cancelado: fuera de las ventas, en su línea.
  order({ id: "#6", dueIso: "2026-11-03", total: 12000, status: "cancelled" }),
];

const RANGE = { from: "2026-11-01", to: "2026-11-05" };

describe("salesReport", () => {
  it("suma los pedidos del rango, sin pruebas ni cancelados", () => {
    const r = salesReport(ORDERS, RANGE);

    expect(r.orders).toBe(3);
    expect(r.sales).toBe(30000 + 20000 + 15000);
    expect(r.averageTicket).toBe(Math.round((30000 + 20000 + 15000) / 3));
    expect(r.shipping).toBe(5000 + 4000);
  });

  it("lo cobrado es la misma cuenta del KPI «Ingresos» y lo demás está por cobrar", () => {
    const r = salesReport(ORDERS, RANGE);
    const kpi = ORDERS.filter(
      (o) => isCollectedRevenue(o) && orderDayIso(o) >= RANGE.from && orderDayIso(o) <= RANGE.to,
    ).reduce((a, o) => a + o.total, 0);

    expect(r.collected).toBe(kpi);
    expect(r.toCollect).toBe(r.sales - r.collected);
  });

  it("separa contra entrega de pago confirmado", () => {
    const r = salesReport(ORDERS, RANGE);

    expect(r.byPayType.cod).toEqual({ orders: 2, sales: 35000 });
    expect(r.byPayType.confirmed).toEqual({ orders: 1, sales: 30000 });
  });

  it("cuenta los cancelados aparte", () => {
    expect(salesReport(ORDERS, RANGE).cancelled).toEqual({ orders: 1, sales: 12000 });
  });

  it("desglosa por día, etapa, canal y ciudad", () => {
    const r = salesReport(ORDERS, RANGE);

    expect(r.byDay).toEqual([
      { key: "2026-11-02", orders: 1, sales: 30000 },
      { key: "2026-11-03", orders: 2, sales: 35000 },
    ]);
    expect(r.byStatus.find((x) => x.key === "delivered")).toEqual({ key: "delivered", orders: 1, sales: 30000 });
    expect(r.byChannel.find((x) => x.key === "Web")).toEqual({ key: "Web", orders: 1, sales: 15000 });
    expect(r.byCity.find((x) => x.key === "Medellín")).toEqual({ key: "Medellín", orders: 1, sales: 30000 });
  });

  it("sin rango toma todas las fechas, como la vista", () => {
    const r = salesReport(ORDERS, NO_DATE_RANGE);

    expect(r.orders).toBe(4);
    expect(r.sales).toBe(30000 + 20000 + 15000 + 99000);
  });

  it("un rango sin pedidos da ceros, sin dividir por cero", () => {
    const r = salesReport(ORDERS, { from: "2025-01-01", to: "2025-01-31" });

    expect(r.orders).toBe(0);
    expect(r.averageTicket).toBe(0);
  });
});
