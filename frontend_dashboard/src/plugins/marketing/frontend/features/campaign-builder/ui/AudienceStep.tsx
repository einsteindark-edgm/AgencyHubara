/**
 * Paso 4 — Audiencia: cards de segmento (toggle múltiple) + importación de
 * contactos por archivo (CSV/TXT de números — no necesitan haber chateado con
 * el bot) + barra de total con costo estimado EXPLÍCITO (unit_cost × N, USD
 * micros → US$ + COP aprox) y los excluidos (humano / opt-out) siempre visibles.
 */

import { useRef } from "react";

import { Icon } from "@/shared/ui";

import {
  importRejectReasonLabel,
  useClearContacts,
  useImportContacts,
  type Campaign,
} from "@plugins/marketing/frontend/entities/campaign";
import type { SegmentsInfo } from "@plugins/marketing/frontend/entities/segment";
import {
  apiErrorDetail,
  fmtCop,
  fmtN,
  fmtUsdMicros,
  usdMicrosToCop,
} from "@plugins/marketing/frontend/lib/format";

interface Props {
  campaign: Campaign;
  selected: string[];
  info: SegmentsInfo | undefined;
  editable: boolean;
  recipients: number;
  totalCostMicros: number;
  onToggle: (key: string) => void;
}

export function AudienceStep({
  campaign,
  selected,
  info,
  editable,
  recipients,
  totalCostMicros,
  onToggle,
}: Props) {
  if (!info) {
    return <p className="text-[11.5px] text-fg-faint">Cargando segmentos…</p>;
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-3 gap-2.5 max-[900px]:grid-cols-1">
        {info.segments.map((s) => {
          const on = selected.includes(s.key);
          return (
            <button
              key={s.key}
              type="button"
              disabled={!editable}
              aria-pressed={on}
              onClick={() => onToggle(s.key)}
              className={
                "rounded-lg border px-3 py-2.5 text-left transition-colors disabled:opacity-60 " +
                (on
                  ? "border-accent bg-accent/10"
                  : "border-line hover:border-fg-faint hover:bg-white/[0.03]")
              }
            >
              <span className="flex items-center gap-2">
                <span className="text-[12.5px] font-semibold text-fg">{s.label}</span>
                {on ? (
                  <span className="ml-auto text-accent-fg">
                    <Icon.check />
                  </span>
                ) : null}
              </span>
              <span className="mt-0.5 block text-[11px] leading-snug text-fg-muted">
                {s.description}
              </span>
              <span className="mt-1.5 block text-[11px] font-semibold tabular-nums text-fg-soft">
                {fmtN(s.count)} contactos
              </span>
            </button>
          );
        })}
      </div>

      <ImportContacts campaign={campaign} editable={editable} />

      <div className="rounded-md bg-white/[0.04] px-3 py-2 text-[12px] text-fg">
        <span className="font-semibold tabular-nums">
          {fmtN(recipients)} destinatarios
        </span>
        <span className="text-fg-muted">
          {" · "}Costo estimado: {fmtUsdMicros(totalCostMicros)} (≈{" "}
          {fmtCop(usdMicrosToCop(totalCostMicros))} COP aprox)
        </span>
      </div>
      <p className="text-[11px] text-fg-faint">
        {fmtN(info.excludedCount)} contactos excluidos: atendidos por humano u
        opt-out de promociones.
      </p>
    </div>
  );
}

/** Importación de una lista externa de números (feria, base de otro canal). */
function ImportContacts({
  campaign,
  editable,
}: {
  campaign: Campaign;
  editable: boolean;
}) {
  const importContacts = useImportContacts(campaign.id);
  const clearContacts = useClearContacts(campaign.id);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const count = campaign.importedContacts.length;
  const summary = importContacts.data;

  return (
    <div className="rounded-lg border border-dashed border-line px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-fg-muted">
          <Icon.files />
        </span>
        <span className="text-[12.5px] font-semibold text-fg">
          Importar contactos desde un archivo
        </span>
        <span className="text-[11px] text-fg-muted">
          CSV o TXT con una columna de teléfonos (y opcional nombre)
        </span>
        <div className="ml-auto flex items-center gap-2">
          <input
            ref={inputRef}
            type="file"
            accept=".csv,.txt,.tsv,text/csv,text/plain"
            aria-label="Importar contactos (CSV)"
            disabled={!editable || importContacts.isPending}
            className="sr-only"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) importContacts.mutate(file);
              e.target.value = "";
            }}
          />
          <button
            type="button"
            disabled={!editable || importContacts.isPending}
            onClick={() => inputRef.current?.click()}
            className="rounded-md border border-line px-3 py-1.5 text-[12px] font-semibold text-fg hover:bg-white/[0.05] disabled:opacity-50"
          >
            {importContacts.isPending ? "Importando…" : "Elegir archivo"}
          </button>
          {count > 0 ? (
            <button
              type="button"
              disabled={!editable || clearContacts.isPending}
              onClick={() => clearContacts.mutate()}
              className="rounded-md border border-line px-3 py-1.5 text-[12px] font-semibold text-fg-muted hover:bg-white/[0.05] disabled:opacity-50"
            >
              Quitar importados
            </button>
          ) : null}
        </div>
      </div>

      {count > 0 ? (
        <p className="mt-2 text-[12px] tabular-nums text-fg">
          <span className="font-semibold">{fmtN(count)} contactos importados</span>
          <span className="text-fg-muted">
            {" "}
            — reciben la campaña aunque nunca hayan chateado con el bot.
          </span>
        </p>
      ) : null}

      {summary ? (
        <div className="mt-2 text-[11.5px] leading-snug text-fg-muted">
          <p>
            Última importación: {fmtN(summary.imported)} nuevos ·{" "}
            {fmtN(summary.duplicates)} repetido{summary.duplicates === 1 ? "" : "s"} ·{" "}
            {fmtN(summary.rejectedCount)} rechazado{summary.rejectedCount === 1 ? "" : "s"}.
          </p>
          {summary.rejected.length > 0 ? (
            <p className="mt-1 text-fg-faint">
              Rechazados:{" "}
              {summary.rejected
                .slice(0, 8)
                .map((r) => `línea ${r.line} (${importRejectReasonLabel(r.reason)})`)
                .join(", ")}
              {summary.rejected.length > 8 ? "…" : ""}
            </p>
          ) : null}
        </div>
      ) : null}

      {importContacts.error ? (
        <p className="mt-2 text-[11.5px] leading-snug text-danger">
          {apiErrorDetail(importContacts.error)}
        </p>
      ) : null}
      {clearContacts.error ? (
        <p className="mt-2 text-[11.5px] leading-snug text-danger">
          {apiErrorDetail(clearContacts.error)}
        </p>
      ) : null}
    </div>
  );
}
