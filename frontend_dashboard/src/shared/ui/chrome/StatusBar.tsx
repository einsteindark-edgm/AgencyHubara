/**
 * Status bar inferior con indicadores de conexión, contadores, breadcrumb,
 * agente activo, latencia y atajos.
 */

import { Icon } from "../Icon";

export function StatusBar() {
  return (
    <div className="statusbar">
      <span className="st">
        <span className="d" />
        Conectado · WhatsApp Cloud API
      </span>
      <span className="sep" />
      <span>247 conversaciones · 38 sin asignar</span>
      <span className="sep" />
      <span className="crumb">
        Bandeja <Icon.chevR /> <b>Hoy</b> <Icon.chevR />{" "}
        <b>+57 312 567 1604</b>
      </span>
      <span className="right">
        <span className="ag">
          <i />
          Agente: remarketing
        </span>
        <span className="sep" />
        <span>
          Latencia{" "}
          <b style={{ color: "var(--fg-soft)", fontWeight: 500 }}>184 ms</b>
        </span>
        <span className="sep" />
        <kbd>⌘/</kbd> <span>atajos</span>
      </span>
    </div>
  );
}
