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

import type { ComponentType } from "react";

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
  /**
   * Glifos aportados por plugins (F7 — `PLUGIN_ICONS` del registry generado).
   * Inyectados como prop por el shell para que `shared/ui` NO importe del
   * registry (`app/`) — la dirección FSD se preserva. Resolución: primero
   * contribuciones del plugin, después el set base `Icon`, después fallback.
   */
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  pluginIcons?: Record<string, ComponentType<any>>;
}

/**
 * Devuelve un componente de icono dado su nombre. Orden de resolución:
 * contribución del plugin (`pluginIcons`, F7) → set base `Icon` → fallback
 * `Icon.bot` visible + warning (mejor que romper el render cuando un manifest
 * cambia el nombre del icono y nadie regeneró el registry).
 */
function resolveIcon(
  name: string | undefined,
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  pluginIcons?: Record<string, ComponentType<any>>,
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
): ComponentType<any> {
  if (name && pluginIcons && name in pluginIcons) {
    return pluginIcons[name];
  }
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
  pluginIcons,
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
          const IconComp = resolveIcon(s.icon, pluginIcons);
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
