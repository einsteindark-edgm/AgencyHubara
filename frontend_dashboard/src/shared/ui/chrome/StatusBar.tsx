/**
 * Status bar inferior del shell.
 *
 * F2.3 (auditoría 2026-06-10): cero métricas fake — el prototipo mostraba
 * "247 conversaciones · 184 ms" hardcodeados. Hoy muestra SOLO datos reales:
 * el estado de la conexión del event-stream (se lo pasa el Dashboard por
 * prop — esta capa no importa de shared/api) + el hint de atajos. Los
 * contadores operativos llegan con el endpoint platform/health (F2 fase B).
 */

interface StatusBarProps {
  /** Estado del SSE multiplexado (`useEventStreamState()` en el Dashboard). */
  connection?: "connecting" | "open" | "reconnecting";
}

const CONNECTION_LABEL: Record<
  NonNullable<StatusBarProps["connection"]>,
  string
> = {
  connecting: "Conectando…",
  open: "Tiempo real conectado",
  reconnecting: "Reconectando…",
};

export function StatusBar({ connection }: StatusBarProps) {
  return (
    <div className="statusbar" role="status">
      <span className="st">
        {connection !== undefined && (
          <span
            className="d"
            style={{
              background:
                connection === "open"
                  ? "var(--ok, #5be07b)"
                  : connection === "reconnecting"
                    ? "var(--warn, #ffb44a)"
                    : "var(--fg-muted)",
            }}
          />
        )}
        {connection !== undefined
          ? CONNECTION_LABEL[connection]
          : "Hubara Dashboard"}
      </span>
      <span className="right">
        <kbd>⌘/</kbd> <span>atajos</span>
      </span>
    </div>
  );
}
