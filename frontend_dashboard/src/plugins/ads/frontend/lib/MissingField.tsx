/**
 * `MissingField` — placeholder visual para datos que el backend aún no
 * puede derivar (spend, revenue, status Meta Ads, name de cliente, etc.).
 *
 * Por defecto muestra `"—"` muted con `title` (tooltip nativo) explicando
 * que es un dato pendiente. Si `withIcon` está activo, agrega el icono
 * `dataPending` (reloj minimalista) — usalo en cards o lugares con más
 * aire visual; en celdas densas de tabla preferí sin icono.
 *
 * Plugin-local: NO se promueve a `shared/ui` porque hoy es exclusivo del
 * plugin `ads`. Si otros plugins lo necesitan, se eleva (regla §11 FSD).
 */

import { Icon } from "@/shared/ui";

interface Props {
  withIcon?: boolean;
  /** Texto del tooltip — default explica el origen del marker. */
  title?: string;
}

const DEFAULT_TITLE =
  "Dato pendiente — pendiente de integración upstream (Meta Ads API / CRM / clasificador)";

export function MissingField({ withIcon = false, title = DEFAULT_TITLE }: Props) {
  if (withIcon) {
    return (
      <span
        title={title}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
          color: "var(--fg-faint)",
          opacity: 0.75,
        }}
      >
        <span style={{ display: "inline-flex", transform: "scale(0.75)" }}>
          <Icon.dataPending />
        </span>
        <span>—</span>
      </span>
    );
  }
  return (
    <span
      title={title}
      style={{ color: "var(--fg-faint)", cursor: "help" }}
    >
      —
    </span>
  );
}
