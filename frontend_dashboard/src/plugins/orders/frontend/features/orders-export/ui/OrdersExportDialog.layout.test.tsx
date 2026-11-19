/**
 * En pantalla, lo que se vendió en cada pedido se ve sin buscarlo
 * (2026-11-19): «Por producto» va justo después de las cifras, antes de los
 * desgloses, y no ocupa el ancho con columnas vacías (sin fuente de costo,
 * costo, ganancia, margen y línea no se muestran; el CSV sí las trae).
 * Datos inventados.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { NO_DATE_RANGE } from "@/shared/lib";
import type { Order, OrderItemDetail, OrderLinesResponse } from "@plugins/orders/frontend/entities/order";

const lines = vi.hoisted(() => ({
  current: { data: undefined, isLoading: true } as { data?: OrderLinesResponse; isLoading: boolean },
}));

vi.mock("@plugins/orders/frontend/entities/order", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useOrderLines: () => lines.current,
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

beforeEach(() => {
  lines.current = {
    data: {
      orders: [{ order_id: "order_1", display_id: "#1", payment_method_label: null, items: [ITEM] }],
      count: 1,
      offset: 0,
      limit: 100,
      catalog_available: true,
      error_detail: null,
    },
    isLoading: false,
  };
  render(
    <OrdersExportDialog
      open
      onClose={() => {}}
      orders={[ORDER]}
      listLimit={100}
      initialRange={NO_DATE_RANGE}
      activeDays={new Set()}
      today="2026-11-06"
    />,
  );
});

describe("Reporte de ventas: los productos a la vista", () => {
  it("«Por producto» va antes de los desgloses", () => {
    const products = screen.getByRole("region", { name: "Por producto" });
    const byStage = screen.getByRole("region", { name: "Por etapa" });

    expect(products.compareDocumentPosition(byStage) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("sin fuente de costo, la tabla en pantalla no muestra las columnas vacías", () => {
    const section = screen.getByRole("region", { name: "Por producto" });
    const headers = within(section).getAllByRole("columnheader").map((h) => h.textContent);

    expect(headers).toEqual([
      "Producto",
      "Producto en el catálogo (variante)",
      "Cantidad",
      "Precio unitario",
      "Venta total sin envío",
      "Cliente",
      "Número de pedido",
      "Canal de pago",
    ]);
    const row = within(section).getByRole("row", { name: /Vela Luna/ });
    expect(within(row).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "Vela Luna",
      "Lavanda",
      "2",
      "$ 16.000",
      "$ 32.000",
      "Ana",
      "#1",
      "Contra entrega",
    ]);
  });
});
