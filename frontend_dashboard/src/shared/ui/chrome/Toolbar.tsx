/**
 * Toolbar: navegación atrás/adelante + segmented control de secciones
 * (Chats / Órdenes / Productos / ETA agent) + botón "Agentes" donde antes
 * estaba el buscador + toggles de sidebar/inspector.
 */

import { Icon } from "../Icon";

export type SectionKey = "chat" | "orders" | "upload" | "eta" | "agent";

interface Props {
  section: SectionKey;
  setSection: (s: SectionKey) => void;
  showSidebar: boolean;
  setShowSidebar: (v: boolean) => void;
  showInspector: boolean;
  setShowInspector: (v: boolean) => void;
}

const SECTIONS: { key: SectionKey; label: string; icon: () => React.ReactElement }[] = [
  { key: "chat",   label: "Chats",     icon: Icon.chat },
  { key: "orders", label: "Órdenes",   icon: Icon.workflow },
  { key: "upload", label: "Productos", icon: Icon.plus },
  { key: "eta",    label: "ETA agent", icon: Icon.notes },
];

export function Toolbar({
  section,
  setSection,
  showSidebar,
  setShowSidebar,
  showInspector,
  setShowInspector,
}: Props) {
  return (
    <div className="toolbar">
      <div className="tb-group">
        <button
          className={"tb-btn" + (showSidebar ? " active" : "")}
          title="Mostrar/ocultar barra lateral"
          onClick={() => setShowSidebar(!showSidebar)}
        >
          <Icon.sidebarL />
        </button>
        <button className="tb-btn" title="Atrás">
          <Icon.back />
        </button>
        <button className="tb-btn" title="Adelante">
          <Icon.fwd />
        </button>
      </div>

      <div className="tb-sep" />

      <div className="seg">
        {SECTIONS.map((s) => {
          const IconComp = s.icon;
          return (
            <button
              key={s.key}
              className={section === s.key ? "on" : ""}
              onClick={() => setSection(s.key)}
            >
              <IconComp />
              {s.label}
            </button>
          );
        })}
      </div>

      <div className="tb-spacer" />

      <button
        className={"tb-agents-btn" + (section === "agent" ? " on" : "")}
        onClick={() => setSection("agent")}
      >
        <Icon.wand />
        <span>Agentes</span>
      </button>

      <div className="tb-sep" />

      <div className="tb-group">
        <button
          className={"tb-btn" + (showInspector ? " active" : "")}
          title="Inspector"
          onClick={() => setShowInspector(!showInspector)}
        >
          <Icon.sidebarR />
        </button>
      </div>
    </div>
  );
}
