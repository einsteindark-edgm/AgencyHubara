/**
 * Encabezado de Reseñas: el promedio y cuántas hay, el reparto de estrellas,
 * cuántas invitaciones salieron (formulario dentro de la ventana vs
 * plantilla), los cupones dados y usados, y si el envío está prendido — todo
 * con el JSON real de `/api/reviews/summary`.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";

import { SUMMARY_FIXTURE } from "@plugins/reviews/frontend/entities/review/fixtures";

import { ReviewSummary } from "./ui/ReviewSummary";

const fetchMock = vi.fn();
let summaryResponse: { status: number; body: unknown } = { status: 200, body: SUMMARY_FIXTURE };

function json(body: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }),
  );
}

beforeEach(() => {
  summaryResponse = { status: 200, body: SUMMARY_FIXTURE };
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation((url: string) =>
    String(url).endsWith("/api/reviews/summary")
      ? json(summaryResponse.body, summaryResponse.status)
      : json({}, 404),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

function renderSummary() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ReviewSummary />
    </QueryClientProvider>,
  );
}

/** El número que acompaña a una etiqueta (`<dt>` → `<dd>`). */
function valueOf(label: string): string | null | undefined {
  return screen.getByText(label).nextElementSibling?.textContent;
}

describe("ReviewSummary", () => {
  it("muestra el promedio, cuántas reseñas hay y las estrellas", async () => {
    renderSummary();

    expect(await screen.findByText("4,6")).toBeInTheDocument();
    expect(screen.getByText("20 reseñas")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Promedio: 4,6 de 5 estrellas" })).toBeInTheDocument();
    expect(screen.getByText("6 con foto · 2 con 1 a 3 estrellas")).toBeInTheDocument();
  });

  it("reparte las reseñas por estrellas, de 5 a 1", async () => {
    renderSummary();

    const rows = await screen.findAllByRole("listitem", { name: /estrellas?:/ });
    expect(rows.map((r) => r.getAttribute("aria-label"))).toEqual([
      "5 estrellas: 15",
      "4 estrellas: 3",
      "3 estrellas: 1",
      "2 estrellas: 1",
      "1 estrella: 0",
    ]);
  });

  it("cuenta las invitaciones: formulario dentro de la ventana vs plantilla", async () => {
    renderSummary();

    await screen.findByText("4,6");
    expect(valueOf("Invitaciones enviadas")).toBe("42");
    expect(valueOf("Con el formulario (escribió en las últimas 24 h)")).toBe("12");
    expect(valueOf("Con la plantilla (al día siguiente)")).toBe("30");
    expect(valueOf("No se pudieron enviar")).toBe("1");
    expect(valueOf("No se enviaron (no aplicaba)")).toBe("4");
  });

  it("cuenta los cupones de agradecimiento dados y usados", async () => {
    renderSummary();

    await screen.findByText("4,6");
    expect(valueOf("Cupones dados")).toBe("20");
    expect(valueOf("Cupones usados")).toBe("5");
  });

  it.each([
    ["off", "Apagado: no se están enviando invitaciones (se enciende por Terraform)"],
    ["test", "Solo números de prueba"],
    ["on", "Activo"],
  ])("dice en qué modo está el envío (%s)", async (mode, text) => {
    summaryResponse = {
      status: 200,
      body: { ...SUMMARY_FIXTURE, settings: { ...SUMMARY_FIXTURE.settings, mode } },
    };
    renderSummary();

    expect(await screen.findByText(text)).toBeInTheDocument();
  });

  it("sin reseñas todavía no inventa un promedio", async () => {
    summaryResponse = {
      status: 200,
      body: {
        ...SUMMARY_FIXTURE,
        reviews: { total: 0, average_rating: null, distribution: {}, with_photo: 0, low_rating: 0 },
      },
    };
    renderSummary();

    expect(await screen.findByText("Todavía no hay calificaciones")).toBeInTheDocument();
    expect(screen.queryByText("0,0")).not.toBeInTheDocument();
  });

  it("si el resumen no carga lo dice y deja reintentar", async () => {
    summaryResponse = { status: 500, body: { detail: "Se cayó la lectura del resumen." } };
    renderSummary();

    expect(await screen.findByText("No se pudo cargar el resumen.")).toBeInTheDocument();
    expect(screen.getByText("Se cayó la lectura del resumen.")).toBeInTheDocument();

    summaryResponse = { status: 200, body: SUMMARY_FIXTURE };
    fireEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByText("4,6")).toBeInTheDocument();
  });
});
