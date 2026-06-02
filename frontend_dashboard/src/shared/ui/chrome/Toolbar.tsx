/**
 * Toolbar — chrome macOS-style del shell.
 *
 *   ┌──────┬──── segmented sections ────┬──────┐
 *   │ ◄ ► │  [Chats] [Orders] [ETA] …   │  ⌘   │
 *   └──────┴────────────────────────────┴──────┘
 *
 * Post-auditoría (2026-05-16): el shell es 100 % data-driven. La lista de
 * sections viene del registry generado (`PLUGINS.flatMap(p => p.sections)`),
 * NO hardcoded. El Toolbar no conoce los ids de los plugins — solo recibe la
 * lista, el id de la sección activa y el setter. Agregar un plugin no requiere
 * tocar este archivo.
 *
 * El campo `icon` de cada section es un string que mapea a `Icon[name]`. Si el
 * plugin declara un icon que no existe en el set, fallback a `Icon.bot` (en
 * lugar de romper el render). Es preferible warning silencioso a crash
 * cuando un manifest cambia el nombre del icono y nadie regeneró el registry.
 */

import { Icon, type IconName } from "../Icon";

/**
 * Forma mínima de una section contribuida por un plugin. Mantiene paridad con
 * `SectionEntry` del registry generado, pero replicada aquí para no introducir
 * dependencia inversa (`shared/ui → app/plugin-registry.generated`).
 */
export interface ToolbarSection {
  key: string;
  label: string;
  icon?: string;
}

/**
 * Mantenido como alias laxo (`string`) por backward-compat con consumers que
 * importaban `SectionKey` cuando era una union literal. Hoy es solo `string`.
 */
export type SectionKey = string;

interface Props {
  /**
   * Sections a mostrar en el segmented control. Vienen del registry generado,
   * ya ordenadas por `order`. Si está vacío, el segmented queda colapsado.
   */
  sections: ToolbarSection[];
  /** Key de la section activa. */
  section: SectionKey;
  setSection: (s: SectionKey) => void;
  showSidebar: boolean;
  setShowSidebar: (v: boolean) => void;
  showInspector: boolean;
  setShowInspector: (v: boolean) => void;
}

/**
 * Devuelve un componente de icono dado su nombre. Si el nombre no matchea
 * ningún glyph registrado en `Icon`, devuelve `Icon.bot` como fallback visible
 * (mejor que romper el render). En dev, el warning queda en consola para que
 * sea fácil notar la inconsistencia entre el manifest y el icon set.
 */
function resolveIcon(name: string | undefined): () => React.ReactElement {
  if (name && (name as IconName) in Icon) {
    return Icon[name as IconName];
  }
  if (name) {
    // eslint-disable-next-line no-console
    console.warn(
      `[Toolbar] icon "${name}" not found in Icon set; using fallback "bot"`,
    );
  }
  return Icon.bot;
}

export function Toolbar({
  sections,
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

      <div className="seg" role="tablist">
        {sections.map((s) => {
          const IconComp = resolveIcon(s.icon);
          return (
            <button
              key={s.key}
              role="tab"
              aria-selected={section === s.key}
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
