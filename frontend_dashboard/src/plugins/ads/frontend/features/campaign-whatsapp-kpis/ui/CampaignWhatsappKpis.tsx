/**
 * "WhatsApp · envío de la campaña" (pedido 2026-09-25): una campaña de
 * WhatsApp de la sección Marketing, con las estadísticas de su envío como una
 * campaña de Meta — embudo enviados → entregados → leídos → respondieron →
 * ventas, gasto real, costo por respuesta y por venta, ROAS, fallidos y bajas.
 *
 * Los datos vienen del listado de campañas (`whatsappSend`, solo filas
 * `hubara_campaign`) y responden a la ventana de fecha del header. Derivados
 * en render (regla 5). El gasto es el precio de Meta de cada mensaje (USD);
 * el ROAS lo compara con las ventas en COP a la tasa aproximada del dashboard.
 */

import type { AdsCampaign } from "@plugins/ads/frontend/entities/ads-campaign";
import {
  USD_TO_COP_APPROX,
  fmtMoney,
  fmtN,
  fmtPct,
  fmtUsdMicros,
} from "@plugins/ads/frontend/lib/format";

function Kpi({ testId, label, value, hint }: {
  testId: string;
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div data-testid={testId} className="flex flex-col gap-0.5 rounded-md border border-line px-3 py-2">
      <span className="text-[11px] uppercase tracking-wide text-fg-muted">{label}</span>
      <span className="text-sm font-semibold text-fg">{value}</span>
      {hint && <span className="text-[11px] text-fg-muted">{hint}</span>}
    </div>
  );
}

/** "7 mensajes" / "1 mensaje". */
function plural(n: number, one: string, many: string): string {
  return `${fmtN(n)} ${n === 1 ? one : many}`;
}

export function CampaignWhatsappKpis({ campaign }: { campaign: AdsCampaign }) {
  const send = campaign.whatsappSend;
  if (campaign.sourceType !== "hubara_campaign" || !send) return null;

  const sales = campaign.conversations?.ganado ?? 0;
  const cost = send.costUsdMicros;
  const costCop = cost !== null ? (cost / 1_000_000) * USD_TO_COP_APPROX : null;
  const perReply = cost !== null && send.replied > 0 ? cost / send.replied : null;
  const perSale = cost !== null && sales > 0 ? cost / sales : null;
  const roas =
    costCop !== null && costCop > 0 && campaign.revenue !== null
      ? campaign.revenue / costCop
      : null;

  // Cada etapa con la parte que llegó sobre su base: la lectura se mide sobre
  // lo entregado, y la respuesta también (quien apaga las confirmaciones de
  // lectura igual puede responder).
  const stages = [
    { key: "sent", label: "Enviados", value: send.sent, base: null, of: "" },
    { key: "delivered", label: "Entregados", value: send.delivered, base: send.sent, of: "de los enviados" },
    { key: "read", label: "Leídos", value: send.read, base: send.delivered, of: "de los entregados" },
    { key: "replied", label: "Respondieron", value: send.replied, base: send.delivered, of: "de los entregados" },
    { key: "sales", label: "Ventas", value: sales, base: send.replied, of: "de los que respondieron" },
  ];

  return (
    <section className="mx-4 my-2" aria-label="WhatsApp · envío de la campaña">
      <header className="flex items-center justify-between px-1 pb-1.5">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-fg-muted">
          WhatsApp · envío de la campaña
        </h3>
      </header>
      <ol aria-label="Embudo del envío" className="mb-2 grid grid-cols-5 gap-2">
        {stages.map((stage) => (
          <li
            key={stage.key}
            data-stage={stage.key}
            data-testid={`wa-send-${stage.key}`}
            className="flex flex-col gap-0.5 rounded-md bg-ok-soft px-3 py-2"
          >
            <span className="text-[11px] uppercase tracking-wide text-fg-muted">{stage.label}</span>
            <span className="text-base font-semibold text-fg">{fmtN(stage.value)}</span>
            {stage.base !== null && stage.base > 0 && (
              <span className="text-[11px] text-fg-muted" title={stage.of}>
                {fmtPct(stage.value / stage.base, 0)}
              </span>
            )}
          </li>
        ))}
      </ol>
      <div className="grid grid-cols-3 gap-2 md:grid-cols-6">
        <Kpi
          testId="wa-send-spend"
          label="Gasto"
          value={cost !== null ? fmtUsdMicros(cost) : "—"}
          hint={costCop !== null ? `≈ ${fmtMoney(Math.round(costCop))}` : undefined}
        />
        <Kpi
          testId="wa-send-per-reply"
          label="Costo/respuesta"
          value={perReply !== null ? fmtUsdMicros(perReply) : "—"}
        />
        <Kpi
          testId="wa-send-per-sale"
          label="Costo/venta"
          value={perSale !== null ? fmtUsdMicros(perSale) : "—"}
        />
        <Kpi
          testId="wa-send-roas"
          label="ROAS aprox."
          value={roas !== null ? `${roas.toFixed(1)}×` : "—"}
        />
        <Kpi testId="wa-send-failed" label="Fallidos" value={fmtN(send.failed)} />
        <Kpi testId="wa-send-opted-out" label="Bajas" value={fmtN(send.optedOut)} />
      </div>
      {send.costPending > 0 && (
        <p className="px-1 pt-1.5 text-[11px] text-fg-muted">
          {plural(send.costPending, "mensaje", "mensajes")} todavía sin precio: Meta lo manda al
          entregarlos.
        </p>
      )}
      {send.untracked > 0 && (
        <p className="px-1 pt-1 text-[11px] text-fg-muted">
          {plural(send.untracked, "enviado", "enviados")} antes del registro de entregas: sin datos de
          entrega ni de costo.
        </p>
      )}
    </section>
  );
}
