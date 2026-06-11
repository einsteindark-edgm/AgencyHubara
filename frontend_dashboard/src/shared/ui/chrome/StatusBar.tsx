/**
 * Status bar inferior del shell.
 *
 * F2.3 (auditoría 2026-06-10): se eliminaron TODAS las métricas fake que
 * traía del prototipo ("Conectado · WhatsApp Cloud API", "247 conversaciones",
 * "Latencia 184 ms", teléfono hardcodeado) — un operador no puede decidir
 * sobre números inventados. Los indicadores REALES vuelven con F1/F2 fase B:
 * estado de conexión del event-stream (`connectionState`) + contadores del
 * endpoint platform/health. Hasta entonces, la barra solo muestra identidad
 * de la app y el hint de atajos.
 */

export function StatusBar() {
  return (
    <div className="statusbar" role="status">
      <span className="st">Hubara Dashboard</span>
      <span className="right">
        <kbd>⌘/</kbd> <span>atajos</span>
      </span>
    </div>
  );
}
