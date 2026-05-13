/**
 * `MacButton` — botón estilo "push" macOS. Variantes:
 *   - default: gradient gris claro/oscuro (acción secundaria).
 *   - primary: gradient azul (acción dominante).
 *   - ghost:   transparente, sólo hover (acciones inline en toolbars).
 *   - danger:  rojo (acciones destructivas).
 *
 * `sm` reduce padding y font-size — coincide con la densidad del prototipo.
 */

import type { ButtonHTMLAttributes, ReactNode } from "react";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  primary?: boolean;
  ghost?: boolean;
  danger?: boolean;
  sm?: boolean;
  children: ReactNode;
}

export function MacButton({
  primary,
  ghost,
  danger,
  sm,
  className,
  children,
  ...rest
}: Props) {
  const cls = [
    "mac-btn",
    primary && "mac-btn-primary",
    ghost && "mac-btn-ghost",
    danger && "mac-btn-danger",
    sm && "sm",
    className,
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <button className={cls} {...rest}>
      {children}
    </button>
  );
}
