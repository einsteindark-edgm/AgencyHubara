/**
 * Hooks de las reseñas contra una QueryClient REAL (se stubea solo `fetch`):
 * qué URL piden, qué devuelven ya pasado por Zod, y qué refrescan después de
 * publicar, ocultar o responder (la reseña cambia de bandeja y el resumen
 * cambia de números).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";

import {
  useApproveReview,
  useHideReview,
  useReplyReview,
  useReviews,
  useReviewsSummary,
} from "./api";
import { REVIEW_FIXTURE, SUMMARY_FIXTURE } from "./fixtures";
import { reviewKeys } from "./keys";
import { reviewErrorDetail } from "./model";

const fetchMock = vi.fn();

function json(body: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    }),
  );
}

function setup() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return { qc, wrapper };
}

function calls(): Array<{ url: string; method: string; body: unknown }> {
  return fetchMock.mock.calls.map(([url, init]) => {
    const req = init as RequestInit | undefined;
    return {
      url: String(url),
      method: req?.method ?? "GET",
      body: typeof req?.body === "string" ? (JSON.parse(req.body) as unknown) : undefined,
    };
  });
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

describe("useReviews", () => {
  it("pide la bandeja del estado elegido y devuelve reseñas + conteos", async () => {
    fetchMock.mockImplementation(() =>
      json({ reviews: [REVIEW_FIXTURE], counts: { pending: 3, approved: 10, hidden: 1 } }),
    );
    const { wrapper } = setup();

    const { result } = renderHook(() => useReviews("pending"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(calls()[0]?.url).toBe("http://localhost:8000/api/reviews/reviews?status=pending");
    expect(result.current.data?.counts).toEqual({ pending: 3, approved: 10, hidden: 1 });
    expect(result.current.data?.reviews[0]?.displayId).toBe("#31");
  });
});

describe("useReviewsSummary", () => {
  it("pide el resumen y lo devuelve en el dominio", async () => {
    fetchMock.mockImplementation(() => json(SUMMARY_FIXTURE));
    const { wrapper } = setup();

    const { result } = renderHook(() => useReviewsSummary(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(calls()[0]?.url).toBe("http://localhost:8000/api/reviews/summary");
    expect(result.current.data?.reviews.averageRating).toBe(4.6);
    expect(result.current.data?.settings.mode).toBe("test");
  });
});

describe("moderar una reseña", () => {
  function seed(qc: QueryClient) {
    const keys = [
      reviewKeys.list("pending"),
      reviewKeys.list("approved"),
      reviewKeys.list("hidden"),
      reviewKeys.summary(),
    ];
    for (const key of keys) qc.setQueryData(key, {});
    return keys;
  }

  it("Publicar llama a approve y refresca las tres bandejas y el resumen", async () => {
    fetchMock.mockImplementation(() => json({ review: { ...REVIEW_FIXTURE, status: "approved" } }));
    const { qc, wrapper } = setup();
    const keys = seed(qc);

    const { result } = renderHook(() => useApproveReview("rev_order_01J"), { wrapper });
    let returned: unknown;
    await act(async () => {
      returned = await result.current.mutateAsync();
    });

    expect(calls()[0]).toMatchObject({
      url: "http://localhost:8000/api/reviews/reviews/rev_order_01J/approve",
      method: "POST",
    });
    expect(returned).toMatchObject({ reviewId: "rev_order_01J", status: "approved" });
    for (const key of keys) expect(qc.getQueryState(key)?.isInvalidated).toBe(true);
  });

  it("si la tienda falla (502), el error trae el texto para el equipo y la bandeja igual se refresca", async () => {
    fetchMock.mockImplementation(() =>
      json({ detail: "La tienda no respondió. La reseña sigue por revisar." }, 502),
    );
    const { qc, wrapper } = setup();
    seed(qc);

    const { result } = renderHook(() => useApproveReview("rev_order_01J"), { wrapper });
    await act(async () => {
      await result.current.mutateAsync().catch(() => undefined);
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(reviewErrorDetail(result.current.error)).toBe(
      "La tienda no respondió. La reseña sigue por revisar.",
    );
    // La reseña quedó con `published.error`: la tarjeta lo tiene que ver.
    expect(qc.getQueryState(reviewKeys.list("pending"))?.isInvalidated).toBe(true);
  });

  it("Ocultar llama a hide", async () => {
    fetchMock.mockImplementation(() => json({ review: { ...REVIEW_FIXTURE, status: "hidden" } }));
    const { qc, wrapper } = setup();
    const keys = seed(qc);

    const { result } = renderHook(() => useHideReview("rev_order_01J"), { wrapper });
    await act(async () => {
      await result.current.mutateAsync();
    });

    expect(calls()[0]).toMatchObject({
      url: "http://localhost:8000/api/reviews/reviews/rev_order_01J/hide",
      method: "POST",
    });
    for (const key of keys) expect(qc.getQueryState(key)?.isInvalidated).toBe(true);
  });

  it("Responder manda el texto en `reply`", async () => {
    fetchMock.mockImplementation(() =>
      json({ review: { ...REVIEW_FIXTURE, reply: "¡Gracias, Ana!" } }),
    );
    const { qc, wrapper } = setup();
    seed(qc);

    const { result } = renderHook(() => useReplyReview("rev_order_01J"), { wrapper });
    await act(async () => {
      await result.current.mutateAsync("¡Gracias, Ana!");
    });

    expect(calls()[0]).toEqual({
      url: "http://localhost:8000/api/reviews/reviews/rev_order_01J/reply",
      method: "POST",
      body: { reply: "¡Gracias, Ana!" },
    });
    expect(qc.getQueryState(reviewKeys.list("pending"))?.isInvalidated).toBe(true);
  });
});
