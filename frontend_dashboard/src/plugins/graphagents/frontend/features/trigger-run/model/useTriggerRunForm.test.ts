import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { AgentOption } from "@plugins/graphagents/frontend/entities/graphagents-run";

import {
  formatExampleInput,
  parseDraft,
  useTriggerRunForm,
} from "./useTriggerRunForm";

const AGENTS: AgentOption[] = [
  { id: "greeter", label: "Greeter", exampleInput: { name: "ada" } },
  { id: "roas-cac", label: "ROAS / CAC", exampleInput: { route: "roas-cac" } },
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
