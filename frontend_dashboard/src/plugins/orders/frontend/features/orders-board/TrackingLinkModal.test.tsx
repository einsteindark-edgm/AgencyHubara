/**
 * Modal "En camino": al soltar un pedido en la columna `shipping` el operador
 * puede adjuntar el link de la guía y el valor del envío (ambos opcionales).
 * Comportamiento:
 *  - dialog accesible con los inputs vacíos y foco en el link;
 *  - "Marcar en camino" sin link → onConfirm({trackingUrl: null, ...});
 *  - con link válido → trackingUrl normalizada (trim + https:// si falta);
 *  - link inválido → error visible, NO confirma;
 *  - valor del envío "12.000" / "$ 12000" → shippingCost 12000 (COP entero);
 *    vacío → null; inválido → error visible, NO confirma;
 *  - Cancelar / Escape / click en el backdrop → onCancel (el pedido no se mueve).
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { normalizeTrackingUrl, parseShippingCost } from "@/shared/lib";
import { TrackingLinkModal } from "./ui/TrackingLinkModal";

const URL = "https://www.servientrega.com/wps/portal/rastreo-envio?guia=1234567890";

describe("parseShippingCost", () => {
  it("accepts plain, dotted and $-prefixed COP amounts", () => {
    expect(parseShippingCost("12000")).toEqual({ value: 12000 });
    expect(parseShippingCost(" 12.000 ")).toEqual({ value: 12000 });
    expect(parseShippingCost("$ 12.000")).toEqual({ value: 12000 });
    expect(parseShippingCost("1.250.000")).toEqual({ value: 1250000 });
  });
  it("treats empty (or zero) as no value", () => {
    expect(parseShippingCost("")).toEqual({ value: null });
    expect(parseShippingCost("   ")).toEqual({ value: null });
    expect(parseShippingCost("0")).toEqual({ value: null });
  });
  it("rejects decimals, negatives, text and absurd amounts", () => {
    expect(parseShippingCost("12,5")).toEqual({ error: expect.any(String) });
    expect(parseShippingCost("-5000")).toEqual({ error: expect.any(String) });
    expect(parseShippingCost("doce mil")).toEqual({ error: expect.any(String) });
    expect(parseShippingCost("99999999")).toEqual({ error: expect.any(String) });
  });
});

describe("normalizeTrackingUrl", () => {
  it("trims and keeps http(s) urls verbatim", () => {
    expect(normalizeTrackingUrl(`  ${URL} `)).toBe(URL);
    expect(normalizeTrackingUrl("http://rastreo.envia.co/g/1")).toBe(
      "http://rastreo.envia.co/g/1",
    );
  });
  it("prefixes https:// when the operator pastes a bare domain", () => {
    expect(normalizeTrackingUrl("coordinadora.com/rastreo?guia=1")).toBe(
      "https://coordinadora.com/rastreo?guia=1",
    );
  });
  it("returns null for empty or unusable input", () => {
    expect(normalizeTrackingUrl("")).toBeNull();
    expect(normalizeTrackingUrl("   ")).toBeNull();
    expect(normalizeTrackingUrl("javascript:alert(1)")).toBeNull();
    expect(normalizeTrackingUrl("ftp://x.com/a")).toBeNull();
    expect(normalizeTrackingUrl("https://x.com/a b")).toBeNull();
    expect(normalizeTrackingUrl("solo texto")).toBeNull();
  });
});

function setup(busy = false, orderTotal: number | null = 50000) {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  render(
    <TrackingLinkModal
      orderId="#1247"
      orderTotal={orderTotal}
      busy={busy}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />,
  );
  return { onConfirm, onCancel };
}

describe("TrackingLinkModal", () => {
  it("renders an accessible dialog for the order with the link input focused", () => {
    setup();
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("#1247");
    const input = screen.getByLabelText(/link de la guía/i) as HTMLInputElement;
    expect(input.value).toBe("");
    expect(document.activeElement).toBe(input);
  });

  it("confirms with null when the operator sends without a link", () => {
    const { onConfirm } = setup();
    fireEvent.click(screen.getByRole("button", { name: /sin guía/i }));
    expect(onConfirm).toHaveBeenCalledWith({ trackingUrl: null, shippingCost: null });
  });

  it("confirms with the normalized link", () => {
    const { onConfirm } = setup();
    fireEvent.change(screen.getByLabelText(/link de la guía/i), {
      target: { value: `  ${URL} ` },
    });
    fireEvent.click(screen.getByRole("button", { name: /con guía/i }));
    expect(onConfirm).toHaveBeenCalledWith({ trackingUrl: URL, shippingCost: null });
  });

  it("submits on Enter inside the input", () => {
    const { onConfirm } = setup();
    const input = screen.getByLabelText(/link de la guía/i);
    fireEvent.change(input, { target: { value: "coordinadora.com/rastreo?guia=1" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onConfirm).toHaveBeenCalledWith({
      trackingUrl: "https://coordinadora.com/rastreo?guia=1",
      shippingCost: null,
    });
  });

  it("sends the shipping cost as an integer COP amount, with or without a link", () => {
    const { onConfirm } = setup();
    fireEvent.change(screen.getByLabelText(/valor del envío/i), {
      target: { value: "12.000" },
    });
    fireEvent.click(screen.getByRole("button", { name: /sin guía/i }));
    expect(onConfirm).toHaveBeenLastCalledWith({ trackingUrl: null, shippingCost: 12000 });

    fireEvent.change(screen.getByLabelText(/link de la guía/i), { target: { value: URL } });
    fireEvent.click(screen.getByRole("button", { name: /con guía/i }));
    expect(onConfirm).toHaveBeenLastCalledWith({ trackingUrl: URL, shippingCost: 12000 });
  });

  it("shows the order value pre-set, and the total updates with the shipping cost", () => {
    setup();
    expect(screen.getByTestId("ship-order-value")).toHaveTextContent("$ 50.000");
    expect(screen.getByTestId("ship-total")).toHaveTextContent("$ 50.000");
    fireEvent.change(screen.getByLabelText(/valor del envío/i), {
      target: { value: "12.000" },
    });
    expect(screen.getByTestId("ship-total")).toHaveTextContent("$ 62.000");
  });

  it("does not add an invalid shipping cost to the total", () => {
    setup();
    fireEvent.change(screen.getByLabelText(/valor del envío/i), {
      target: { value: "doce" },
    });
    expect(screen.getByTestId("ship-total")).toHaveTextContent("$ 50.000");
  });

  it("without a known order value it shows only the shipping cost", () => {
    setup(false, null);
    expect(screen.queryByTestId("ship-order-value")).toBeNull();
    expect(screen.queryByTestId("ship-total")).toBeNull();
  });

  it("shows an error and does not confirm on an invalid shipping cost", () => {
    const { onConfirm } = setup();
    fireEvent.change(screen.getByLabelText(/valor del envío/i), {
      target: { value: "doce mil" },
    });
    fireEvent.click(screen.getByRole("button", { name: /sin guía/i }));
    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/envío/i);
  });

  it("shows an error and does not confirm on an invalid link", () => {
    const { onConfirm } = setup();
    fireEvent.change(screen.getByLabelText(/link de la guía/i), {
      target: { value: "no es un link" },
    });
    fireEvent.click(screen.getByRole("button", { name: /con guía/i }));
    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/link/i);
  });

  it("cancels via button, Escape and backdrop click", () => {
    const { onCancel, onConfirm } = setup();
    fireEvent.click(screen.getByRole("button", { name: /cancelar/i }));
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.click(screen.getByTestId("tracking-link-backdrop"));
    expect(onCancel).toHaveBeenCalledTimes(3);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("disables the actions while the transition is in flight", () => {
    setup(true);
    expect(screen.getByRole("button", { name: /con guía/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /sin guía/i })).toBeDisabled();
  });
});
