/**
 * `Panel` colapsable usado en inspectores (paneles tipo "disclosure" Mac).
 * Mantiene su propio estado abierto/cerrado vía `defaultOpen`.
 */

import { useState, type ReactNode } from "react";
import { Icon } from "./Icon";

interface Props {
  title: string;
  actions?: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
}

/**
 * F6.2 (a11y): el header es un div clickeable por CSS legacy — le damos
 * semántica de botón (role + tabIndex + Enter/Espacio + aria-expanded) sin
 * cambiar el markup, para no alterar el render.
 */
function toggleKeyHandler(toggle: () => void) {
  return (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      toggle();
    }
  };
}

export function Panel({ title, actions, children, defaultOpen = true }: Props) {
  const [open, setOpen] = useState(defaultOpen);
  const toggle = () => setOpen((v) => !v);
  return (
    <div className={"panel" + (open ? "" : " closed")}>
      <div
        className="panel-h"
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={toggle}
        onKeyDown={toggleKeyHandler(toggle)}
      >
        <span className="caret"><Icon.caret /></span>
        <span className="ttl">{title}</span>
        {actions && (
          // Wrapper que corta la propagación para que clickear una acción no
          // colapse el panel — presentational, los interactivos son los hijos.
          <span
            className="actions"
            role="presentation"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.stopPropagation()}
          >
            {actions}
          </span>
        )}
      </div>
      <div className="panel-c">{children}</div>
    </div>
  );
}

interface InsBlockProps {
  title: string;
  open?: boolean;
  children: ReactNode;
}

/** Variante usada en el inspector de Órdenes (estilo `ins-block`). */
export function InsBlock({ title, open = true, children }: InsBlockProps) {
  const [isOpen, setIsOpen] = useState(open);
  const toggle = () => setIsOpen((v) => !v);
  return (
    <div className={"ins-block" + (isOpen ? "" : " closed")}>
      <div
        className="ib-head"
        role="button"
        tabIndex={0}
        aria-expanded={isOpen}
        onClick={toggle}
        onKeyDown={toggleKeyHandler(toggle)}
      >
        <span className="ib-caret"><Icon.caret /></span>
        <span className="ib-title">{title}</span>
      </div>
      <div className="ib-body">{children}</div>
    </div>
  );
}
