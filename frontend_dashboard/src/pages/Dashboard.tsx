/**
 * Página `Dashboard`: shell macOS-style. 100 % data-driven post-auditoría
 * (2026-05-16).
 *
 * Responsabilidades de esta capa:
 *
 *   - State de coordinación cross-feature (sección activa, selecciones por
 *     plugin, toggles de sidebar/inspector).
 *   - Composición JSX del shell + carga dinámica del Page de cada plugin
 *     desde el registry auto-generado (`PLUGINS`).
 *
 * NO va acá:
 *   - Data fetching de dominio (vive en entities/<x>/api.ts).
 *   - UI específica de una feature (vive en plugins/<id>/frontend/).
 *   - Lista de plugins hardcoded — la fuente de verdad es PLUGINS.
 *
 * Notas de diseño:
 *
 *   - El Toolbar recibe `sections` derivadas de `PLUGINS.flatMap` —
 *     agregar/quitar un plugin no requiere editar este archivo NI el Toolbar.
 *
 *   - Los Pages NO reciben props: el contrato shell↔plugin es el contexto
 *     genérico `PluginHostProvider` (chrome + mapa de selección). Cada plugin
 *     lee lo suyo con `usePluginHost()` / `useSelection("<plugin-id>",
 *     fallback)` — el fallback lo declara el plugin, no el shell.
 */

import { Suspense, useCallback, useMemo, useState } from "react";

import { useEventStreamState } from "@/shared/api";
import { ErrorBoundary, StatusBar, Toolbar } from "@/shared/ui";
import { IS_DESKTOP, PluginHostProvider } from "@/shared/lib";

import { PLUGINS, PLUGIN_ICONS } from "@/app/plugin-registry.generated";

export function Dashboard() {
  // ── Derivar lista de sections del registry ───────────────────────────
  // `PLUGINS` ya está ordenado por id; cada plugin contribuye N sections.
  // Re-ordenamos por `order` para respetar la prioridad declarada en el
  // manifest. Una section sin order se trata como 0.
  const sections = useMemo(() => {
    const flat = PLUGINS.flatMap((p) => p.sections ?? []);
    return [...flat].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
  }, []);

  // Map sectionKey → Page del plugin que la contribuyó. Si dos plugins
  // contribuyen la misma key (no debería ocurrir; `plugins-sync.ts` lo
  // detecta), gana el último — el shell no resuelve conflictos.
  const pageByKey = useMemo(() => {
    const out = new Map<string, (typeof PLUGINS)[number]["Page"]>();
    for (const p of PLUGINS) {
      for (const s of p.sections ?? []) {
        out.set(s.key, p.Page);
      }
    }
    return out;
  }, []);

  // Default section: la primera de la lista ordenada. Si el registry está
  // vacío (ningún plugin habilitado), `section` queda `""` y el shell muestra
  // marco vacío — degradación graceful.
  const [section, setSection] = useState<string>(() => sections[0]?.key ?? "");

  const [showSidebar, setShowSidebar] = useState(true);
  const [showInspector, setShowInspector] = useState(true);

  // ── PluginHost (F7 — INV-1) ────────────────────────────────────────────
  // El contrato shell↔plugin ya no es un "bandejón" de props hardcodeado
  // (12 props × 5 plugins): es el contexto GENÉRICO `PluginHostProvider`
  // (shared/lib/plugin-host). La selección cross-sección vive en un mapa
  // clave→id; cada plugin elige su clave vía `useSelection("<plugin-id>")`.
  // Agregar un plugin con selección propia NO toca este archivo.
  //
  // El mapa arranca VACÍO: el default de cada selección lo declara el propio
  // plugin como fallback de `useSelection` (F0.7 — el shell no conoce ids de
  // dominio; antes sembraba "#1247"/"sales" acá, acople shell→plugin).
  const [selection, setSelectionMap] = useState<Record<string, string | null>>(
    {},
  );
  const setSelection = useCallback((key: string, id: string | null) => {
    setSelectionMap((prev) => (prev[key] === id ? prev : { ...prev, [key]: id }));
  }, []);
  const host = useMemo(
    () => ({ showSidebar, showInspector, selection, setSelection }),
    [showSidebar, showInspector, selection, setSelection],
  );

  // F4 (INV-1): el stream SSE de sesiones era una suscripción del SHELL a la
  // entity de chats — acople shell→dominio. Ahora lo monta el Page de chats
  // (dueño del stream); el shell quedó 100% libre de imports de dominio.
  // (El estado de CONEXIÓN del stream compartido sí es del shell — es infra
  // genérica de shared/api, no dominio.)
  const connection = useEventStreamState();
  const ActivePage = pageByKey.get(section);

  return (
    <div className={"stage" + (IS_DESKTOP ? "" : " is-web")}>
      <div className="win">
        <Toolbar
          sections={sections}
          section={section}
          setSection={setSection}
          showSidebar={showSidebar}
          setShowSidebar={setShowSidebar}
          showInspector={showInspector}
          setShowInspector={setShowInspector}
          pluginIcons={PLUGIN_ICONS}
        />

        <div className="body">
          {ActivePage && (
            // ErrorBoundary FUERA del Suspense: atrapa tanto crashes de render
            // del plugin como fallos de carga del chunk lazy. `resetKey` lo
            // resetea al cambiar de sección (el boundary no se remonta — el
            // key vive en el Suspense interior).
            <ErrorBoundary scope={section} resetKey={section}>
              <Suspense key={section} fallback={<SectionLoading />}>
                <PluginHostProvider value={host}>
                  <ActivePage />
                </PluginHostProvider>
              </Suspense>
            </ErrorBoundary>
          )}
        </div>

        <StatusBar connection={connection} />
      </div>
    </div>
  );
}

/**
 * Fallback del Suspense mientras carga el chunk lazy del plugin. Antes era
 * `null` → flash en blanco en cada cambio de sección (F2.2). Neutro y
 * token-based; el skeleton 3-columnas llega con la fase de design system.
 */
function SectionLoading() {
  return (
    <div
      style={{
        flex: 1,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: 12,
        color: "var(--fg-muted)",
      }}
    >
      Cargando sección…
    </div>
  );
}

/* Post-auditoría 2026-05-16: el shell es totalmente data-driven.
 *
 * Agregar un plugin nuevo NO requiere tocar este archivo:
 *
 *   1. Crear `frontend_dashboard/src/plugins/<id>/plugin.yaml` declarando
 *      `frontend.contributes.sections: [{ key, label, order, icon }]`.
 *   2. Crear `frontend_dashboard/src/plugins/<id>/frontend/index.ts` que
 *      exporte `default` como el componente Page.
 *   3. `npm run plugins:sync` regenera el registry, el shell descubre la
 *      nueva section y la muestra en el Toolbar.
 *
 * Plugins migrados (orden de PR original):
 *   - ChatsSection  → @plugins/chats/frontend          (PR2)
 *   - AgentsSection → @plugins/agents_admin/frontend   (PR4)
 *   - CatalogSyncSection → @plugins/catalog/frontend   (PR5)
 *   - EtaSection    → @plugins/eta/frontend            (PR6)
 *   - OrdersSection → @plugins/orders/frontend         (PR7)
 */
