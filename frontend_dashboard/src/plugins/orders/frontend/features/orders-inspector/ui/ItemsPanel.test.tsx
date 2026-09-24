/**
 * Clase de match de variante en el inspector (duo-zodiacal, 2026-09-17).
 *
 *   - "partial": el signo SÍ se resolvió; sobran tokens (aroma/color). Se
 *     muestra la variante elegida y qué verificar, sin la alarma fuerte.
 *   - "fallback_first_variant" (o flag sin clase, legacy): warning fuerte.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import type {
  OrderDetail,
  OrderItemDetail,
} from "@plugins/orders/frontend/entities/order";
import { orderItemDetailSchema } from "@plugins/orders/frontend/entities/order/contracts";

import { ItemsPanel } from "./ItemsPanel";

const STRONG_WARNING = /NO matchea con ninguna variante/;

function item(overrides: Record<string, unknown>): OrderItemDetail {
  return orderItemDetailSchema.parse({
    title: "Duo Zodiacal",
    sku: "DZ",
    quantity: 1,
    unit_price_cop: 45000,
    total_cop: 45000,
    variant_label: null,
    thumbnail: null,
    handle: "duo-zodiacal",
    ...overrides,
  });
}

function renderItems(items: OrderItemDetail[]) {
  const detail = {
    items_detail: items,
    subtotal_cop: 45000,
    shipping_cop: 0,
    discount_total_cop: 0,
    tax_total_cop: 0,
    summary: { total_cop: 45000 },
  } as unknown as OrderDetail;
  return render(<ItemsPanel detail={detail} />);
}

describe("ItemsPanel — clase de match de variante", () => {
  it("partial muestra la variante elegida y los tokens a verificar, sin alarma", () => {
    renderItems([
      item({
        variant_label: "Aries, Limoncillo",
        variant_match_kind: "partial",
        selected_variant_title: "Aries",
        variant_unresolved_tokens: ["Limoncillo"],
        variant_unresolved_tag_kinds: ["aroma"],
      }),
    ]);

    expect(
      screen.getByText(
        "Variante elegida: Aries. Sin resolver: Limoncillo (verificar aroma)",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(STRONG_WARNING)).not.toBeInTheDocument();
  });

  it("fallback muestra el warning fuerte", () => {
    renderItems([
      item({
        variant_label: "Limoncillo",
        variant_label_mismatch: true,
        variant_match_kind: "fallback_first_variant",
        selected_variant_title: "Acuario",
      }),
    ]);

    expect(screen.getByText(STRONG_WARNING)).toBeInTheDocument();
  });

  it("payload viejo sin campos nuevos sigue parseando y muestra el warning", () => {
    renderItems([item({ variant_label: "X", variant_label_mismatch: true })]);

    expect(screen.getByText(STRONG_WARNING)).toBeInTheDocument();
  });

  it("match exacto no muestra ni warning ni nota", () => {
    renderItems([
      item({ variant_label: "Leo", selected_variant_title: "Leo" }),
    ]);

    expect(screen.queryByText(STRONG_WARNING)).not.toBeInTheDocument();
    expect(screen.queryByText(/Variante elegida/)).not.toBeInTheDocument();
  });
});

describe("ItemsPanel — cupón por línea (pedido #44)", () => {
  // La línea llega a Medusa con el precio YA descontado y `discount_total`
  // queda en 0: sin esta nota el operador ve $18.900 sin saber por qué.
  it("explica el precio con descuento: cupón, descuento por unidad y precio de lista", () => {
    renderItems([
      item({
        title: "Cubo Love",
        quantity: 2,
        unit_price_cop: 18900,
        total_cop: 37800,
        coupon_code: "AMOR26",
        list_unit_price_cop: 21000,
        discount_unit_cop: 2100,
      }),
    ]);

    const note = screen.getByText(/Cupón AMOR26/).textContent ?? "";
    expect(note).toMatch(/2[.,]100/);
    expect(note).toMatch(/21[.,]000/);
  });

  it("una línea sin cupón no muestra nota de cupón", () => {
    renderItems([item({})]);
    expect(screen.queryByText(/Cupón/)).not.toBeInTheDocument();
  });
});

describe("ItemsPanel — color y aroma de la línea (C-3)", () => {
  // El cupo por unidad cuenta por producto + color + aroma: el operador tiene
  // que ver qué combinación se registró para despachar la correcta.
  it("muestra el color y el aroma que se registraron", () => {
    renderItems([item({ title: "Cubo Love", color: "Rosado", aroma: "Café" })]);
    expect(screen.getByText("Color: Rosado · Aroma: Café")).toBeInTheDocument();
  });

  it("muestra solo el atributo que la línea tiene", () => {
    renderItems([item({ title: "Cubo Love", color: "Rosado", aroma: null })]);
    expect(screen.getByText("Color: Rosado")).toBeInTheDocument();
    expect(screen.queryByText(/Aroma:/)).not.toBeInTheDocument();
  });

  it("sin color ni aroma (o un backend viejo sin los campos) no muestra nada", () => {
    const [plain] = [item({})];
    expect(plain.color).toBeNull();
    expect(plain.aroma).toBeNull();
    renderItems([plain]);
    expect(screen.queryByText(/Color:|Aroma:/)).not.toBeInTheDocument();
  });
});

describe("ItemsPanel — desglose del cobro", () => {
  function renderTotals(summary: Record<string, unknown>) {
    const detail = {
      items_detail: [item({})],
      subtotal_cop: 50000,
      shipping_cop: 7900,
      discount_total_cop: 0,
      tax_total_cop: 0,
      summary: { total_cop: 57900, ...summary },
    } as unknown as OrderDetail;
    return render(<ItemsPanel detail={detail} />);
  }

  function row(label: RegExp): string {
    return screen.getByText(label).closest(".kv")?.textContent ?? "";
  }

  it("subtotal de productos, envío aparte (estimado) y total = subtotal + envío", () => {
    renderTotals({ shipping_confirmed: false });
    expect(row(/^Subtotal productos$/)).toMatch(/50[.,]000/);
    expect(row(/^Envío \(estimado\)$/)).toMatch(/7[.,]900/);
    expect(row(/^Total$/)).toMatch(/57[.,]900/);
  });

  it("con el envío real ya fijado deja de decir estimado", () => {
    renderTotals({ total_cop: 62000, shipping_confirmed: true });
    expect(screen.queryByText(/estimado/)).not.toBeInTheDocument();
    expect(row(/^Envío$/)).toMatch(/7[.,]900/);
  });
});
