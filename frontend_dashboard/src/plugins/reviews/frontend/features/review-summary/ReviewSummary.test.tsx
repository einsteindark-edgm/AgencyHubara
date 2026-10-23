/**
 * Encabezado de Reseñas: el promedio y cuántas hay, el reparto de estrellas,
 * cuántas invitaciones salieron (formulario dentro de la ventana vs
 * plantilla), los cupones dados y usados, y si el envío está prendido — todo
 * con el JSON real de `/api/reviews/summary`.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";

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

/** La tarjeta a la que pertenece un título. */
function cardOf(heading: HTMLElement): HTMLElement {
  const card = heading.parentElement;
  if (!card) throw new Error("título sin tarjeta");
  return card;
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

  it("los cupones son por calificar el pedido en WhatsApp, nunca por opinar en Google", async () => {
    renderSummary();

    const coupons = cardOf(await screen.findByRole("heading", { name: "Cupones de agradecimiento" }));
    expect(
      within(coupons).getByText("Por calificar el pedido en nuestro formulario de WhatsApp."),
    ).toBeInTheDocument();
    expect(within(coupons).queryByText(/Google/)).not.toBeInTheDocument();
    // El enlace a Google sigue, pero lejos del cupón.
    expect(screen.getByRole("link", { name: "Ver la página para opinar en Google" })).toHaveAttribute(
      "href",
      "https://hubara.com.co/opinar/",
    );
  });

  describe("por qué no salieron invitaciones", () => {
    it("sin el formulario de Meta, «Qué falta» lo dice", async () => {
      summaryResponse = {
        status: 200,
        body: { ...SUMMARY_FIXTURE, settings: { ...SUMMARY_FIXTURE.settings, flow_configured: false } },
      };
      renderSummary();

      const alert = await screen.findByRole("alert", { name: "Qué falta" });
      expect(
        within(alert).getByText("Falta el formulario de Meta (META_FLOW_ID_REVIEW): no se invita a nadie."),
      ).toBeInTheDocument();
      expect(within(alert).queryByText(/Faltan los cupones/)).not.toBeInTheDocument();
    });

    it("sin los cupones GRACIAS5 y GRACIAS10, «Qué falta» dice cómo crearlos", async () => {
      summaryResponse = {
        status: 200,
        body: { ...SUMMARY_FIXTURE, settings: { ...SUMMARY_FIXTURE.settings, coupons_ready: false } },
      };
      renderSummary();

      const alert = await screen.findByRole("alert", { name: "Qué falta" });
      expect(
        within(alert).getByText(
          "Faltan los cupones GRACIAS5 y GRACIAS10 en Marketing → Cupones, marcados «solo quien lo recibió» y vigentes 60 días más: no se invita a nadie hasta crearlos.",
        ),
      ).toBeInTheDocument();
    });

    it("con todo listo, o con el envío apagado, no hay «Qué falta»", async () => {
      summaryResponse = {
        status: 200,
        body: {
          ...SUMMARY_FIXTURE,
          settings: { ...SUMMARY_FIXTURE.settings, mode: "off", flow_configured: false, coupons_ready: false },
        },
      };
      renderSummary();

      await screen.findByText("4,6");
      expect(screen.queryByRole("alert", { name: "Qué falta" })).not.toBeInTheDocument();
      expect(screen.queryByText(/Falta el formulario de Meta/)).not.toBeInTheDocument();
    });

    it("«Sin invitar» cuenta cada motivo en palabras simples (y uno nuevo, con su nombre)", async () => {
      summaryResponse = {
        status: 200,
        body: {
          ...SUMMARY_FIXTURE,
          not_invited: {
            disabled: 3,
            not_in_test_list: 2,
            order_not_eligible: 1,
            opted_out: 1,
            expired: 4,
            flow_not_configured: 5,
            max_steps: 6,
            motivo_nuevo: 7,
          },
        },
      };
      renderSummary();

      const card = cardOf(await screen.findByRole("heading", { name: "Sin invitar" }));
      const value = (label: string) => within(card).getByText(label).nextElementSibling?.textContent;
      expect(value("El programa estaba apagado")).toBe("3");
      expect(value("No era un número de prueba")).toBe("2");
      expect(value("Pedido de prueba o ya no entregado")).toBe("1");
      expect(value("Se dio de baja de promociones")).toBe("1");
      expect(value("Pasaron 48 h sin poder escribirle")).toBe("4");
      expect(value("Faltaba el formulario de Meta")).toBe("5");
      expect(value("Se agotaron los intentos")).toBe("6");
      expect(value("motivo_nuevo")).toBe("7");
    });

    it("«En espera» dice qué espera cada pedido (y uno nuevo, con su nombre)", async () => {
      summaryResponse = {
        status: 200,
        body: {
          ...SUMMARY_FIXTURE,
          waiting: {
            coupon_not_configured: 1,
            quiet_hours: 2,
            waiting_for_window: 3,
            min_delay: 4,
            conversation_busy: 5,
            another_invitation_pending: 6,
            order_unknown: 7,
            espera_nueva: 8,
          },
        },
      };
      renderSummary();

      const card = cardOf(await screen.findByRole("heading", { name: "En espera" }));
      const value = (label: string) => within(card).getByText(label).nextElementSibling?.textContent;
      expect(value("Faltan los cupones")).toBe("1");
      expect(value("Horario de silencio")).toBe("2");
      expect(value("Esperando a que escriba (plantilla desde las 20 h)")).toBe("3");
      expect(value("Esperando 2 h tras la entrega")).toBe("4");
      expect(value("La conversación está ocupada")).toBe("5");
      expect(value("Ya tiene otra invitación sin contestar")).toBe("6");
      expect(value("Medusa no respondió")).toBe("7");
      expect(value("espera_nueva")).toBe("8");
    });

    it("sin motivos no muestra listas vacías", async () => {
      summaryResponse = { status: 200, body: { ...SUMMARY_FIXTURE, not_invited: {}, waiting: {} } };
      renderSummary();

      await screen.findByText("4,6");
      expect(screen.queryByRole("heading", { name: "Sin invitar" })).not.toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "En espera" })).not.toBeInTheDocument();
    });

    it("avisa de los envíos que se cortaron a mitad", async () => {
      summaryResponse = {
        status: 200,
        body: { ...SUMMARY_FIXTURE, requests: { ...SUMMARY_FIXTURE.requests, unconfirmed: 2 } },
      };
      renderSummary();

      expect(
        await screen.findByText("2 envíos sin confirmar (se cortaron a mitad; no se reintentan)"),
      ).toBeInTheDocument();
    });

    it("sin envíos cortados no dice nada", async () => {
      renderSummary();

      await screen.findByText("4,6");
      expect(screen.queryByText(/sin confirmar/)).not.toBeInTheDocument();
    });
  });

  describe("la tienda web", () => {
    it("sin el enlace para reconstruirla, lo dice", async () => {
      summaryResponse = {
        status: 200,
        body: { ...SUMMARY_FIXTURE, storefront: { ...SUMMARY_FIXTURE.storefront, configured: false } },
      };
      renderSummary();

      expect(
        await screen.findByText(
          "La tienda web no se reconstruye sola (falta STOREFRONT_DEPLOY_HOOK_URL)",
        ),
      ).toBeInTheDocument();
    });

    it("muestra el último error y a qué hora (Colombia) está la próxima actualización", async () => {
      summaryResponse = {
        status: 200,
        body: {
          ...SUMMARY_FIXTURE,
          storefront: {
            configured: true,
            last_ok_at_ms: null,
            last_error: "HTTP 500",
            // 2 oct 2026, 23:30 en Bogotá (3 oct 04:30 UTC).
            due_at_ms: 1791000000000 + 30 * 60_000,
          },
        },
      };
      renderSummary();

      expect(
        await screen.findByText("Último intento de actualizar la tienda falló: HTTP 500"),
      ).toBeInTheDocument();
      expect(screen.getByText("Actualización de la tienda programada a las 23:30")).toBeInTheDocument();
    });

    it("con la tienda al día no dice nada", async () => {
      renderSummary();

      await screen.findByText("4,6");
      expect(screen.queryByText(/tienda/)).not.toBeInTheDocument();
    });
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
