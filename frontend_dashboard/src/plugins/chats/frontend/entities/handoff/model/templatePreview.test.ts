import { describe, it, expect } from "vitest";
import {
  buildTemplatePreview,
  isTemplateReady,
  sanitizeTemplateParam,
} from "./templatePreview";
import type { WhatsAppTemplate } from "../contracts";

const followup: WhatsAppTemplate = {
  name: "human_followup_utility_v1",
  category: "utility",
  semantics: "Seguimiento del operador humano",
  body: "Hola, te escribe Liliana, asesora de Hubara, para hacer seguimiento a tu consulta {{1}}. Quedo atenta a tu respuesta.",
  variables: [
    { name: "followup_message", description: "Mensaje del operador", max_length: 400 },
  ],
  is_default: true,
};

const payment: WhatsAppTemplate = {
  name: "payment_pending_utility_v2",
  category: "utility",
  semantics: "Recordar pago pendiente",
  body: "Hola, tu pago de la orden {{1}} por {{2}} está pendiente.",
  variables: [
    { name: "order_reference", description: "ID de la orden", max_length: 60 },
    { name: "amount_currency", description: "Monto con moneda", max_length: 40 },
  ],
  is_default: false,
};

describe("buildTemplatePreview", () => {
  it("shows '{ tu texto aquí }' in the single slot while it is empty", () => {
    const segments = buildTemplatePreview(followup, {});
    expect(segments).toEqual([
      { kind: "text", text: "Hola, te escribe Liliana, asesora de Hubara, para hacer seguimiento a tu consulta " },
      { kind: "slot", name: "followup_message", text: "{ tu texto aquí }", filled: false },
      { kind: "text", text: ". Quedo atenta a tu respuesta." },
    ]);
  });

  it("puts what the operator typed inside the slot", () => {
    const segments = buildTemplatePreview(followup, {
      followup_message: "Ya tenemos las fotos de tu vela.",
    });
    expect(segments[1]).toEqual({
      kind: "slot",
      name: "followup_message",
      text: "Ya tenemos las fotos de tu vela.",
      filled: true,
    });
  });

  it("names each empty slot by its description when there are several", () => {
    const segments = buildTemplatePreview(payment, { order_reference: "#1042" });
    expect(segments.filter((s) => s.kind === "slot")).toEqual([
      { kind: "slot", name: "order_reference", text: "#1042", filled: true },
      { kind: "slot", name: "amount_currency", text: "{ Monto con moneda }", filled: false },
    ]);
  });
});

describe("isTemplateReady", () => {
  it("requires every variable with non-blank text", () => {
    expect(isTemplateReady(payment, { order_reference: "#1", amount_currency: " " })).toBe(false);
    expect(isTemplateReady(payment, { order_reference: "#1", amount_currency: "$1" })).toBe(true);
  });
});

describe("sanitizeTemplateParam", () => {
  it("turns line breaks and tabs into spaces and caps long runs of spaces (Meta rules)", () => {
    expect(sanitizeTemplateParam("hola\nmundo\tya      listo")).toBe("hola mundo ya listo");
  });
});
