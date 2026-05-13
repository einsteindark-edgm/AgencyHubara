/**
 * Barra de título estilo macOS con semáforos (rojo/amarillo/verde),
 * título centrado y contador de agentes activos a la derecha.
 */

export function TitleBar() {
  return (
    <div className="titlebar">
      <div className="lights">
        <span className="light r" />
        <span className="light y" />
        <span className="light g" />
      </div>
      <div className="title">
        Agency <span className="sep">—</span> Conversaciones{" "}
        <span className="sep">·</span> +57 312 567 1604
      </div>
      <div className="right-side">
        <span style={{ fontSize: 11, color: "var(--fg-faint)" }}>
          3 agentes activos
        </span>
      </div>
    </div>
  );
}
