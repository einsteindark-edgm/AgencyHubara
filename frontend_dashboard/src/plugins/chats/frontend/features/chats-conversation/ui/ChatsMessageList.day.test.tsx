/**
 * El historial se pinta agrupado por día.
 *
 * No es cosmético: el separador de día es `position: sticky`, y varios sticky
 * hermanos dentro del MISMO contenedor se pegan todos al tope a la vez y se
 * solapan (visto en la verificación visual: "1 DE SI DOMINGO E 2026"). Cada día
 * necesita su propio bloque para que su separador se despegue cuando entra el
 * del día siguiente — el comportamiento de WhatsApp.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import type { ChatMessageItem } from "@plugins/chats/frontend/entities/chat";

// El composer real necesita TanStack Query + entities/handoff; acá sólo se
// verifica la estructura de la lista (mismo stub que `ChatsMessageList.test`).
vi.mock("./ChatsComposer", () => ({
  ChatsComposer: () => <div data-testid="composer-mock" />,
}));

import { ChatsMessageList } from "./ChatsMessageList";

const MESSAGES: ChatMessageItem[] = [
  { kind: "day", dayIso: "2026-09-08" },
  { kind: "in", text: "Hola", time: "21:00", dayIso: "2026-09-08" },
  { kind: "out", text: "¡Hola!", time: "21:01", dayIso: "2026-09-08" },
  { kind: "day", dayIso: "2026-09-09" },
  { kind: "in", text: "¿Precio?", time: "10:30", dayIso: "2026-09-09" },
];

// jsdom no implementa scrollIntoView y `useAutoScroll` lo llama al montar.
beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

function renderList(messages = MESSAGES) {
  const { container } = render(<ChatsMessageList messages={messages} chatId={null} />);
  return container;
}

describe("agrupación por día", () => {
  it("abre un bloque por cada día", () => {
    expect(renderList().querySelectorAll(".day-group")).toHaveLength(2);
  });

  it("cada bloque contiene su separador y SOLO los mensajes de ese día", () => {
    const groups = renderList().querySelectorAll(".day-group");
    expect(groups[0].querySelectorAll(".bubble")).toHaveLength(2);
    expect(groups[1].querySelectorAll(".bubble")).toHaveLength(1);
  });

  it("el separador es el primer hijo de su bloque (se pega arriba)", () => {
    const groups = renderList().querySelectorAll(".day-group");
    expect(groups[0].firstElementChild).toHaveClass("day");
  });

  it("los mensajes sin separador previo no se pierden", () => {
    // Historial legacy: burbujas sin timestamp, sin ningún `kind: "day"`.
    const container = renderList([
      { kind: "in", text: "Mensaje viejo sin fecha" },
      { kind: "out", text: "Respuesta vieja" },
    ]);
    expect(container.querySelectorAll(".bubble")).toHaveLength(2);
    expect(container.querySelectorAll(".day")).toHaveLength(0);
  });

  it("no renderiza un bloque vacío cuando no hay mensajes", () => {
    expect(renderList([]).querySelectorAll(".day-group")).toHaveLength(0);
  });
});
