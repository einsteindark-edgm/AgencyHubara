/**
 * «Ajustar precio» en el inspector (pedido #169, 2026-11-09): una venta al
 * por mayor con precio negociado quedó a precio de lista. El operador escribe
 * el precio real de cada línea; Medusa queda con ese precio y todo Hubara
 * (Órdenes, Exportar, Ads) lee el nuevo total.
 *
 *   - Solo manda las líneas que cambian, con el motivo (obligatorio).
 *   - Muestra el nuevo total antes de guardar.
 *   - En un pedido cobrado avisa que la diferencia se registra como
 *     reembolso en Medusa (corrige el cobro, no mueve dinero).
 *   - La línea ajustada explica su precio (motivo + precio de lista).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import type { Order, OrderDetail } from "@plugins/orders/frontend/entities/order";
import { orderItemDetailSchema } from "@plugins/orders/frontend/entities/order/contracts";

const adjustMutate = vi.fn();

vi.mock("@plugins/orders/frontend/entities/order", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useAdjustOrderPrices: () => ({ mutate: adjustMutate, isPending: false }),
}));

import { ItemsPanel } from "./ItemsPanel";

function line(overrides: Record<string, unknown>) {
  return orderItemDetailSchema.parse({
    title: "Caja de Velas de deseos onduladas",
    sku: "VDO",
    quantity: 10,
    unit_price_cop: 22500,
    total_cop: 225000,
    variant_label: null,
    thumbnail: null,
    handle: "velas-de-deseos-onduladas",
    item_id: "ordli_ond",
    ...overrides,
  });
}

const LINES = [
  line({}),
  line({
    title: "Caja de Velas de deseos cuadradas",
    unit_price_cop: 25000,
    total_cop: 250000,
    item_id: "ordli_cua",
  }),
];

function detail(items = LINES, total = 475000): OrderDetail {
  return {
    items_detail: items,
    subtotal_cop: total,
    shipping_cop: 0,
    discount_total_cop: 0,
    tax_total_cop: 0,
    summary: { total_cop: total, free_shipping: null, store_shipping_cost_cop: null },
  } as unknown as OrderDetail;
}

function order(overrides: Partial<Order> = {}): Order {
  return {
    id: "#169",
    customer: "Cliente",
    short: "CL",
    color: "a",
    phone: null,
    city: "Bogotá",
    channel: "WhatsApp",
    status: "delivered",
    payStatus: "pending",
    payType: "cod",
    items: 2,
    total: 475000,
    dueIso: "",
    dueTime: "—",
    pieces: 20,
    agent: "—",
    priority: "alta",
    isDraft: false,
    isDueEstimated: false,
    createdIso: "",
    ...overrides,
  } as Order;
}

function openEditor(o: Order = order(), d: OrderDetail = detail()) {
  render(<ItemsPanel detail={d} order={o} />);
  fireEvent.click(screen.getByRole("button", { name: /Ajustar precio/ }));
}

beforeEach(() => {
  adjustMutate.mockReset();
});

describe("Ajustar precio", () => {
  it("manda solo las líneas que cambian, con el motivo", () => {
    openEditor();

    fireEvent.change(screen.getByLabelText("Precio por unidad de Caja de Velas de deseos onduladas"), {
      target: { value: "18.000" },
    });
    fireEvent.change(screen.getByLabelText("Motivo del ajuste"), {
      target: { value: "Precio por mayor negociado" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Guardar precios" }));

    expect(adjustMutate).toHaveBeenCalledTimes(1);
    expect(adjustMutate.mock.calls[0][0]).toEqual({
      orderId: "#169",
      prices: [{ item_id: "ordli_ond", unit_price_cop: 18000 }],
      reason: "Precio por mayor negociado",
    });
  });

  it("muestra el nuevo total antes de guardar", () => {
    openEditor();

    fireEvent.change(screen.getByLabelText("Precio por unidad de Caja de Velas de deseos onduladas"), {
      target: { value: "18000" },
    });
    fireEvent.change(screen.getByLabelText("Precio por unidad de Caja de Velas de deseos cuadradas"), {
      target: { value: "20000" },
    });

    expect(screen.getByText("Nuevo total: $ 380.000")).toBeTruthy();
  });

  it("sin motivo o sin cambios no deja guardar", () => {
    openEditor();
    const save = screen.getByRole("button", { name: "Guardar precios" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);

    fireEvent.change(screen.getByLabelText("Precio por unidad de Caja de Velas de deseos onduladas"), {
      target: { value: "18000" },
    });
    expect(save.disabled).toBe(true);

    fireEvent.change(screen.getByLabelText("Motivo del ajuste"), { target: { value: "Por mayor" } });
    expect(save.disabled).toBe(false);
  });

  it("en un pedido cobrado avisa que la diferencia se registra como reembolso en Medusa", () => {
    openEditor(order({ payStatus: "paid" }));
    expect(screen.getByText(/se registra en Medusa como un reembolso/)).toBeTruthy();
  });

  it("en un pedido cobrado no deja subir el precio (primero se reversa el pago)", () => {
    openEditor(order({ payStatus: "paid" }));
    fireEvent.change(screen.getByLabelText("Precio por unidad de Caja de Velas de deseos onduladas"), {
      target: { value: "30000" },
    });
    fireEvent.change(screen.getByLabelText("Motivo del ajuste"), { target: { value: "Por mayor" } });

    expect(screen.getByText(/primero reversa el pago/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Guardar precios" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("muestra el error del backend", () => {
    adjustMutate.mockImplementation((_vars, opts) =>
      opts.onSuccess({ success: false, order_id: "#169", current_stage: null, error_detail: "validation_error: no", audit_id: null }),
    );
    openEditor();
    fireEvent.change(screen.getByLabelText("Precio por unidad de Caja de Velas de deseos onduladas"), {
      target: { value: "18000" },
    });
    fireEvent.change(screen.getByLabelText("Motivo del ajuste"), { target: { value: "Por mayor" } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar precios" }));

    expect(screen.getByText("validation_error: no")).toBeTruthy();
  });

  it("un pedido cancelado no ofrece ajustar", () => {
    render(<ItemsPanel detail={detail()} order={order({ status: "cancelled" })} />);
    expect(screen.queryByRole("button", { name: /Ajustar precio/ })).toBeNull();
  });

  it("la línea ajustada explica su precio", () => {
    render(
      <ItemsPanel
        detail={detail([
          line({
            unit_price_cop: 18000,
            total_cop: 180000,
            list_unit_price_cop: 22500,
            price_adjusted_reason: "Precio por mayor negociado",
          }),
        ], 180000)}
        order={order()}
      />,
    );
    expect(
      screen.getByText("Precio ajustado: Precio por mayor negociado (precio de lista $ 22.500)"),
    ).toBeTruthy();
  });
});
