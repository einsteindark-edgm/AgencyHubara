/**
 * `TriggerRun` — selector de agente (con descripción de QUÉ análisis hace) +
 * botón Analizar. Rediseño 2026-07-09: el JSON dejó de ser el protagonista —
 * con Meta conectado la entrada se precarga con los DATOS REALES del
 * analysis-input (lo que confundía era un ejemplo hardcodeado) y el JSON queda
 * colapsado en un <details> para quien quiera inspeccionarlo/editarlo.
 *
 * Consume la entity (`useAgents` para el menú, `useTriggerRun` para el disparo,
 * `useMetaAnalysisInput` para los datos reales) y el hook de UI-state
 * `useTriggerRunForm` (selección + draft + fuente + validez). Al disparar con
 * éxito, llama `onRunStarted(runId)` — la única salida hacia el page.
 */
import { Icon } from "@/shared/ui";

import {
  useAgents,
  useTriggerRun,
} from "@plugins/ads/frontend/entities/ad-analysis-run";
import {
  useMetaAnalysisInput,
  useMetaConnection,
} from "@plugins/ads/frontend/entities/meta-connection";

import { useTriggerRunForm } from "../model/useTriggerRunForm";

interface Props {
  onRunStarted: (runId: string) => void;
}

export function TriggerRun({ onRunStarted }: Props) {
  const { data: agents, isLoading, isError } = useAgents();
  const trigger = useTriggerRun();

  // Datos REALES de Meta para el análisis (solo si conectado y no expirado).
  const { data: conn } = useMetaConnection();
  const metaReady = Boolean(conn?.connected && !conn.expired);
  const liveInput = useMetaAnalysisInput(metaReady);
  const live = metaReady ? liveInput.data : undefined;

  const form = useTriggerRunForm(agents, live);
  const selectedAgent = agents?.find((a) => a.id === form.agentId) ?? null;
  const liveLoading = metaReady && liveInput.isLoading;

  const onRun = () => {
    if (!form.canRun || !form.agentId) return;
    trigger.mutate(
      { agent: form.agentId, input: form.parsed.value },
      { onSuccess: (runId) => onRunStarted(runId) },
    );
  };

  return (
    <section className="flex w-full max-w-xl flex-col gap-4 p-4 text-fg">
      <header className="flex items-center gap-3">
        <span className="text-accent">
          <Icon.bot />
        </span>
        <div>
          <div className="text-sm font-semibold">Correr un análisis</div>
          <div className="text-xs text-fg-muted">
            Elegí el tipo de análisis y dale a Analizar — los datos se cargan solos.
          </div>
        </div>
      </header>

      <label className="flex flex-col gap-1.5">
        <span className="text-xs font-medium text-fg-muted">Tipo de análisis</span>
        <select
          className="rounded-md border border-line bg-canvas px-3 py-2 text-sm text-fg outline-none focus:border-accent disabled:opacity-50"
          value={form.agentId ?? ""}
          onChange={(e) => form.selectAgent(e.target.value)}
          disabled={isLoading || isError || !agents?.length}
        >
          {isLoading && <option value="">Cargando agentes…</option>}
          {isError && <option value="">Error al cargar agentes</option>}
          {!isLoading && !isError && !agents?.length && (
            <option value="">No hay agentes disponibles</option>
          )}
          {agents?.map((a) => (
            <option key={a.id} value={a.id}>
              {a.label}
            </option>
          ))}
        </select>
        {selectedAgent?.description && (
          <p className="text-xs leading-relaxed text-fg-muted">
            {selectedAgent.description}
          </p>
        )}
      </label>

      {/* Qué se va a enviar — etiqueta honesta según la fuente del draft. */}
      <div className="flex flex-col gap-1.5 rounded-md border border-line bg-canvas p-3">
        <div className="flex items-center gap-2 text-xs">
          {form.source === "meta" && (
            <span className="font-medium text-ok" style={{ color: "var(--ok, #30a46c)" }}>
              ✓ Se enviarán tus datos reales de Meta (Graph, últimos 14 días)
            </span>
          )}
          {form.source === "edited" && (
            <span className="font-medium text-fg-muted">
              Entrada editada a mano — se enviará tal cual la dejaste
            </span>
          )}
          {form.source === "example" &&
            (liveLoading ? (
              <span className="font-medium text-fg-muted">Cargando datos de Meta…</span>
            ) : (
              <span className="font-medium text-warn" style={{ color: "var(--warn, #f5a524)" }}>
                Meta no conectado — se enviará un JSON de ejemplo (no son tus datos)
              </span>
            ))}
        </div>

        <details className="text-xs">
          <summary className="cursor-pointer select-none text-fg-muted hover:text-fg">
            Ver / editar el JSON que se envía
          </summary>
          <div className="mt-2 flex flex-col gap-1.5">
            <textarea
              className="min-h-[14rem] rounded-md border border-line bg-canvas px-3 py-2 font-mono text-xs text-fg outline-none focus:border-accent"
              value={form.draft}
              onChange={(e) => form.setDraft(e.target.value)}
              spellCheck={false}
              rows={12}
              placeholder="{}"
            />
            <div className="flex items-center gap-3">
              {metaReady ? (
                <button
                  type="button"
                  className="text-xs font-semibold text-accent hover:underline disabled:opacity-50"
                  onClick={form.resetToLive}
                  disabled={liveInput.isLoading || liveInput.data == null}
                  title="Vuelve a cargar tus campañas reales de Meta como entrada del análisis"
                >
                  {liveInput.isLoading ? "Cargando Meta…" : "Restaurar datos de Meta"}
                </button>
              ) : (
                <button
                  type="button"
                  className="text-xs text-accent hover:underline disabled:opacity-50"
                  onClick={form.resetToExample}
                  disabled={!form.agentId}
                >
                  Restaurar ejemplo
                </button>
              )}
            </div>
            {!form.parsed.ok && (
              <span className="text-xs text-danger">
                JSON inválido: {form.parsed.error}
              </span>
            )}
          </div>
        </details>
      </div>

      <div className="flex items-center gap-3">
        <button
          type="button"
          className="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-accent-fg hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
          onClick={onRun}
          disabled={!form.canRun || trigger.isPending || liveLoading}
        >
          {trigger.isPending
            ? "Disparando…"
            : liveLoading
              ? "Cargando datos…"
              : "Analizar"}
        </button>
        {!form.parsed.ok && (
          <span className="text-xs text-danger">Corregí el JSON para poder analizar.</span>
        )}
        {trigger.isError && (
          <span className="text-xs text-danger">
            No se pudo disparar el análisis. Revisá el backend y reintentá.
          </span>
        )}
      </div>
    </section>
  );
}
