/**
 * KPI "Desde agentes de IA" (2026-09-15): conversaciones de WhatsApp que mandó
 * ChatGPT, Gemini… en la ventana del header, y cuántas terminaron en pedido.
 *
 * La tienda publica su catálogo para agentes (feed de Google, JSON-LD,
 * llms.txt) y el botón de WhatsApp lleva `via: <agente>` cuando la visita vino
 * de uno. Este número es el que dice si todo eso está vendiendo.
 *
 * Es de TODA la tienda, no de la campaña seleccionada: el título lo aclara para
 * que nadie lo lea como una métrica de la campaña. Si el endpoint falla se
 * oculta — un KPI secundario no puede tumbar la vista de campañas.
 */

import {
  useAgentReferrals,
  type AdsWindowParams,
} from "@plugins/ads/frontend/entities/ads-campaign";

function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5 rounded-md border border-line px-3 py-2">
      <span className="text-[11px] uppercase tracking-wide text-fg-muted">{label}</span>
      <span className="text-sm font-semibold text-fg">{value}</span>
    </div>
  );
}

export function AgentReferralsKpi({ params }: { params: AdsWindowParams }) {
  const { data, isError } = useAgentReferrals(params);
  if (isError || !data) return null;

  const conversion = data.total > 0 ? Math.round((data.withOrder / data.total) * 100) : 0;
  const active = data.bySource.filter((s) => s.count > 0);

  return (
    <section className="mx-4 my-2">
      <header className="px-1 pb-1.5">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-fg-muted">
          Desde agentes de IA · toda la tienda
        </h3>
      </header>
      {data.total === 0 ? (
        <p className="px-1 text-xs text-fg-muted">
          Todavía no llegó ninguna conversación desde ChatGPT, Gemini u otro agente
          en esta ventana.
        </p>
      ) : (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Kpi label="Conversaciones" value={String(data.total)} />
          <Kpi label="Con pedido" value={`${data.withOrder} · ${conversion}%`} />
          {active.map((s) => (
            <Kpi key={s.source} label={s.label} value={String(s.count)} />
          ))}
        </div>
      )}
    </section>
  );
}
