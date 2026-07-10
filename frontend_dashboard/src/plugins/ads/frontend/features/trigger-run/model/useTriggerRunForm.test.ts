import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { AgentOption } from "@plugins/ads/frontend/entities/ad-analysis-run";

import {
  formatExampleInput,
  parseDraft,
  useTriggerRunForm,
} from "./useTriggerRunForm";

const AGENTS: AgentOption[] = [
  { id: "greeter", label: "Greeter", description: null, exampleInput: { name: "ada" } },
  {
    id: "roas-cac",
    label: "ROAS / CAC",
    description: null,
    exampleInput: { route: "roas-cac" },
  },
];

describe("formatExampleInput", () => {
  it("pretty-printa el objeto con 2 espacios", () => {
    expect(formatExampleInput({ name: "ada" })).toBe('{\n  "name": "ada"\n}');
  });
  it("null/undefined ⇒ '{}'", () => {
    expect(formatExampleInput(undefined)).toBe("{}");
    expect(formatExampleInput(null)).toBe("{}");
  });
});

describe("parseDraft", () => {
  it("texto vacío ⇒ {} válido", () => {
    expect(parseDraft("   ")).toEqual({ ok: true, value: {} });
  });
  it("JSON válido ⇒ ok con el valor", () => {
    expect(parseDraft('{"a":1}')).toEqual({ ok: true, value: { a: 1 } });
  });
  it("JSON inválido ⇒ ok:false con error", () => {
    const r = parseDraft("{not json");
    expect(r.ok).toBe(false);
    expect(r.error).toBeTruthy();
  });
});

describe("useTriggerRunForm", () => {
  it("auto-selecciona el primer agente y precarga su ejemplo", () => {
    const { result } = renderHook(() => useTriggerRunForm(AGENTS));
    expect(result.current.agentId).toBe("greeter");
    expect(result.current.draft).toBe('{\n  "name": "ada"\n}');
    expect(result.current.canRun).toBe(true);
  });

  it("cambiar de agente re-precarga el ejemplo del nuevo (descarta el draft)", () => {
    const { result } = renderHook(() => useTriggerRunForm(AGENTS));
    act(() => result.current.selectAgent("roas-cac"));
    expect(result.current.agentId).toBe("roas-cac");
    expect(result.current.draft).toBe('{\n  "route": "roas-cac"\n}');
  });

  it("draft con JSON inválido ⇒ canRun=false", () => {
    const { result } = renderHook(() => useTriggerRunForm(AGENTS));
    act(() => result.current.setDraft("{roto"));
    expect(result.current.parsed.ok).toBe(false);
    expect(result.current.canRun).toBe(false);
  });

  it("resetToExample restaura el ejemplo del agente elegido", () => {
    const { result } = renderHook(() => useTriggerRunForm(AGENTS));
    act(() => result.current.setDraft('{"edited":true}'));
    act(() => result.current.resetToExample());
    expect(result.current.draft).toBe('{\n  "name": "ada"\n}');
  });

  it("sin agentes todavía ⇒ agentId null, canRun false", () => {
    const { result } = renderHook(() => useTriggerRunForm(undefined));
    expect(result.current.agentId).toBeNull();
    expect(result.current.canRun).toBe(false);
  });
});

/**
 * Feedback operador 2026-07-09: "el JSON de ejemplo no aporta nada, debería
 * enviarse la información que se trajo de Meta". Con Meta conectado, la entrada
 * REAL (del endpoint analysis-input) es la que se precarga y se envía — el
 * ejemplo queda solo como fallback sin conexión. Una edición manual nunca se pisa.
 */
describe("useTriggerRunForm — datos reales de Meta", () => {
  const LIVE = { meta_insights: { data: [{ spend: "120000" }] } };

  it("con datos reales disponibles ⇒ precarga los datos de Meta, no el ejemplo", () => {
    const { result } = renderHook(() => useTriggerRunForm(AGENTS, LIVE));
    expect(result.current.source).toBe("meta");
    expect(result.current.draft).toBe(formatExampleInput(LIVE));
    expect(result.current.parsed.value).toEqual(LIVE);
  });

  it("los datos reales llegan DESPUÉS ⇒ reemplazan el ejemplo precargado", () => {
    const { result, rerender } = renderHook(
      ({ live }: { live?: unknown }) => useTriggerRunForm(AGENTS, live),
      { initialProps: { live: undefined as unknown } },
    );
    expect(result.current.source).toBe("example");
    rerender({ live: LIVE });
    expect(result.current.source).toBe("meta");
    expect(result.current.draft).toBe(formatExampleInput(LIVE));
  });

  it("una edición manual NO se pisa cuando llegan los datos reales", () => {
    const { result, rerender } = renderHook(
      ({ live }: { live?: unknown }) => useTriggerRunForm(AGENTS, live),
      { initialProps: { live: undefined as unknown } },
    );
    act(() => result.current.setDraft('{"mio":true}'));
    rerender({ live: LIVE });
    expect(result.current.source).toBe("edited");
    expect(result.current.draft).toBe('{"mio":true}');
  });

  it("resetToLive vuelve a los datos reales tras una edición", () => {
    const { result } = renderHook(() => useTriggerRunForm(AGENTS, LIVE));
    act(() => result.current.setDraft('{"mio":true}'));
    act(() => result.current.resetToLive());
    expect(result.current.source).toBe("meta");
    expect(result.current.draft).toBe(formatExampleInput(LIVE));
  });
});
