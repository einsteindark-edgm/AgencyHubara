import { describe, it, expect } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useSessionFilters } from "./useSessionFilters";
import type { ChatSession } from "@/entities/session";

const session = (overrides: Partial<ChatSession>): ChatSession => ({
  session_id: "wa_1",
  phone_number: "5491111111111",
  tag: "INTERESADO",
  motivo: "Quiere precio",
  active_agent_route: "ventas",
  phone_number_id: null,
  pending_payment_order_id: null,
  last_updated_timestamp: 0,
  ...overrides,
});

describe("useSessionFilters", () => {
  it("excludes NO_ETIQUETADO from availableTags", () => {
    const { result } = renderHook(() =>
      useSessionFilters([
        session({ tag: "INTERESADO" }),
        session({ session_id: "wa_2", tag: "NO_ETIQUETADO" }),
        session({ session_id: "wa_3", tag: "RECHAZO" }),
      ]),
    );
    expect(result.current.availableTags).toEqual(["INTERESADO", "RECHAZO"]);
  });

  it("filters by phone number substring", () => {
    const { result } = renderHook(() =>
      useSessionFilters([
        session({ phone_number: "5491111111111" }),
        session({ session_id: "wa_2", phone_number: "5492222222222" }),
      ]),
    );
    act(() => result.current.setSearchTerm("2222"));
    expect(result.current.filteredSessions).toHaveLength(1);
    expect(result.current.filteredSessions[0].phone_number).toBe(
      "5492222222222",
    );
  });

  it("filters by motivo case-insensitive", () => {
    const { result } = renderHook(() =>
      useSessionFilters([
        session({ motivo: "Quiere PRECIO nuevo" }),
        session({ session_id: "wa_2", motivo: "Otra cosa" }),
      ]),
    );
    act(() => result.current.setSearchTerm("precio"));
    expect(result.current.filteredSessions).toHaveLength(1);
  });

  it("filters by active tag", () => {
    const { result } = renderHook(() =>
      useSessionFilters([
        session({ tag: "INTERESADO" }),
        session({ session_id: "wa_2", tag: "RECHAZO" }),
      ]),
    );
    act(() => result.current.setActiveTag("RECHAZO"));
    expect(result.current.filteredSessions).toHaveLength(1);
    expect(result.current.filteredSessions[0].tag).toBe("RECHAZO");
  });
});
