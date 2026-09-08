/**
 * Modal "En camino": al soltar un pedido en la columna `shipping` el operador
 * puede adjuntar el link de la guía (opcional). Comportamiento:
 *  - dialog accesible con el input vacío y foco en él;
 *  - "Marcar en camino" sin link → onConfirm(null) (el mensaje sale sin guía);
 *  - con link válido → onConfirm(url normalizada: trim + https:// si falta);
 *  - link inválido → error visible, NO confirma;
 *  - Cancelar / Escape / click en el backdrop → onCancel (el pedido no se mueve).
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { normalizeTrackingUrl } from "./model/trackingUrl";
import { TrackingLinkModal } from "./ui/TrackingLinkModal";

const URL = "https://www.servientrega.com/wps/portal/rastreo-envio?guia=1234567890";

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

function setup(busy = false) {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  render(
    <TrackingLinkModal orderId="#1247" busy={busy} onConfirm={onConfirm} onCancel={onCancel} />,
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
    expect(onConfirm).toHaveBeenCalledWith(null);
  });

  it("confirms with the normalized link", () => {
    const { onConfirm } = setup();
    fireEvent.change(screen.getByLabelText(/link de la guía/i), {
      target: { value: `  ${URL} ` },
    });
    fireEvent.click(screen.getByRole("button", { name: /con guía/i }));
    expect(onConfirm).toHaveBeenCalledWith(URL);
  });

  it("submits on Enter inside the input", () => {
    const { onConfirm } = setup();
    const input = screen.getByLabelText(/link de la guía/i);
    fireEvent.change(input, { target: { value: "coordinadora.com/rastreo?guia=1" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onConfirm).toHaveBeenCalledWith("https://coordinadora.com/rastreo?guia=1");
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
