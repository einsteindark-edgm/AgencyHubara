/**
 * Panel "Costos de WhatsApp" del scope activo (campaña / segmento / anuncio).
 *
 * Pedido del operador (2026-09-18): ver en el panel principal cuánto cuesta en
 * WhatsApp la campaña, POR CATEGORÍA de Meta, y separarlo con una línea
 * divisoria rotulada para leerlo de un vistazo. NO es el gasto del anuncio
 * (COP, bloque "Meta · métricas"): es lo que Meta cobra por los mensajes —
 * por eso va en US$ y en su propia sección.
 *
 * El dato sale de `episode.cost_summary` (webhook `message_status` de Meta +
 * tarjeta de tarifas), sumado por el backend de ads. `null` = sin dato → no se
 * pinta nada (≠ "costó cero", que sí se pinta).
 */
import {
  waCostBreakdown,
  type AdsCampaign,
  type WaCostRow,
} from "@plugins/ads/frontend/entities/ads-campaign";
import { fmtN, fmtUsdMicros } from "@plugins/ads/frontend/lib/format";

/** Clases ESTÁTICAS por tono (Tailwind no genera `text-${tone}` dinámico).
 *  Todos son tokens reales del `@theme` de index.css. */
const TONE_DOT: Record<WaCostRow["tone"], string> = {
  violet: "bg-violet",
  info: "bg-info",
  ok: "bg-ok",
  warn: "bg-warn",
  neutral: "bg-neutral",
};

function messages(n: number): string {
  return `${fmtN(n)} ${n === 1 ? "mensaje" : "mensajes"}`;
}

export function WhatsappCosts({ campaign }: { campaign: AdsCampaign }) {
  const total = campaign.waCostUsdMicros;
  if (total === null || total === undefined) return null;

  const rows = waCostBreakdown(campaign.waCostByCategory);
  const totalCount = rows.reduce((sum, row) => sum + row.count, 0);
  const pending = campaign.waMsgsPending ?? 0;

  return (
    <section className="mx-4 my-3" aria-label="Costos de WhatsApp">
      {/* Línea divisoria rotulada: separa visualmente estos costos del resto. */}
      <div
        role="separator"
        aria-label="Costos de WhatsApp"
        className="flex items-center gap-3 pb-2"
      >
        <span className="h-px flex-1 bg-line-strong" />
        <span className="text-xs font-semibold uppercase tracking-wide text-fg-muted">
          Costos de WhatsApp
        </span>
        <span className="h-px flex-1 bg-line-strong" />
      </div>

      <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
        <div
          data-testid="wa-cost-total"
          className="flex flex-col gap-0.5 rounded-md border border-line-strong px-3 py-2"
        >
          <span className="text-[11px] uppercase tracking-wide text-fg-muted">Total</span>
          <span className="text-sm font-semibold text-fg">{fmtUsdMicros(total)}</span>
          <span className="text-[11px] text-fg-faint">{messages(totalCount)}</span>
        </div>

        {rows.map((row) => (
          <div
            key={row.key}
            data-testid={`wa-cost-${row.key}`}
            className="flex flex-col gap-0.5 rounded-md border border-line px-3 py-2"
          >
            <span className="flex items-center gap-1.5 text-[11px] uppercase tracking-wide text-fg-muted">
              <span className={"h-1.5 w-1.5 rounded-full " + TONE_DOT[row.tone]} />
              {row.label}
            </span>
            <span className="text-sm font-semibold text-fg">
              {row.usdMicros > 0 ? fmtUsdMicros(row.usdMicros) : "Gratis"}
            </span>
            <span className="text-[11px] text-fg-faint">{messages(row.count)}</span>
          </div>
        ))}
      </div>

      {pending > 0 && (
        <p className="px-1 pt-1.5 text-[11px] text-fg-faint">
          {messages(pending)} sin precio todavía — Meta lo informa al entregar; el
          total aún no {pending === 1 ? "lo" : "los"} incluye.
        </p>
      )}
    </section>
  );
}
