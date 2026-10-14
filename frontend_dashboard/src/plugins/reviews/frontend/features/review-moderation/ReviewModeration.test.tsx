/**
 * Moderación de reseñas: tres bandejas (por revisar / publicadas / ocultas)
 * con sus conteos, y cada tarjeta con lo que el equipo necesita para decidir
 * — estrellas, pedido, productos, comentario, fotos, quién y cuándo, el cupón
 * que recibió y los avisos — más Publicar, Ocultar y Responder. Todo con el
 * JSON real de `/api/reviews/*` (solo se stubea `fetch`).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import {
  BARE_REVIEW_FIXTURE,
  REVIEW_FIXTURE,
} from "@plugins/reviews/frontend/entities/review/fixtures";

import { ReviewModeration } from "./ui/ReviewModeration";

type Status = "pending" | "approved" | "hidden";

const COUNTS = { pending: 3, approved: 10, hidden: 1 };
const fetchMock = vi.fn();
let lists: Record<Status, unknown[]>;
let listStatus = 200;
let actionResponse: { status: number; body: unknown };

function json(body: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }),
  );
}

function posts(): Array<{ url: string; body: unknown }> {
  return fetchMock.mock.calls
    .filter(([, init]) => (init as RequestInit | undefined)?.method === "POST")
    .map(([url, init]) => ({
      url: String(url),
      body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
    }));
}

function requestedStatuses(): string[] {
  return fetchMock.mock.calls
    .map(([url]) => new URL(String(url)).searchParams.get("status"))
    .filter((s): s is string => s !== null);
}

beforeEach(() => {
  // Solo el reloj (no los timers): «hoy» es 13 oct 2026 en Bogotá.
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-13T15:00:00Z"));
  lists = { pending: [REVIEW_FIXTURE], approved: [], hidden: [] };
  listStatus = 200;
  actionResponse = { status: 200, body: { review: { ...REVIEW_FIXTURE, status: "approved" } } };
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation((url: string, init?: RequestInit) => {
    const u = new URL(String(url));
    if (init?.method === "POST") return json(actionResponse.body, actionResponse.status);
    if (u.pathname === "/api/reviews/reviews") {
      const status = (u.searchParams.get("status") ?? "pending") as Status;
      return listStatus === 200
        ? json({ reviews: lists[status], counts: COUNTS })
        : json({ detail: "La bandeja no respondió." }, listStatus);
    }
    return json({}, 404);
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

function renderModeration() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <ReviewModeration />
    </QueryClientProvider>,
  );
}

async function findCard(displayId = "#31") {
  return screen.findByRole("article", { name: `Reseña del pedido ${displayId}` });
}

describe("ReviewModeration — bandejas", () => {
  it("abre en «Por revisar» y muestra los conteos de las tres bandejas", async () => {
    renderModeration();

    const pending = await screen.findByRole("tab", { name: "Por revisar (3)" });
    expect(pending).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Publicadas (10)" })).toHaveAttribute("aria-selected", "false");
    expect(screen.getByRole("tab", { name: "Ocultas (1)" })).toBeInTheDocument();
    expect(requestedStatuses()).toEqual(["pending"]);
  });

  it("cambiar de bandeja pide esa bandeja y deja los botones que tienen sentido ahí", async () => {
    lists.approved = [{ ...REVIEW_FIXTURE, status: "approved" }];
    renderModeration();
    await findCard();

    fireEvent.click(screen.getByRole("tab", { name: "Publicadas (10)" }));

    await waitFor(() => expect(requestedStatuses()).toContain("approved"));
    const card = await findCard();
    expect(within(card).getByRole("button", { name: "Ocultar" })).toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: "Publicar" })).not.toBeInTheDocument();
  });

  it("en «Ocultas» se puede volver a publicar, no ocultar otra vez", async () => {
    lists.hidden = [{ ...REVIEW_FIXTURE, status: "hidden" }];
    renderModeration();
    await findCard();

    fireEvent.click(screen.getByRole("tab", { name: "Ocultas (1)" }));

    await waitFor(() => expect(requestedStatuses()).toContain("hidden"));
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Ocultar" })).not.toBeInTheDocument(),
    );
    expect(within(await findCard()).getByRole("button", { name: "Publicar" })).toBeInTheDocument();
  });

  it.each([
    ["pending", "Por revisar (3)", "No hay reseñas por revisar. Cuando un cliente califique su pedido, aparece aquí."],
    ["approved", "Publicadas (10)", "Todavía no hay reseñas publicadas."],
    ["hidden", "Ocultas (1)", "No hay reseñas ocultas."],
  ] as const)("bandeja vacía (%s) lo dice en palabras simples", async (status, tab, text) => {
    lists = { pending: [], approved: [], hidden: [] };
    renderModeration();
    await screen.findByRole("tab", { name: "Por revisar (3)" });

    if (status !== "pending") fireEvent.click(screen.getByRole("tab", { name: tab }));

    expect(await screen.findByText(text)).toBeInTheDocument();
  });

  it("si la bandeja no carga lo dice y deja reintentar", async () => {
    listStatus = 500;
    renderModeration();

    expect(await screen.findByText("No se pudieron cargar las reseñas.")).toBeInTheDocument();
    expect(screen.getByText("La bandeja no respondió.")).toBeInTheDocument();

    listStatus = 200;
    fireEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await findCard()).toBeInTheDocument();
  });
});

describe("ReviewModeration — la tarjeta", () => {
  it("muestra estrellas, pedido, productos, comentario, autor, fecha y cupón", async () => {
    renderModeration();
    const card = await findCard();

    expect(within(card).getByRole("img", { name: "5 de 5 estrellas" })).toBeInTheDocument();
    expect(within(card).getByText("Pedido #31")).toBeInTheDocument();
    expect(within(card).getByText("Velón Encanto Silvestre")).toBeInTheDocument();
    expect(within(card).getByText("Hermosa")).toBeInTheDocument();
    expect(within(card).getByText("Ana G. · Bogotá")).toBeInTheDocument();
    expect(within(card).getByText("2 oct, 23:00")).toBeInTheDocument();
    expect(within(card).getByText("GRACIAS10 · 10 % · vence 1 dic")).toBeInTheDocument();
  });

  it("muestra las fotos en miniatura desde nuestro API", async () => {
    renderModeration();
    const card = await findCard();

    const img = within(card).getByRole("img", { name: "Foto 1 de la reseña del pedido #31" });
    expect(img).toHaveAttribute("src", "http://localhost:8000/api/reviews/photos/order_01J_0.jpg");
  });

  it("avisa si la conversación pasó al equipo y si no hay permiso para publicar", async () => {
    lists.pending = [BARE_REVIEW_FIXTURE];
    renderModeration();
    const card = await findCard("#32");

    expect(within(card).getByText("Se avisó al equipo")).toBeInTheDocument();
    expect(
      within(card).getByText("Sin permiso para publicar: solo cuenta en el promedio"),
    ).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Publicar" })).toBeDisabled();
    // Sin comentario, ciudad ni cupón: no inventa nada.
    expect(within(card).getByText("Luis P.")).toBeInTheDocument();
    expect(within(card).getByText("Sin comentario")).toBeInTheDocument();
    expect(within(card).queryByText(/vence/)).not.toBeInTheDocument();
  });

  it("una reseña con permiso no lleva esos avisos", async () => {
    renderModeration();
    const card = await findCard();

    expect(within(card).queryByText("Se avisó al equipo")).not.toBeInTheDocument();
    expect(within(card).queryByText(/Sin permiso para publicar/)).not.toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Publicar" })).toBeEnabled();
  });
});

describe("ReviewModeration — acciones", () => {
  it("Publicar la manda a publicar", async () => {
    renderModeration();
    const card = await findCard();

    fireEvent.click(within(card).getByRole("button", { name: "Publicar" }));

    await waitFor(() =>
      expect(posts()).toEqual([
        { url: "http://localhost:8000/api/reviews/reviews/rev_order_01J/approve", body: undefined },
      ]),
    );
  });

  it("si la tienda falla al publicar, muestra lo que dijo el backend en la tarjeta", async () => {
    actionResponse = {
      status: 502,
      body: { detail: "La tienda no respondió. La reseña sigue por revisar; inténtalo en un rato." },
    };
    renderModeration();
    const card = await findCard();

    fireEvent.click(within(card).getByRole("button", { name: "Publicar" }));

    expect(
      await within(card).findByText(
        "La tienda no respondió. La reseña sigue por revisar; inténtalo en un rato.",
      ),
    ).toBeInTheDocument();
  });

  it("un intento anterior fallido queda a la vista (published.error)", async () => {
    lists.pending = [{ ...REVIEW_FIXTURE, published: { at_ms: null, error: "La tienda rechazó la reseña." } }];
    renderModeration();
    const card = await findCard();

    expect(
      within(card).getByText("No se pudo publicar en la tienda: La tienda rechazó la reseña."),
    ).toBeInTheDocument();
  });

  it("Ocultar la saca de la vista pública", async () => {
    actionResponse = { status: 200, body: { review: { ...REVIEW_FIXTURE, status: "hidden" } } };
    renderModeration();
    const card = await findCard();

    fireEvent.click(within(card).getByRole("button", { name: "Ocultar" }));

    await waitFor(() =>
      expect(posts()).toEqual([
        { url: "http://localhost:8000/api/reviews/reviews/rev_order_01J/hide", body: undefined },
      ]),
    );
  });

  it("Responder abre un cuadro de texto y manda la respuesta", async () => {
    lists.pending = [{ ...REVIEW_FIXTURE, reply: null }];
    actionResponse = { status: 200, body: { review: { ...REVIEW_FIXTURE, reply: "¡Gracias, Ana!" } } };
    renderModeration();
    const card = await findCard();

    fireEvent.click(within(card).getByRole("button", { name: "Responder" }));
    const box = within(card).getByRole("textbox", { name: "Respuesta para Ana G." });
    const send = within(card).getByRole("button", { name: "Enviar respuesta" });
    expect(send).toBeDisabled(); // vacío no se manda

    fireEvent.change(box, { target: { value: "¡Gracias, Ana!" } });
    expect(within(card).getByText("14/600")).toBeInTheDocument();
    fireEvent.click(send);

    await waitFor(() =>
      expect(posts()).toEqual([
        {
          url: "http://localhost:8000/api/reviews/reviews/rev_order_01J/reply",
          body: { reply: "¡Gracias, Ana!" },
        },
      ]),
    );
    // Al guardar se cierra el cuadro.
    await waitFor(() =>
      expect(within(card).queryByRole("textbox", { name: "Respuesta para Ana G." })).not.toBeInTheDocument(),
    );
  });

  it("la respuesta ya guardada se ve y se puede cambiar", async () => {
    renderModeration();
    const card = await findCard();

    expect(within(card).getByText("¡Gracias!")).toBeInTheDocument();
    fireEvent.click(within(card).getByRole("button", { name: "Cambiar respuesta" }));
    expect(within(card).getByRole("textbox", { name: "Respuesta para Ana G." })).toHaveValue("¡Gracias!");

    fireEvent.click(within(card).getByRole("button", { name: "Cancelar" }));
    expect(within(card).queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("la respuesta no pasa de 600 caracteres", async () => {
    lists.pending = [{ ...REVIEW_FIXTURE, reply: null }];
    renderModeration();
    const card = await findCard();

    fireEvent.click(within(card).getByRole("button", { name: "Responder" }));
    expect(within(card).getByRole("textbox", { name: "Respuesta para Ana G." })).toHaveAttribute(
      "maxLength",
      "600",
    );
  });
});
