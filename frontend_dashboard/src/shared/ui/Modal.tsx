/**
 * Modal compartido (plan del laboratorio §11.1). Lo estrena el modal del hilo
 * del turno (sección Laboratorio) y después lo usa Chats.
 *
 * Accesibilidad: `role="dialog"` + `aria-modal` + `aria-labelledby`; Escape y
 * clic en el fondo cierran; el foco vuelve al elemento que tenía el foco al
 * abrir (el que lo abrió); la página de atrás no se desplaza mientras está
 * abierto. En el celular (< 760 px) ocupa toda la pantalla. Colores solo con
 * los tokens del `@theme` (R-TAILWIND: sin archivos .css propios).
 */

import { useEffect, useRef, type ReactNode } from "react";

interface Props {
  open: boolean;
  onClose: () => void;
  /** id del título dentro del modal. */
  labelledBy: string;
  children: ReactNode;
  className?: string;
}

export function Modal({ open, onClose, labelledBy, children, className }: Props) {
  const returnTo = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) return undefined;
    returnTo.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCloseRef.current();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
      returnTo.current?.focus?.();
    };
  }, [open]);

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-0 min-[760px]:p-4"
      role="presentation"
      data-testid="modal-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className={
          "flex h-full w-full flex-col overflow-hidden bg-win-bg text-fg shadow-2xl " +
          "min-[760px]:h-auto min-[760px]:max-h-[min(88vh,900px)] min-[760px]:max-w-[1120px] " +
          "min-[760px]:rounded-xl min-[760px]:border min-[760px]:border-line-strong" +
          (className ? ` ${className}` : "")
        }
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
      >
        {children}
      </div>
    </div>
  );
}
