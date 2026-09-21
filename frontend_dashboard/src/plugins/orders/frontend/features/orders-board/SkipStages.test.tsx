/**
 * Salto de etapas en el kanban: un operador olvidó mover el pedido y ya se
 * entregó. Soltarlo en "Entregada" desde "En preparación" NO debe recorrer
 * Lista / En camino (cada paso le mandaría un WhatsApp al cliente fuera de
 * contexto). Comportamiento:
 *  - `skippedStages` detecta los saltos hacia adelante (no los adyacentes,
 *    ni hacia atrás, ni cancelar);
 *  - el modal de salto confirma con el aviso al cliente APAGADO por defecto;
 *  - el tablero manda UN PATCH con force + notify_customer + nota del salto.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import type { Order } from "@plugins/orders/frontend/entities/order";

import { skipNote, skippedStages } from "./model/stageSkip";
import { SkipStagesModal } from "./ui/SkipStagesModal";

const mutate = vi.fn();

vi.mock("@plugins/orders/frontend/entities/order", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@plugins/orders/frontend/entities/order")>();
  return {
    ...actual,
    useTransitionOrderStage: () => ({ mutate, isPending: false }),
  };
});

// Import después del mock para que el board use el hook falso.
const { OrdersBoard } = await import("./ui/OrdersBoard");

describe("skippedStages", () => {
  it("lists the intermediate stages of a forward jump", () => {
    expect(skippedStages("preparing", "delivered")).toEqual(["ready", "shipping"]);
    expect(skippedStages("new", "ready")).toEqual(["preparing"]);
  });
  it("is empty for adjacent, backward and cancel moves", () => {
    expect(skippedStages("preparing", "ready")).toEqual([]);
    expect(skippedStages("shipping", "preparing")).toEqual([]);
    expect(skippedStages("preparing", "cancelled")).toEqual([]);
  });
  it("builds a human note for the stage history", () => {
    expect(skipNote(["ready", "shipping"])).toBe("Salto manual: se omitió Lista, En camino");
  });
});

describe("SkipStagesModal", () => {
  function setup() {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(
      <SkipStagesModal
        orderId="#32"
        from="preparing"
        to="delivered"
        skipped={["ready", "shipping"]}
        onConfirm={onConfirm}
        onCancel={onCancel}
      />,
    );
    return { onConfirm, onCancel };
  }

  it("explains the jump and keeps the customer notice off by default", () => {
    const { onConfirm } = setup();
    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent).toContain("Lista");
    expect(dialog.textContent).toContain("En camino");
    const notify = screen.getByRole("checkbox", { name: /avisar al cliente/i });
    expect((notify as HTMLInputElement).checked).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: /mover a entregada/i }));
    expect(onConfirm).toHaveBeenCalledWith(false);
  });

  it("confirms with the notice when the operator opts in", () => {
    const { onConfirm } = setup();
    fireEvent.click(screen.getByRole("checkbox", { name: /avisar al cliente/i }));
    fireEvent.click(screen.getByRole("button", { name: /mover a entregada/i }));
    expect(onConfirm).toHaveBeenCalledWith(true);
  });

  it("cancels with the button or Escape", () => {
    const { onCancel, onConfirm } = setup();
    fireEvent.click(screen.getByRole("button", { name: /cancelar/i }));
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(2);
    expect(onConfirm).not.toHaveBeenCalled();
  });
});

describe("OrdersBoard — drop que salta etapas", () => {
  beforeEach(() => mutate.mockReset());

  const order = {
    id: "#32",
    status: "preparing",
    customer: "Cliente",
    short: "CL",
    color: "#888",
    items: 1,
    pieces: 1,
    total: 50000,
    payStatus: "paid",
    payType: "transfer",
    isDraft: false,
    overdue: false,
    dueIso: "2026-09-21",
    dueTime: "10:00",
  } as unknown as Order;

  function dropOn(columnLabel: string) {
    const column = screen.getByText(columnLabel).closest(".kcol")!;
    const dataTransfer = { getData: () => "#32", types: ["application/x-hubara-order"] };
    fireEvent.drop(column, { dataTransfer });
  }

  it("asks before jumping and sends one forced, silent PATCH", () => {
    render(<OrdersBoard orders={[order]} selectedId={null} onSelect={() => {}} />);

    dropOn("Entregada");
    expect(mutate).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /mover a entregada/i }));
    expect(mutate).toHaveBeenCalledTimes(1);
    expect(mutate.mock.calls[0][0]).toEqual({
      orderId: "#32",
      to_stage: "delivered",
      force: true,
      notify_customer: false,
      note: "Salto manual: se omitió Lista, En camino",
    });
  });

  it("keeps adjacent moves as a single direct PATCH", () => {
    // "Lista" y "En camino" abren su propio modal (foto / guía); el resto de
    // movimientos adyacentes van directo.
    render(<OrdersBoard orders={[order]} selectedId={null} onSelect={() => {}} />);
    dropOn("Nueva");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(mutate.mock.calls[0][0]).toMatchObject({ orderId: "#32", to_stage: "new" });
  });
});
