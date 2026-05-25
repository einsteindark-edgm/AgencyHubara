/**
 * `MissingData` — marker visual para campos cuyo dato no llega del backend
 * todavía. Aparece en el inspector cuando `data_completeness_missing[]`
 * incluye el slot correspondiente.
 *
 * Diseño: chip pequeño con ícono + label "Pendiente integración". Hover
 * (title attribute) muestra el detalle de qué integración falta.
 *
 * Anti-pattern que evita: dejar valores hardcoded ("Sofía", "VIP",
 * "$1.248.500") cuando el backend no los puede derivar todavía — eso
 * engaña al operador que cree que tiene datos reales.
 */

import { Icon } from "./Icon";

interface Props {
  /** Razón concreta — aparece en el tooltip (`title`). */
  reason?: string;
  /** Texto corto del chip. Por defecto "Pendiente integración". */
  label?: string;
  /** Variante visual: 'inline' (chip pequeño) o 'block' (placeholder grande). */
  variant?: "inline" | "block";
}

export function MissingData({
  reason,
  label = "Pendiente integración",
  variant = "inline",
}: Props) {
  if (variant === "block") {
    return (
      <div
        className="missing-data-block"
        title={reason}
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 6,
          padding: "16px 12px",
          borderRadius: 8,
          background: "rgba(255,255,255,0.03)",
          border: "1px dashed rgba(255,255,255,0.12)",
          color: "var(--fg-muted)",
          fontSize: 12,
          textAlign: "center",
        }}
      >
        <Icon.info />
        <div style={{ lineHeight: 1.4 }}>
          {label}
          {reason && (
            <div style={{ fontSize: 10, opacity: 0.7, marginTop: 4 }}>
              {reason}
            </div>
          )}
        </div>
      </div>
    );
  }
  return (
    <span
      className="missing-data-chip"
      title={reason ?? label}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "2px 7px",
        fontSize: 10,
        borderRadius: 999,
        background: "rgba(255,180,74,0.10)",
        color: "#ffb44a",
        border: "1px dashed rgba(255,180,74,0.35)",
        cursor: "help",
        lineHeight: 1.4,
      }}
    >
      <Icon.info />
      {label}
    </span>
  );
}
