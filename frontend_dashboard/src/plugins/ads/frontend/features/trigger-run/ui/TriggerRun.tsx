/**
 * `TriggerRun` — selector de agente + textarea JSON pre-cargado + botón Run.
 *
 * Consume la entity (`useAgents` para el menú, `useTriggerRun` para el disparo)
 * y el hook de UI-state `useTriggerRunForm` (selección + draft + validez). Al
 * disparar con éxito, llama `onRunStarted(runId)` — la única salida hacia el
 * page, que setea ese run como selección activa para montar la vista en vivo.
 *
 * Estilado con utilidades Tailwind inline sobre los tokens del @theme (patrón
 * de los plugins post-migración, p.ej. agents_admin) — cero CSS global.
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

import { formatExampleInput, useTriggerRunForm } from "../model/useTriggerRunForm";

interface Props {
  onRunStarted: (runId: string) => void;
}

export function TriggerRun({ onRunStarted }: Props) {
  const { data: agents, isLoading, isError } = useAgents();
  const form = useTriggerRunForm(agents);
  const trigger = useTriggerRun();

  // Datos REALES de Meta para pre-cargar el análisis (solo si conectado y no expirado).
  const { data: conn } = useMetaConnection();
  const metaReady = Boolean(conn?.connected && !conn.expired);
  const liveInput = useMetaAnalysisInput(metaReady);

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
          <div className="text-sm font-semibold">Disparar un agente</div>
          <div className="text-xs text-fg-muted">
            Elegí un agente, ajustá el JSON de entrada y corré el análisis.
          </div>
        </div>
      </header>

      <label className="flex flex-col gap-1.5">
        <span className="text-xs font-medium text-fg-muted">Agente</span>
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
      </label>

      <label className="flex flex-col gap-1.5">
        <span className="flex items-center justify-between gap-3 text-xs font-medium text-fg-muted">
          Entrada (JSON)
          <span className="flex items-center gap-3">
            {metaReady ? (
              <button
                type="button"
                className="text-xs font-semibold text-accent hover:underline disabled:opacity-50"
                onClick={() => form.setDraft(formatExampleInput(liveInput.data))}
                disabled={liveInput.isLoading || liveInput.data == null}
                title="Carga tus campañas reales de Meta (datos de Graph) como entrada del análisis"
              >
                {liveInput.isLoading ? "Cargando Meta…" : "Cargar datos reales de Meta"}
              </button>
            ) : null}
            <button
              type="button"
              className="text-xs text-accent hover:underline disabled:opacity-50"
              onClick={form.resetToExample}
              disabled={!form.agentId}
            >
              Restaurar ejemplo
            </button>
          </span>
        </span>
        <textarea
          className="min-h-[16rem] rounded-md border border-line bg-canvas px-3 py-2 font-mono text-xs text-fg outline-none focus:border-accent"
          value={form.draft}
          onChange={(e) => form.setDraft(e.target.value)}
          spellCheck={false}
          rows={12}
          placeholder="{}"
        />
        {!form.parsed.ok && (
          <span className="text-xs text-danger">
            JSON inválido: {form.parsed.error}
          </span>
        )}
      </label>

      <div className="flex items-center gap-3">
        <button
          type="button"
          className="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-accent-fg hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
          onClick={onRun}
          disabled={!form.canRun || trigger.isPending}
        >
          {trigger.isPending ? "Disparando…" : "Run"}
        </button>
        {trigger.isError && (
          <span className="text-xs text-danger">
            No se pudo disparar el run. Revisá el backend y reintentá.
          </span>
        )}
      </div>
    </section>
  );
}
