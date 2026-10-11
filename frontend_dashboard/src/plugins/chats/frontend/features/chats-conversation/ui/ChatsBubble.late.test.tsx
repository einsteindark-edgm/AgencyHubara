/**
 * Mensaje del cliente que Meta entregó tarde (caso 2026-10-09): un «buenas
 * tardes» escrito 3 días antes llegó 19 s después de la plantilla del operador
 * y, con la hora de llegada, pareció su respuesta. La burbuja dice cuándo lo
 * escribió el cliente y, si llegó con la ventana cerrada, por qué el bot no
 * contestó.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { shiftIsoDay, todayBogotaIso } from "@/shared/lib";
import { ChatsBubble } from "./ChatsBubble";

const NOTE =
  "El bot no respondió: este mensaje llegó con la ventana de 24 h cerrada. Solo un mensaje nuevo del cliente la abre.";

describe("ChatsBubble — mensaje que llegó tarde", () => {
  it("dice cuándo lo escribió el cliente y cuándo llegó", () => {
    render(
      <ChatsBubble
        message={{ kind: "in", text: "Hola, ¿siguen teniendo velas?", time: "16:35", sentDayIso: "2026-08-21", sentTime: "12:02" }}
      />,
    );
    expect(screen.getByText("Escrito el 21 de agosto de 2026, 12:02 · llegó 16:35")).toBeInTheDocument();
  });

  it("ayer se dice «ayer»", () => {
    render(
      <ChatsBubble
        message={{
          kind: "in",
          text: "hola",
          time: "09:10",
          sentDayIso: shiftIsoDay(todayBogotaIso(), -1),
          sentTime: "22:40",
        }}
      />,
    );
    expect(screen.getByText("Escrito ayer, 22:40 · llegó 09:10")).toBeInTheDocument();
  });

  it("con la ventana cerrada explica por qué el bot no contestó", () => {
    render(
      <ChatsBubble
        message={{
          kind: "in",
          text: "Hola, ¿siguen teniendo velas?",
          time: "16:35",
          sentDayIso: "2026-08-21",
          sentTime: "12:02",
          arrivedAfterWindow: true,
        }}
      />,
    );
    expect(screen.getByText(NOTE)).toBeInTheDocument();
  });

  it("un mensaje que llegó a tiempo no lleva ni la hora escrita ni la nota", () => {
    render(<ChatsBubble message={{ kind: "in", text: "hola", time: "16:35" }} />);
    expect(screen.queryByText(/Escrito/)).toBeNull();
    expect(screen.queryByText(NOTE)).toBeNull();
    expect(screen.getByText("16:35")).toBeInTheDocument();
  });
});
