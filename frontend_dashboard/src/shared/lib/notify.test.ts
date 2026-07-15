/**
 * `sendSystemNotification` en Tauri/Android: debe crear un canal de ALTA
 * importancia y enviar por él. Visto en device real (2026-07-15): sin canal
 * explícito el plugin postea en su canal "default" (importancia estándar) →
 * notificación silenciosa, sin heads-up ni vibración — el operador nunca se
 * entera del handoff aunque la notificación exista en la bandeja.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";

const createChannel = vi.fn().mockResolvedValue(undefined);
const sendNotification = vi.fn();
const isPermissionGranted = vi.fn().mockResolvedValue(true);

vi.mock("@tauri-apps/plugin-notification", () => ({
  createChannel: (...a: unknown[]) => createChannel(...a),
  sendNotification: (...a: unknown[]) => sendNotification(...a),
  isPermissionGranted: () => isPermissionGranted(),
  requestPermission: vi.fn().mockResolvedValue("granted"),
  Importance: { None: 0, Min: 1, Low: 2, Default: 3, High: 4 },
  Visibility: { Secret: -1, Private: 0, Public: 1 },
}));

// Forzar el path Tauri (IS_DESKTOP true) sin depender de __TAURI_INTERNALS__.
vi.mock("./runtime", () => ({
  IS_DESKTOP: true,
  IS_MOBILE_APP: false,
}));

beforeEach(() => {
  createChannel.mockClear();
  sendNotification.mockClear();
});

describe("sendSystemNotification (Tauri/Android)", () => {
  it("crea el canal 'handoffs' con importancia ALTA y envía por él", async () => {
    const { sendSystemNotification } = await import("./notify");
    await sendSystemNotification("Conversación asignada", "cliente X");

    expect(createChannel).toHaveBeenCalledWith(
      expect.objectContaining({ id: "handoffs", importance: 4, vibration: true }),
    );
    expect(sendNotification).toHaveBeenCalledWith(
      expect.objectContaining({
        title: "Conversación asignada",
        body: "cliente X",
        channelId: "handoffs",
      }),
    );
  });

  it("si el engine no soporta canales (desktop), envía igual sin channelId", async () => {
    createChannel.mockRejectedValueOnce(new Error("not supported"));
    const { sendSystemNotification } = await import("./notify");
    await sendSystemNotification("t", "b");

    expect(sendNotification).toHaveBeenCalledWith(
      expect.objectContaining({ title: "t", body: "b", channelId: undefined }),
    );
  });
});
