/**
 * Banner de pedidos pendientes de reconciliar (vault sin Medusa).
 *
 * C-4 (premortem de la central de cupones): la reconciliación NO registra un
 * pedido cuyas unidades con descuento ya se vendieron (`abandon_reason:
 * "quota_changed"`) — un humano tiene que confirmar el total nuevo con el
 * cliente. El banner lo dice con esas palabras, no solo "Abandonado" con el
 * error viejo de Medusa. Las mutations se stubean.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("@plugins/orders/frontend/entities/order", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useRetryVaultOrder: () => ({ mutate: vi.fn(), isPending: false, isError: false, data: undefined }),
  useResolveVaultOrder: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
}));

import {
  vaultOrdersResponseSchema,
  type VaultOrderRecord,
} from "@plugins/orders/frontend/entities/order";

import { VaultOrdersBanner } from "./ui/VaultOrdersBanner";

const QUOTA_MESSAGE =
  "Ya no quedan las unidades con descuento: confirma el total nuevo con el cliente antes de registrarlo a mano";

function record(over: Record<string, unknown> = {}): VaultOrderRecord {
  const parsed = vaultOrdersResponseSchema.parse({
    records: [
      {
        kind: "failed",
        session_key: "wa_golden_vault",
        order_id: "AUDIT-1",
        customer_phone: "570000000000",
        customer_city: "Bogotá",
        total_cop: 47800,
        currency: "COP",
        items_count: 1,
        payment_method: "transfer",
        error_detail: "medusa_api_error: HTTP 503 /admin/draft-orders: down",
        registered_at_ms: 1_790_000_000_000,
        status: "abandoned",
        attempts: 3,
        ...over,
      },
    ],
    count: 1,
    failed_count: 1,
    stub_count: 0,
  });
  return parsed.records[0]!;
}

function renderBanner(r: VaultOrderRecord) {
  return render(<VaultOrdersBanner failedCount={1} stubCount={0} records={[r]} />);
}

describe("VaultOrdersBanner — motivo del abandono (C-4)", () => {
  it("sin las unidades con descuento lo dice explícito, no solo 'Abandonado' con el error viejo", () => {
    renderBanner(record({ abandon_reason: "quota_changed" }));

    expect(screen.getByText(QUOTA_MESSAGE)).toBeInTheDocument();
    // El error viejo de Medusa ya no es el motivo que se le muestra.
    expect(screen.getByText(QUOTA_MESSAGE).closest("td")?.getAttribute("title")).toBe(QUOTA_MESSAGE);
  });

  it("un backend que aún no expone el campo lo trae en el registro crudo", () => {
    renderBanner(record({ raw: { abandon_reason: "quota_changed" } }));

    expect(screen.getByText(QUOTA_MESSAGE)).toBeInTheDocument();
  });

  it("otro abandono sigue diciendo 'Abandonado' con el error como detalle", () => {
    const r = record();
    expect(r.abandon_reason).toBeNull();
    renderBanner(r);

    expect(screen.getByText(/Abandonado/)).toBeInTheDocument();
    expect(screen.queryByText(QUOTA_MESSAGE)).not.toBeInTheDocument();
  });
});
