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
 *   - Cada plugin recibe el mismo "envelope" de props vía `pluginProps`
 *     (showSidebar, showInspector, selectedXxx, setSelectedXxx + el state
 *     selections compartido). Los plugins desestructuran lo que necesitan; los
 *     que no usan algo lo ignoran. Esto es una decisión pragmática mientras
 *     el contrato de props del Page no está formalizado (PR pendiente).
 */

import { Suspense, useMemo, useState } from "react";

import { StatusBar, TitleBar, Toolbar } from "@/shared/ui";
import { IS_DESKTOP } from "@/shared/lib";

import { useSessionsStream } from "@/entities/chat";

import { PLUGINS } from "@/app/plugin-registry.generated";

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

  // ── Selections per-plugin ──────────────────────────────────────────────
  // Cross-plugin state vive aquí porque sobrevive cambios de section.
  // Cuando el contrato de props del Page se formalice, esto se moverá a
  // un context o se delegará al state interno del plugin.
  const [selectedChatId, setSelectedChatId] = useState<string | null>(null);
  const [selectedOrderId, setSelectedOrderId] = useState<string | null>("#1247");
  const [selectedTrackedId, setSelectedTrackedId] = useState<string | null>(null);
  const [selectedJobId, setSelectedJobId] = useState<string>("j-now");
  const [selectedAgentId, setSelectedAgentId] = useState<string>("sales");

  // Stream SSE de sesiones: se monta UNA vez aquí — empuja el snapshot al
  // cache de TanStack Query para todas las features de Chats. Las demás
  // secciones del diseño viven con datos mock locales y no dependen del
  // stream.
  useSessionsStream();

  const ActivePage = pageByKey.get(section);

  // ── pluginProps — contrato (pragmático v1) entre shell y plugin Page ──
  //
  // CONTRATO:
  //   El shell entrega un "bandejón" con TODO el state cross-plugin: toggles
  //   visuales (showSidebar/showInspector) + las selecciones de cada feature
  //   (selectedXxxId/setSelectedXxxId). Cada plugin Page desestructura las
  //   props que necesita e ignora el resto.
  //
  // TRADE-OFF aceptado:
  //   - PRO: agregar un nuevo prop solo requiere editar acá una vez; cada
  //     Page que lo necesite ya lo recibe.
  //   - CONTRA: el tipo de la prop es `any` (ver registry generado), así que
  //     TypeScript NO te avisa si un plugin pide algo que el shell no provee.
  //
  // CUÁNDO MIGRAR:
  //   Cuando haya 6+ plugins o cuando un bug nuevo aparezca por "plugin pide
  //   prop X pero el shell pasa prop Y", introducir un Context provider y
  //   firmar el contrato con generics en el registry. Hoy (5 plugins) este
  //   patrón es la solución más simple que funciona.
  const pluginProps = {
    showSidebar,
    showInspector,
    selectedChatId,
    setSelectedChatId,
    selectedOrderId,
    setSelectedOrderId,
    selectedTrackedId,
    setSelectedTrackedId,
    selectedJobId,
    setSelectedJobId,
    selectedAgentId,
    setSelectedAgentId,
  };

  return (
    <div className={"stage" + (IS_DESKTOP ? "" : " is-web")}>
      <div className="win">
        {IS_DESKTOP && <TitleBar />}
        <Toolbar
          sections={sections}
          section={section}
          setSection={setSection}
          showSidebar={showSidebar}
          setShowSidebar={setShowSidebar}
          showInspector={showInspector}
          setShowInspector={setShowInspector}
        />

        <div className="body">
          {ActivePage && (
            <Suspense key={section} fallback={null}>
              <ActivePage {...pluginProps} />
            </Suspense>
          )}
        </div>

        <StatusBar />
      </div>
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
 *   - UploadSection → @plugins/catalog/frontend        (PR5)
 *   - EtaSection    → @plugins/eta/frontend            (PR6)
 *   - OrdersSection → @plugins/orders/frontend         (PR7)
 */
