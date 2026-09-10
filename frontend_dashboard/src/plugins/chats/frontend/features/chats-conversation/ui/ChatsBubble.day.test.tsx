/**
 * El separador de día se renderiza desde `dayIso` (dato), no desde un texto ya
 * congelado en el adaptador — así "Hoy" sigue siendo correcto aunque la lista
 * lleve horas en la cache de TanStack Query.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { todayBogotaIso, shiftIsoDay } from "@/shared/lib";
import { ChatsBubble } from "./ChatsBubble";

describe("separador de día", () => {
  it("el día de hoy en Colombia se rotula 'Hoy'", () => {
    render(<ChatsBubble message={{ kind: "day", dayIso: todayBogotaIso() }} />);
    expect(screen.getByText("Hoy")).toBeInTheDocument();
  });

  it("el día anterior se rotula 'Ayer'", () => {
    render(
      <ChatsBubble message={{ kind: "day", dayIso: shiftIsoDay(todayBogotaIso(), -1) }} />,
    );
    expect(screen.getByText("Ayer")).toBeInTheDocument();
  });

  it("un día viejo se rotula con la fecha larga en español", () => {
    render(<ChatsBubble message={{ kind: "day", dayIso: "2026-08-21" }} />);
    expect(screen.getByText("21 de agosto de 2026")).toBeInTheDocument();
  });

  it("sin dayIso cae al texto crudo (historial legacy sin timestamps)", () => {
    render(<ChatsBubble message={{ kind: "day", text: "Conversación" }} />);
    expect(screen.getByText("Conversación")).toBeInTheDocument();
  });
});
