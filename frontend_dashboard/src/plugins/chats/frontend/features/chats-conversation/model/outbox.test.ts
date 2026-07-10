/**
 * Tests del reducer puro del outbox de fotos. El reducer es la máquina de
 * estados de cada envío optimista (compressing → uploading → sending → sent |
 * failed) — testeable sin red ni React.
 */

import { describe, it, expect } from "vitest";
import { outboxReducer, type OutboxState } from "./useOutbox";

const empty: OutboxState = { items: [] };

describe("outboxReducer", () => {
  it("enqueue agrega un item en estado compressing", () => {
    const s = outboxReducer(empty, {
      type: "enqueue",
      id: "m1",
      previewUrl: "blob:x",
      caption: "hola",
    });
    expect(s.items).toHaveLength(1);
    expect(s.items[0]).toMatchObject({
      id: "m1",
      status: "compressing",
      progress: 0,
      caption: "hola",
    });
  });

  it("transiciona compressing → uploading → sending → sent (remueve al enviar)", () => {
    let s = outboxReducer(empty, {
      type: "enqueue",
      id: "m1",
      previewUrl: "blob:x",
      caption: "",
    });
    s = outboxReducer(s, { type: "compressed", id: "m1" });
    expect(s.items[0].status).toBe("uploading");
    s = outboxReducer(s, { type: "progress", id: "m1", fraction: 0.5 });
    expect(s.items[0].progress).toBe(0.5);
    s = outboxReducer(s, { type: "uploaded", id: "m1", attachmentId: "att-1" });
    expect(s.items[0].status).toBe("sending");
    expect(s.items[0].attachmentId).toBe("att-1");
    // Al enviarse, el item se remueve — la burbuja real del servidor lo reemplaza.
    s = outboxReducer(s, { type: "sent", id: "m1" });
    expect(s.items).toHaveLength(0);
  });

  it("failed marca el item con error sin removerlo (permite reintentar)", () => {
    let s = outboxReducer(empty, {
      type: "enqueue",
      id: "m1",
      previewUrl: "blob:x",
      caption: "",
    });
    s = outboxReducer(s, { type: "failed", id: "m1", error: "red caída" });
    expect(s.items[0].status).toBe("failed");
    expect(s.items[0].error).toBe("red caída");
  });

  it("retry resetea el item a uploading si ya tenía attachment (no re-sube)", () => {
    let s = outboxReducer(empty, {
      type: "enqueue",
      id: "m1",
      previewUrl: "blob:x",
      caption: "",
    });
    s = outboxReducer(s, { type: "uploaded", id: "m1", attachmentId: "att-1" });
    s = outboxReducer(s, { type: "failed", id: "m1", error: "send falló" });
    s = outboxReducer(s, { type: "retry", id: "m1" });
    // Ya tiene attachment → reintenta desde "sending", conserva attachmentId.
    expect(s.items[0].status).toBe("sending");
    expect(s.items[0].attachmentId).toBe("att-1");
    expect(s.items[0].error).toBeUndefined();
  });

  it("retry sin attachment reinicia desde compressing", () => {
    let s = outboxReducer(empty, {
      type: "enqueue",
      id: "m1",
      previewUrl: "blob:x",
      caption: "",
    });
    s = outboxReducer(s, { type: "failed", id: "m1", error: "compress falló" });
    s = outboxReducer(s, { type: "retry", id: "m1" });
    expect(s.items[0].status).toBe("compressing");
    expect(s.items[0].error).toBeUndefined();
  });

  it("remove saca el item (descartar un fallido)", () => {
    let s = outboxReducer(empty, {
      type: "enqueue",
      id: "m1",
      previewUrl: "blob:x",
      caption: "",
    });
    s = outboxReducer(s, { type: "remove", id: "m1" });
    expect(s.items).toHaveLength(0);
  });

  it("acciones sobre un id inexistente no rompen el estado", () => {
    const s = outboxReducer(empty, { type: "progress", id: "ghost", fraction: 1 });
    expect(s.items).toHaveLength(0);
  });
});
