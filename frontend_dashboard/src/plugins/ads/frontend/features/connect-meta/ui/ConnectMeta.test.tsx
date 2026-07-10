/**
 * Single-tenant Meta (decisión 2026-07-09): la conexión se PROVISIONA server-side
 * (system-user token en SSM), no se obtiene por diálogo OAuth. La feature muestra
 * SOLO estado — sin botón de login (disparaba un flujo muerto) y sin "Desconectar"
 * (borraba el token provisionado sin camino self-service de vuelta).
 */

import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";

const mockConnection = vi.hoisted(() => ({ current: {} as object }));

vi.mock("@plugins/ads/frontend/entities/meta-connection", () => ({
  useMetaConnection: () => mockConnection.current,
}));

import { ConnectMeta } from "./ConnectMeta";

describe("ConnectMeta — estado de conexión provisionada (sin acciones)", () => {
  it("desconectado: informa el estado, SIN botón de login", () => {
    mockConnection.current = { data: { connected: false }, isLoading: false };
    const { queryByRole, getByText } = render(<ConnectMeta />);

    expect(getByText(/meta no conectado/i)).toBeTruthy();
    expect(queryByRole("button")).toBeNull();
  });

  it("conectado: chip con la cuenta, SIN Desconectar", () => {
    mockConnection.current = {
      data: {
        connected: true,
        accountName: "Hubara",
        expiresAt: null,
        expired: false,
      },
      isLoading: false,
    };
    const { queryByRole, getByText } = render(<ConnectMeta />);

    expect(getByText(/meta conectado/i)).toBeTruthy();
    expect(getByText(/hubara/i)).toBeTruthy();
    expect(queryByRole("button")).toBeNull();
  });

  it("token con expiración vencida: lo señala, sin botón de reconexión", () => {
    mockConnection.current = {
      data: {
        connected: true,
        accountName: "Hubara",
        expiresAt: 1,
        expired: true,
      },
      isLoading: false,
    };
    const { queryByRole, getByText } = render(<ConnectMeta />);

    expect(getByText(/expirad/i)).toBeTruthy();
    expect(queryByRole("button")).toBeNull();
  });
});
