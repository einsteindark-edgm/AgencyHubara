import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";

import { Modal } from "./Modal";

/**
 * Modal compartido (plan del laboratorio §11.1): lo estrena el modal del hilo
 * del turno y después lo usa Chats. Accesible: `role="dialog"` con
 * `aria-modal`, Escape y clic en el fondo cierran, el foco vuelve a lo que
 * abrió el modal y la página de atrás no se desplaza mientras está abierto.
 */

function Harness({ onClose }: { onClose?: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Ver hilo del turno
      </button>
      <Modal
        open={open}
        labelledBy="m-title"
        onClose={() => {
          onClose?.();
          setOpen(false);
        }}
      >
        <p id="m-title">Hilo del turno 2</p>
        <button type="button">Adentro</button>
      </Modal>
    </>
  );
}

describe("Modal", () => {
  it("no renderiza nada cerrado", () => {
    render(<Harness />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("abre como diálogo modal con su título y bloquea el scroll", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "Ver hilo del turno" }));

    const dialog = screen.getByRole("dialog", { name: "Hilo del turno 2" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(document.body.style.overflow).toBe("hidden");
  });

  it("Escape cierra y el foco vuelve a lo que lo abrió", () => {
    const onClose = vi.fn();
    render(<Harness onClose={onClose} />);
    const trigger = screen.getByRole("button", { name: "Ver hilo del turno" });
    trigger.focus();
    fireEvent.click(trigger);

    fireEvent.keyDown(document, { key: "Escape" });

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(trigger);
    expect(document.body.style.overflow).toBe("");
  });

  it("clic en el fondo cierra; clic adentro no", () => {
    const onClose = vi.fn();
    render(<Harness onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: "Ver hilo del turno" }));

    fireEvent.click(screen.getByRole("button", { name: "Adentro" }));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("modal-backdrop"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
