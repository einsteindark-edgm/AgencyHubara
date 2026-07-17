/**
 * Inspector derecho de marketing — tabs Preview / Validación.
 *
 * Preview: mock de teléfono con la burbuja del template MARKETING REAL que
 * va a salir (greeting del sistema + header/body + línea de oferta espejo
 * del backend + opt-out fijo). Validación: checklist de requisitos (espejo
 * del 422 de /send) y, para campañas enviadas, las stats reales de
 * GET /stats (envíos, respuestas, atribución, gasto).
 */

import { useState } from "react";

import { Icon } from "@/shared/ui";

import {
  campaignChecklist,
  campaignOfferLine,
  OPT_OUT_LINE,
  useCampaignStats,
  type Campaign,
} from "@plugins/marketing/frontend/entities/campaign";
import {
  fmtCop,
  fmtN,
  fmtUsdMicros,
} from "@plugins/marketing/frontend/lib/format";

interface Props {
  campaign: Campaign;
}

type Tab = "preview" | "validation";

export function CampaignInspector({ campaign }: Props) {
  const [tab, setTab] = useState<Tab>("preview");

  return (
    <aside className="inspector">
      <div className="flex h-[38px] shrink-0 items-center gap-1 border-b border-line bg-sidebar px-2">
        {(
          [
            { key: "preview", label: "Preview" },
            { key: "validation", label: "Validación" },
          ] as const
        ).map((t) => (
          <button
            key={t.key}
            type="button"
            aria-pressed={tab === t.key}
            onClick={() => setTab(t.key)}
            className={
              "rounded-md px-3 py-1 text-[11.5px] font-semibold " +
              (tab === t.key
                ? "bg-accent/15 text-accent-fg"
                : "text-fg-muted hover:bg-white/[0.04] hover:text-fg")
            }
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {tab === "preview" ? (
          <TemplatePreview campaign={campaign} />
        ) : (
          <ValidationPanel campaign={campaign} />
        )}
      </div>
    </aside>
  );
}

/* ── Preview: mock de teléfono estilo WhatsApp ──────────────────────────── */

function TemplatePreview({ campaign }: { campaign: Campaign }) {
  const m = campaign.message;
  return (
    <div className="rounded-2xl border border-line bg-canvas p-2.5">
      {/* "Pantalla" del teléfono */}
      <div className="flex flex-col gap-2 rounded-xl bg-sidebar p-2.5">
        <div className="flex items-center gap-2 border-b border-line/60 pb-2">
          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-ok-soft text-ok">
            <Icon.msg />
          </span>
          <span className="text-[11.5px] font-semibold text-fg">WhatsApp</span>
          <span className="ml-auto text-[10px] text-fg-faint">preview</span>
        </div>

        {/* Burbuja entrante del negocio */}
        <div className="max-w-[92%] rounded-lg rounded-tl-none border border-line bg-inspector px-2.5 py-2 text-[12px] leading-relaxed text-fg">
          <p className="font-semibold">¡Hola Camila! 👋</p>
          {m.header.trim() ? <p className="mt-1 font-semibold">{m.header}</p> : null}
          {m.body.trim() ? (
            <p className="mt-1 whitespace-pre-wrap">{m.body}</p>
          ) : (
            <p className="mt-1 italic text-fg-faint">
              El cuerpo del mensaje aparecerá acá…
            </p>
          )}
          <p className="mt-1.5">{campaignOfferLine(campaign)}</p>
          <p className="mt-1.5 text-[10.5px] leading-snug text-fg-muted">
            {OPT_OUT_LINE}
          </p>
          {m.footer.trim() ? (
            <p className="mt-1 text-[10.5px] text-fg-faint">{m.footer}</p>
          ) : null}
        </div>

        {m.cta.trim() ? (
          <div className="max-w-[92%] rounded-lg border border-line bg-inspector px-2.5 py-1.5 text-center text-[11.5px] font-semibold text-info">
            {m.cta}
          </div>
        ) : null}
      </div>

      <p className="px-1 pt-2 text-[10.5px] leading-snug text-fg-faint">
        El saludo se personaliza con el nombre de cada destinatario (o "Hola" a
        secas si no lo tenemos).
      </p>
    </div>
  );
}

/* ── Validación: checklist + stats reales para enviadas ─────────────────── */

function ValidationPanel({ campaign }: { campaign: Campaign }) {
  const items = campaignChecklist(campaign);
  const { data: stats } = useCampaignStats(
    campaign.id,
    campaign.status === "sent",
  );

  return (
    <div className="flex flex-col gap-3">
      <ul className="flex flex-col gap-1.5">
        {items.map((i) => (
          <li
            key={i.key}
            className="flex items-center gap-2 rounded-md border border-line/60 px-2.5 py-1.5"
          >
            <span
              className={
                "flex h-4.5 w-4.5 shrink-0 items-center justify-center rounded-full " +
                (i.done
                  ? "bg-ok-soft text-ok"
                  : i.required
                    ? "bg-danger-soft text-danger"
                    : "bg-line/60 text-fg-faint")
              }
            >
              {i.done ? <Icon.check /> : <Icon.x />}
            </span>
            <span className="text-[12px] text-fg">{i.label}</span>
            {!i.required ? (
              <span className="ml-auto text-[10px] text-fg-faint">opcional</span>
            ) : null}
          </li>
        ))}
      </ul>

      {campaign.status === "sent" ? (
        stats ? (
          <div className="rounded-lg border border-line bg-sidebar/40 p-3">
            <h3 className="mb-2 text-[11px] font-bold uppercase tracking-wide text-fg-muted">
              Resultado del envío
            </h3>
            <dl className="grid grid-cols-2 gap-x-3 gap-y-2">
              <StatRow label="Enviados" value={stats.sent !== null ? fmtN(stats.sent) : "—"} />
              <StatRow label="Fallidos" value={fmtN(stats.failedCount)} />
              <StatRow label="Respondieron" value={fmtN(stats.replied)} />
              <StatRow label="Pedidos atribuidos" value={fmtN(stats.attributedOrders)} />
              <StatRow
                label="Revenue atribuido"
                value={fmtCop(stats.attributedRevenueCop)}
              />
              <StatRow
                label="Gastado"
                value={
                  stats.spentUsdMicros !== null
                    ? fmtUsdMicros(stats.spentUsdMicros, 4)
                    : "—"
                }
              />
            </dl>
          </div>
        ) : (
          <p className="text-[11.5px] text-fg-faint">Cargando stats…</p>
        )
      ) : null}
    </div>
  );
}

function StatRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <dt className="text-[10px] uppercase tracking-wide text-fg-faint">{label}</dt>
      <dd className="text-[13px] font-semibold tabular-nums text-fg">{value}</dd>
    </div>
  );
}
