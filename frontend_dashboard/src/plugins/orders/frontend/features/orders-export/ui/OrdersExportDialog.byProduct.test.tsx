/**
 * La sección «Por producto» del reporte (tarjeta del ciclo #49): las líneas se
 * piden solo al abrir el diálogo; la tabla trae las columnas pedidas, el aviso
 * de las columnas sin fuente, y avisa si la tienda no respondió o si un pedido
 * no cuadra. Datos inventados.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { NO_DATE_RANGE } from "@/shared/lib";
import type { Order, OrderItemDetail, OrderLinesResponse } from "@plugins/orders/frontend/entities/order";

const lines = vi.hoisted(() => ({
  current: { data: undefined, isLoading: true } as { data?: OrderLinesResponse; isLoading: boolean },
  calls: 0,
}));

vi.mock("@plugins/orders/frontend/entities/order", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useOrderLines: () => {
    lines.calls += 1;
    return lines.current;
  },
}));

import { OrdersExportDialog } from "./OrdersExportDialog";

const ORDER: Order = {
  id: "#1",
  customer: "Ana",
  short: "AN",
  color: "a",
  phone: "—",
  city: "Bogotá",
  channel: "WhatsApp",
  status: "preparing",
  payStatus: "pending",
  payType: "cod",
  items: 1,
  total: 32000,
  shipping: 0,
  dueIso: "2026-11-02",
  dueTime: "—",
  pieces: 2,
  agent: "—",
  priority: "normal",
  isDraft: false,
  isDueEstimated: false,
  createdIso: "",
};

const ITEM: OrderItemDetail = {
  title: "Vela Luna",
  sku: null,
  quantity: 2,
  unit_price_cop: 16000,
  total_cop: 32000,
  variant_label: "Lavanda",
  variant_label_mismatch: false,
  thumbnail: null,
  handle: null,
  variant_match_kind: null,
  selected_variant_title: null,
  variant_unresolved_tokens: [],
  variant_unresolved_tag_kinds: [],
  coupon_code: null,
  list_unit_price_cop: null,
  discount_unit_cop: 0,
  color: null,
  aroma: null,
  item_id: null,
  price_adjusted_reason: null,
};

function response(items: OrderItemDetail[], ok = true): OrderLinesResponse {
  return {
    orders: ok ? [{ order_id: "order_1", display_id: "#1", payment_method_label: null, items }] : [],
    count: ok ? 1 : 0,
    offset: 0,
    limit: 100,
    catalog_available: ok,
    error_detail: ok ? null : "medusa_unavailable: HTTP 503",
  };
}

function renderDialog(open = true) {
  render(
    <OrdersExportDialog
      open={open}
      onClose={() => {}}
      orders={[ORDER]}
      listLimit={100}
      initialRange={NO_DATE_RANGE}
      activeDays={new Set()}
      today="2026-11-06"
    />,
  );
}

beforeEach(() => {
  lines.calls = 0;
  lines.current = { data: response([ITEM]), isLoading: false };
});

describe("Reporte de ventas: por producto", () => {
  it("cerrado no pide las líneas", () => {
    renderDialog(false);

    expect(lines.calls).toBe(0);
  });

  it("abierto muestra una fila por producto con las columnas pedidas", () => {
    renderDialog();

    const section = screen.getByRole("region", { name: "Por producto" });
    const headers = within(section).getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers.slice(0, 14)).toEqual([
      "Producto",
      "Producto en el catálogo (variante)",
      "Cantidad",
      "Costo unitario",
      "Precio unitario",
      "Costo total",
      "Venta total sin envío",
      "Ganancia",
      "Margen",
      "Cliente",
      "Número de pedido",
      "Línea de producto",
      "Costo completo unitario",
      "Canal de pago",
    ]);
    const row = within(section).getByRole("row", { name: /Vela Luna/ });
    expect(within(row).getByText("Lavanda")).toBeInTheDocument();
    expect(within(row).getByText("Contra entrega")).toBeInTheDocument();
  });

  it("avisa que costo, ganancia, margen y línea quedan vacíos", () => {
    renderDialog();

    const section = screen.getByRole("region", { name: "Por producto" });
    expect(within(section).getByRole("note", { name: /sin costo/i })).toHaveTextContent(
      "Sin fuente de costo ni de línea: estas columnas quedan vacías",
    );
  });

  it("si la tienda no respondió, lo dice en vez de mostrar una tabla vacía", () => {
    lines.current = { data: response([], false), isLoading: false };
    renderDialog();

    const section = screen.getByRole("region", { name: "Por producto" });
    expect(within(section).getByText(/no se pudo traer el detalle por producto/i)).toBeInTheDocument();
    expect(within(section).queryByRole("table")).not.toBeInTheDocument();
  });

  it("un pedido cuyas líneas no suman su valor sale avisado con la diferencia", () => {
    lines.current = { data: response([{ ...ITEM, quantity: 1, total_cop: 16000 }]), isLoading: false };
    renderDialog();

    const section = screen.getByRole("region", { name: "Por producto" });
    expect(within(section).getByRole("note", { name: /no cuadran/i })).toHaveTextContent("#1");
  });

  it("mientras carga, lo dice", () => {
    lines.current = { data: undefined, isLoading: true };
    renderDialog();

    const section = screen.getByRole("region", { name: "Por producto" });
    expect(within(section).getByText(/cargando/i)).toBeInTheDocument();
  });
});
