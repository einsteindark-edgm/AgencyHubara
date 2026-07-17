/**
 * Paso 4 — Audiencia: cards de segmento (toggle múltiple) + barra de total
 * con costo estimado EXPLÍCITO (unit_cost × N, USD micros → US$ + COP aprox)
 * y los excluidos (humano / opt-out) siempre visibles.
 */

import { Icon } from "@/shared/ui";

import type { SegmentsInfo } from "@plugins/marketing/frontend/entities/segment";
import {
  fmtCop,
  fmtN,
  fmtUsdMicros,
  usdMicrosToCop,
} from "@plugins/marketing/frontend/lib/format";

interface Props {
  selected: string[];
  info: SegmentsInfo | undefined;
  editable: boolean;
  recipients: number;
  totalCostMicros: number;
  onToggle: (key: string) => void;
}

export function AudienceStep({
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
