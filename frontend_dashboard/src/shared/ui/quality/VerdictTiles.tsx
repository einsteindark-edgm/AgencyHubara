import {
  QUALITY_VERDICT_ORDER as EPISODE_VERDICT_ORDER,
  qualityVerdictColor as episodeVerdictColor,
  qualityVerdictLabel as episodeVerdictLabel,
  type QualityVerdict,
  type VerdictCounts,
} from "@/shared/lib";

interface Props {
  totals: VerdictCounts;
  episodes: number;
  /** Elegir un veredicto (Falla/Alerta/Pasa): la composición decide a dónde lleva. */
  onSelectVerdict: (v: Exclude<QualityVerdict, "SIN_DATOS">) => void;
  /** Cola del `aria-label` de cada tile accionable (qué pasa al elegirlo). */
  selectHint?: string;
}

/**
 * Tiles de veredicto de los episodios: conteo y participación por veredicto,
 * en el orden Falla → Alerta → Pasa → Sin datos. "Sin datos" no es accionable.
 */
export function VerdictTiles({
  totals,
  episodes,
  onSelectVerdict,
  selectHint = "ver en Conversaciones",
}: Props) {
  return (
    <ul aria-label="Veredictos de los episodios" className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      {EPISODE_VERDICT_ORDER.map((v) => {
        const n = totals[v];
        const share = episodes ? Math.round((100 * n) / episodes) : 0;
        const body = (
          <>
            <span className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-fg-faint">
              <span className="inline-block h-2 w-2 rounded-full" style={{ background: episodeVerdictColor(v) }} aria-hidden="true" />
              {episodeVerdictLabel(v)}
            </span>
            <span className="text-2xl font-bold tabular-nums leading-tight" style={{ color: episodeVerdictColor(v) }}>
              {n}
            </span>
            <span className="text-[11px] text-fg-faint">{share} % de {episodes}</span>
          </>
        );
        return (
          <li key={v}>
            {v === "SIN_DATOS" ? (
              <div className="flex h-full flex-col rounded-lg border border-line p-2.5" title="Episodios sin trayectoria evaluable">
                {body}
              </div>
            ) : (
              <button
                type="button"
                onClick={() => onSelectVerdict(v)}
                aria-label={`${episodeVerdictLabel(v)}: ${n} episodios — ${selectHint}`}
                className="flex h-full w-full flex-col rounded-lg border border-line p-2.5 text-left transition hover:bg-white/5"
              >
                {body}
              </button>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export default VerdictTiles;
