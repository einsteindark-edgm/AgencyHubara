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

export function Panel({ title, actions, children, defaultOpen = true }: Props) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={"panel" + (open ? "" : " closed")}>
      <div className="panel-h" onClick={() => setOpen(!open)}>
        <span className="caret"><Icon.caret /></span>
        <span className="ttl">{title}</span>
        {actions && (
          <span className="actions" onClick={(e) => e.stopPropagation()}>
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
  return (
    <div className={"ins-block" + (isOpen ? "" : " closed")}>
      <div className="ib-head" onClick={() => setIsOpen(!isOpen)}>
        <span className="ib-caret"><Icon.caret /></span>
        <span className="ib-title">{title}</span>
      </div>
      <div className="ib-body">{children}</div>
    </div>
  );
}
