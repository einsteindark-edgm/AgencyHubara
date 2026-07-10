import React, { useMemo, useState } from "react";
import { PALETTE_MIME } from "../canvas/Canvas";

export interface PaletteItem {
  id: string;
  kind: string;
  label: string;
  /** ya visible en el scope actual (en workflow: ya conectado al flujo). */
  inScope: boolean;
}

export interface PaletteProps {
  items: PaletteItem[];
}

/**
 * Palette de edit mode (§F5): el catálogo de agentes/tools arrastrable al
 * canvas. Soltar un item SOBRE un agente lo conecta (validate→confirm→mutate,
 * el mismo gate que el drag-connect). Los items fuera del scope actual van
 * primero — son los candidatos naturales a "traer al flujo".
 */
export function Palette({ items }: PaletteProps): React.ReactElement {
  // Cerrada por default (F10): el canvas usa todo el ancho; ➕ la abre.
  // Vive DENTRO del canvas a propósito — el drag HTML5 no cruza webviews
  // (iframes aislados), así que un panel nativo no puede ser drag-source.
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");

  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    const matched = q ? items.filter((i) => i.label.toLowerCase().includes(q) || i.id.toLowerCase().includes(q)) : items;
    // fuera-del-scope primero, después por kind (agentes antes que tools), después alfabético
    return [...matched].sort(
      (a, b) =>
        Number(a.inScope) - Number(b.inScope) || a.kind.localeCompare(b.kind) || a.label.localeCompare(b.label),
    );
  }, [items, filter]);

  if (!open) {
    return (
      <button type="button" className="palette-toggle" title="Abrir catálogo arrastrable" onClick={() => setOpen(true)}>
        ➕
      </button>
    );
  }

  return (
    <div className="palette">
      <div className="palette-header">
        <span className="palette-title">➕ Catálogo</span>
        <button type="button" className="trace-stop" onClick={() => setOpen(false)}>
          ‹
        </button>
      </div>
      <input
        className="palette-filter"
        type="text"
        placeholder="filtrar…"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
      />
      <div className="palette-hint">arrastrá un item SOBRE un agente del canvas para conectarlo</div>
      <div className="palette-list">
        {shown.map((item) => (
          <div
            key={item.id}
            className={`palette-item kind-${item.kind}${item.inScope ? " in-scope" : ""}`}
            draggable
            title={item.inScope ? `${item.id} (ya en este scope)` : item.id}
            onDragStart={(ev) => {
              ev.dataTransfer.setData(PALETTE_MIME, JSON.stringify({ id: item.id, kind: item.kind, label: item.label }));
              ev.dataTransfer.effectAllowed = "link";
            }}
          >
            <span className="palette-kind">{item.kind}</span>
            <span className="palette-label">{item.label}</span>
            {item.inScope && <span className="palette-in-scope">●</span>}
          </div>
        ))}
        {shown.length === 0 && <div className="palette-empty">sin resultados</div>}
      </div>
    </div>
  );
}
