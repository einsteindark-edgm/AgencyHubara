/**
 * Tests de las mutaciones de handoff. Mockeamos `fetch` y verificamos:
 *   - Cada hook llama al endpoint correcto con el body esperado.
 *   - El schema Zod parsea respuestas válidas.
 *   - El cache de `sessionKeys` se invalida en `onSuccess`.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { sessionKeys } from "@plugins/chats/frontend/entities/session";
import {
  useInterveneMutation,
  useReturnToBotMutation,
  useSendHumanMessageMutation,
  useSendTemplateMessageMutation,
  useWhatsAppTemplates,
} from "./api";

const fetchMock = vi.fn();

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper };
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

describe("useInterveneMutation", () => {
  it("posts to /intervene and invalidates session detail + list", async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          ok: true,
          active_route: "humano",
          tag: "HUMANO",
          motivo: "tomé control",
          terminated_workflows: ["session-wa_X"],
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );

    const { client, wrapper } = makeWrapper();
    const invalidate = vi.spyOn(client, "invalidateQueries");

    const { result } = renderHook(() => useInterveneMutation("wa_X"), {
      wrapper,
    });

    await act(async () => {
      await result.current.mutateAsync({ motivo: "tomé control" });
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/dashboard/sessions/wa_X/intervene");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ motivo: "tomé control" });

    expect(result.current.data?.active_route).toBe("humano");
    expect(result.current.data?.terminated_workflows).toEqual(["session-wa_X"]);

    // Invalida tanto detail como list.
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: sessionKeys.detail("wa_X"),
    });
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: sessionKeys.list(),
    });
  });

  it("throws when no session is selected", async () => {
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useInterveneMutation(null), {
      wrapper,
    });
    await expect(result.current.mutateAsync({})).rejects.toThrow(
      "No session selected",
    );
  });
});

describe("useSendHumanMessageMutation", () => {
  it("posts text and parses HumanMessageResponse", async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          ok: true,
          role: "assistant",
          sender: "human",
          content: "hola desde el dashboard",
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );

    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useSendHumanMessageMutation("wa_Y"), {
      wrapper,
    });

    let data: { sender: string } | undefined;
    await act(async () => {
      data = await result.current.mutateAsync({
        text: "hola desde el dashboard",
      });
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/dashboard/sessions/wa_Y/messages");
    expect(JSON.parse(init.body)).toEqual({ text: "hola desde el dashboard" });
    expect(data?.sender).toBe("human");
  });

  it("propagates ApiError on 409 (sesión no está en humano)", async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({ detail: "no está en ruta humano" }),
        { status: 409, headers: { "content-type": "application/json" } },
      ),
    );

    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useSendHumanMessageMutation("wa_Z"), {
      wrapper,
    });

    await expect(
      result.current.mutateAsync({ text: "intento" }),
    ).rejects.toBeTruthy();
  });
});

describe("useReturnToBotMutation", () => {
  it("posts target_route=ventas without motivo", async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          ok: true,
          active_route: "ventas",
          tag: "RETOMA_VENTA",
          motivo: "default",
          terminated_workflows: [],
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );

    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useReturnToBotMutation("wa_A"), {
      wrapper,
    });

    let data: { tag: string } | undefined;
    await act(async () => {
      data = await result.current.mutateAsync({ target_route: "ventas" });
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/dashboard/sessions/wa_A/return-to-bot");
    expect(JSON.parse(init.body)).toEqual({ target_route: "ventas" });
    expect(data?.tag).toBe("RETOMA_VENTA");
  });

  it("posts target_route=remarketing with motivo", async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          ok: true,
          active_route: "remarketing",
          tag: "REMARKETING",
          motivo: "indeciso, retomar suave",
          terminated_workflows: [],
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );

    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useReturnToBotMutation("wa_B"), {
      wrapper,
    });

    let data: { active_route: string } | undefined;
    await act(async () => {
      data = await result.current.mutateAsync({
        target_route: "remarketing",
        motivo: "indeciso, retomar suave",
      });
    });

    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      target_route: "remarketing",
      motivo: "indeciso, retomar suave",
    });
    expect(data?.active_route).toBe("remarketing");
  });
});

describe("humanMessageResponseSchema — adjunto documento (PDF)", () => {
  it("conserva document_url y document_filename del backend", async () => {
    const { humanMessageResponseSchema } = await import("./contracts");
    const parsed = humanMessageResponseSchema.parse({
      ok: true,
      role: "assistant",
      sender: "human",
      content: "Ahí va",
      image_url: null,
      document_url: "/api/dashboard/media/wa_x/out-1.pdf",
      document_filename: "comprobante.pdf",
    });
    expect(parsed.document_url).toBe("/api/dashboard/media/wa_x/out-1.pdf");
    expect(parsed.document_filename).toBe("comprobante.pdf");
  });
});

describe("uploadHumanMedia — timeout del XHR escala con el tamaño (PDFs)", () => {
  class FakeXhr {
    static last: FakeXhr | null = null;
    upload: { onprogress: ((e: unknown) => void) | null } = { onprogress: null };
    timeout = 0;
    status = 200;
    responseText = JSON.stringify({
      ok: true,
      attachment_id: "att-1",
      media_ref: "/api/dashboard/media/wa_x/out-1.pdf",
    });
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    ontimeout: (() => void) | null = null;
    open() {}
    setRequestHeader() {}
    send() {
      FakeXhr.last = this;
      this.onload?.();
    }
  }

  it("8 MB → 300s (cap); un blob chico conserva los 60s", async () => {
    const { uploadHumanMedia } = await import("./api");
    vi.stubGlobal("XMLHttpRequest", FakeXhr as unknown as typeof XMLHttpRequest);
    try {
      await uploadHumanMedia(
        "wa_x",
        new Blob([new ArrayBuffer(8 * 1024 * 1024)]),
        "comprobante.pdf",
      );
      expect(FakeXhr.last?.timeout).toBe(300_000);

      await uploadHumanMedia("wa_x", new Blob([new ArrayBuffer(10)]), "f.jpg");
      expect(FakeXhr.last?.timeout).toBe(60_000);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe("useWhatsAppTemplates", () => {
  it("loads the template catalog from the dashboard API", async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          templates: [
            {
              name: "human_followup_utility_v1",
              category: "utility",
              semantics: "Seguimiento",
              body: "Hola {{1}} gracias",
              variables: [{ name: "followup_message", description: "Mensaje", max_length: 400 }],
              is_default: true,
            },
          ],
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );

    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useWhatsAppTemplates(true), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(fetchMock.mock.calls[0][0]).toContain("/api/dashboard/whatsapp-templates");
    expect(result.current.data?.[0].is_default).toBe(true);
  });
});

describe("useSendTemplateMessageMutation", () => {
  it("posts the template with its variables and refreshes the chat", async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          ok: true,
          role: "assistant",
          sender: "human",
          content: "Hola, te escribimos… Ya tenemos tus fotos.",
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );

    const { client, wrapper } = makeWrapper();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useSendTemplateMessageMutation("wa_T"), { wrapper });

    await act(async () => {
      await result.current.mutateAsync({
        template_name: "human_followup_utility_v1",
        variables: { followup_message: "Ya tenemos tus fotos." },
        client_message_id: "cmid-1",
      });
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/dashboard/sessions/wa_T/template-messages");
    expect(JSON.parse(init.body)).toEqual({
      template_name: "human_followup_utility_v1",
      variables: { followup_message: "Ya tenemos tus fotos." },
      client_message_id: "cmid-1",
    });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: sessionKeys.detail("wa_T") });
  });
});
