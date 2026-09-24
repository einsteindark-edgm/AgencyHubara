import { useCheckStats, type StatsBot, type VerdictTotals } from "@plugins/agents_admin/frontend/entities/check-stats";
import {
  EPISODE_VERDICT_ORDER,
  episodeVerdictColor,
  episodeVerdictLabel,
  type EpisodeVerdict,
} from "@plugins/agents_admin/frontend/entities/scorecard";
import { CheckTrend } from "@plugins/agents_admin/frontend/features/check-trend";
import { FailurePareto } from "@plugins/agents_admin/frontend/features/failure-pareto";
import { StageFunnel } from "@plugins/agents_admin/frontend/features/stage-funnel";

interface Props {
  days: number;
  /** Solo los episodios de ese bot (encendido del bot nuevo); null = todos. */
  bot?: StatsBot | null;
  onSelectVerdict: (v: Exclude<EpisodeVerdict, "SIN_DATOS">) => void;
  onSelectCheck: (checkId: string) => void;
}

function VerdictTiles({
  totals,
  episodes,
  onSelectVerdict,
}: {
  totals: VerdictTotals;
  episodes: number;
  onSelectVerdict: Props["onSelectVerdict"];
}) {
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
                aria-label={`${episodeVerdictLabel(v)}: ${n} episodios — ver en Conversaciones`}
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

/** Pestaña Resumen: una sola fetch de agregados alimenta tiles, Pareto, embudo y tendencia. */
export function SummaryView({ days, bot = null, onSelectVerdict, onSelectCheck }: Props) {
  const stats = useCheckStats(days, bot);

  if (stats.isLoading) return <p className="text-sm text-fg-muted">Cargando agregados del scorecard…</p>;
  if (stats.isError) {
    return (
      <p className="text-sm text-red" role="alert">
        No se pudieron cargar los agregados del scorecard. Revisa que el API de evals esté disponible.
      </p>
    );
  }
  const data = stats.data!;
  if (data.episodes === 0) {
    return (
      <p className="rounded-lg border border-line p-4 text-sm text-fg-muted">
        Aún no hay scorecards: se generan al cerrar cada episodio.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-fg-muted">
        {data.episodes} episodios evaluados en los últimos {data.days} días. El veredicto nunca es un promedio:
        falla si cae un check crítico, alerta si cae uno mayor.
      </p>
      <VerdictTiles totals={data.verdicts} episodes={data.episodes} onSelectVerdict={onSelectVerdict} />
      <div className="grid gap-3 xl:grid-cols-2">
        <section className="rounded-lg border border-line p-3" aria-labelledby="pareto-title">
          <h3 id="pareto-title" className="text-sm font-semibold text-fg">Qué arreglar primero</h3>
          <p className="mb-2 text-[11px] text-fg-faint">
            Fallos por check, coloreados por nivel, con el acumulado. Elige una barra para ver esos episodios.
          </p>
          <FailurePareto pareto={data.pareto} days={data.days} onSelectCheck={onSelectCheck} />
        </section>
        <section className="rounded-lg border border-line p-3" aria-labelledby="funnel-title">
          <h3 id="funnel-title" className="text-sm font-semibold text-fg">Dónde terminan los episodios</h3>
          <p className="mb-2 text-[11px] text-fg-faint">Etapa final de cada episodio y su veredicto.</p>
          <StageFunnel funnel={data.funnel} />
        </section>
      </div>
      <section className="rounded-lg border border-line p-3" aria-labelledby="trend-title">
        <h3 id="trend-title" className="text-sm font-semibold text-fg">Cumplimiento por check, semana a semana</h3>
        <CheckTrend trend={data.trend} />
      </section>
    </div>
  );
}
