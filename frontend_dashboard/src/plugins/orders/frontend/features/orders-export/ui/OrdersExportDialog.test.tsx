/**
 * El diálogo del reporte (tarjeta del ciclo #18), revisión:
 *   - Si Orders cargó el tope de pedidos, el reporte avisa que puede faltar
 *     historia, también con «Todas las fechas».
 *   - Dice qué cuenta: todas las modalidades, sin la búsqueda ni la vista.
 *   - Las tablas de desglose dicen qué es cada columna.
 */
import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { NO_DATE_RANGE } from "@/shared/lib";
import type { Order } from "@plugins/orders/frontend/entities/order";
import { OrdersExportDialog } from "./OrdersExportDialog";

function order(id: string, dueIso: string): Order {
  return {
    id,
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
    dueIso,
    dueTime: "—",
    pieces: 1,
    agent: "—",
    priority: "normal",
    isDraft: false,
    isDueEstimated: false,
    createdIso: "",
  };
}

function renderDialog(orders: Order[], listLimit: number | undefined) {
  render(
    <OrdersExportDialog
      open
      onClose={() => {}}
      orders={orders}
      listLimit={listLimit}
      initialRange={NO_DATE_RANGE}
      activeDays={new Set()}
      today="2026-11-06"
    />,
  );
}

describe("OrdersExportDialog", () => {
  it("con el tope de pedidos cargado avisa que puede faltar historia, aun sin rango", () => {
    renderDialog([order("#1", "2026-11-01"), order("#2", "2026-11-02")], 2);

    expect(screen.getByRole("note", { name: /puede faltar historia/i })).toBeInTheDocument();
  });

  it("sin llegar al tope no avisa", () => {
    renderDialog([order("#1", "2026-11-01")], 100);

    expect(screen.queryByRole("note", { name: /puede faltar historia/i })).not.toBeInTheDocument();
  });

  it("dice que cuenta todas las modalidades, sin la búsqueda ni la vista", () => {
    renderDialog([order("#1", "2026-11-01")], 100);

    expect(screen.getByText(/todas las modalidades/i)).toBeInTheDocument();
  });

  it("las tablas de desglose tienen encabezados", () => {
    renderDialog([order("#1", "2026-11-01")], 100);

    const byDay = screen.getByRole("region", { name: "Por día" });
    expect(within(byDay).getByRole("columnheader", { name: "Pedidos" })).toBeInTheDocument();
    expect(within(byDay).getByRole("columnheader", { name: "Ventas" })).toBeInTheDocument();
  });
});
