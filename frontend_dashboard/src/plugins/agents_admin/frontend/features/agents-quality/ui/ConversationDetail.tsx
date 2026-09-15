import {
  episodeLabel,
  useCheckRegistry,
  useScorecard,
  type EpisodeRef,
} from "@plugins/agents_admin/frontend/entities/scorecard";
import { ScorecardPanel } from "@plugins/agents_admin/frontend/features/scorecard-panel";
import { TrajectoryStrip } from "@plugins/agents_admin/frontend/features/trajectory-strip";

interface Props {
  episode: EpisodeRef;
  selectedCheckId: string | null;
  onSelectCheck: (checkId: string) => void;
  onClose: () => void;
}

/**
 * Detalle de una conversación: tira de trayectoria + panel del scorecard,
 * lado a lado en pantallas anchas. Una sola fetch del detalle alimenta a ambos;
 * el check seleccionado (lifted en AgentsQuality) es el mismo para los dos y,
 * mientras el operador no elija uno, se abre en el primer crítico (o el primer fallo).
 */
export function ConversationDetail({ episode, selectedCheckId, onSelectCheck, onClose }: Props) {
  const detail = useScorecard(episode.sessionId, episode.episodeId);
  const registry = useCheckRegistry();
  const label = episodeLabel({ session_id: episode.sessionId, episode_id: episode.episodeId });

  const sc = detail.data?.scorecard ?? null;
  const effectiveCheck =
    selectedCheckId ?? sc?.first_critical?.check_id ?? sc?.first_failure?.check_id ?? null;

  return (
    <section aria-label={`Conversación ${label}`} className="flex min-w-0 flex-col gap-2 rounded-lg border border-line p-3">
      <header className="flex flex-wrap items-center gap-2">
        <h3 className="font-mono text-sm font-semibold text-fg" title={`${episode.sessionId} · ${episode.episodeId}`}>
          {label}
        </h3>
        {sc && (
          <span className="text-xs text-fg-muted">
            {sc.episode_date ?? sc.date}
            {sc.closing_tag ? ` · ${sc.closing_tag}` : ""} · {sc.turns} turnos
          </span>
        )}
        <button
          type="button"
          onClick={onClose}
          className="ml-auto rounded-md px-2 py-1 text-xs text-fg-muted transition hover:bg-white/5"
        >
          Cerrar
        </button>
      </header>

      {detail.isLoading ? (
        <p className="text-sm text-fg-muted">Cargando trayectoria y scorecard…</p>
      ) : detail.isError ? (
        <p className="text-sm text-red" role="alert">
          No se pudo cargar el scorecard de este episodio.
        </p>
      ) : (
        <div className="grid min-w-0 gap-3 2xl:grid-cols-[minmax(0,1fr)_24rem]">
          <div className="min-w-0">
            {detail.data!.trajectory ? (
              <TrajectoryStrip
                trajectory={detail.data!.trajectory}
                results={sc?.results ?? []}
                registry={registry.data}
                selectedCheckId={effectiveCheck}
                onSelectCheck={onSelectCheck}
              />
            ) : (
              <p className="rounded-lg border border-line p-4 text-sm text-fg-muted">
                Sin trayectoria para este episodio: la reconstrucción no encontró turnos.
              </p>
            )}
          </div>
          <ScorecardPanel
            episode={episode}
            detail={detail.data!}
            registry={registry.data}
            selectedCheckId={effectiveCheck}
            onSelectCheck={onSelectCheck}
          />
        </div>
      )}
    </section>
  );
}
