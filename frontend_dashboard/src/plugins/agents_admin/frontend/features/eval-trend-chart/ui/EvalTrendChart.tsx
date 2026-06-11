import { useMemo, useState } from "react";

import {
  episodeUnitKey,
  useConversationEvals,
} from "@plugins/agents_admin/frontend/entities/episode-eval";
import { useEvalTrend } from "@plugins/agents_admin/frontend/entities/eval-trend";

import {
  lineFromAggregate,
  linesFromClientEpisodes,
  type TrendLine,
  type TrendLinePoint,
} from "../lib/series";

const W = 150;
const H = 34;
const PAD = 4;

interface Props {
  /** Día seleccionado en modo agregado (filtra la lista de episodios). */
  selectedDate?: string | null;
  onSelectDate?: (date: string | null) => void;
  /** Episodio "actual" de la comparativa (`<session>::<episode>`), lifted: lo
   *  comparte la lista de episodios. null = promedio de todos los clientes. */
  selectedEpisodeKey?: string | null;
  onSelectEpisode?: (key: string | null) => void;
  /** Ventana (días) de conversaciones — compartida con la lista de episodios. */
  windowDays?: number;
}

function sessionLabel(sessionId: string): string {
  return sessionId.startsWith("wa_") ? sessionId.slice(3) : sessionId;
}

/** Sparkline SVG sin dependencias: línea + umbral + puntos bajos en rojo. */
function Sparkline({
  points,
  threshold,
  selectedDate,
  onSelectDate,
}: {
  points: TrendLinePoint[];
  threshold: number;
  selectedDate?: string | null;
  onSelectDate?: (date: string | null) => void;
}) {
  if (points.length === 0) return <span className="w-[150px] shrink-0 text-xs text-fg-faint">sin datos</span>;

  const xs = (i: number) =>
    points.length === 1 ? W / 2 : PAD + (i * (W - 2 * PAD)) / (points.length - 1);
  const ys = (v: number) => H - PAD - v * (H - 2 * PAD); // 0..1 -> abajo..arriba
  const line = points.map((p, i) => `${xs(i)},${ys(p.value)}`).join(" ");

  return (
    <svg width={W} height={H} className="shrink-0" role="img" aria-label="tendencia">
      <line
        x1={PAD}
        y1={ys(threshold)}
        x2={W - PAD}
        y2={ys(threshold)}
        stroke="var(--color-line-strong)"
        strokeWidth={1}
        strokeDasharray="3 3"
      />
      {points.length > 1 && (
        <polyline points={line} fill="none" stroke="var(--color-accent-fg)" strokeWidth={1.5} />
      )}
      {points.map((p, i) => (
        <circle
          key={p.key}
          cx={xs(i)}
          cy={ys(p.value)}
          r={p.date === selectedDate ? 3.2 : p.below ? 2.6 : 1.8}
          fill={p.below ? "var(--color-red)" : "var(--color-accent-fg)"}
          stroke={p.date === selectedDate ? "var(--color-fg)" : "none"}
          strokeWidth={p.date === selectedDate ? 1 : 0}
          className={onSelectDate ? "cursor-pointer" : undefined}
          onClick={
            onSelectDate ? () => onSelectDate(p.date === selectedDate ? null : p.date) : undefined
          }
        >
          <title>{p.hint}</title>
        </circle>
      ))}
    </svg>
  );
}

/** Bloque de un extremo de la serie: valor + su fecha/episodio (inicio o actual). */
function Endpoint({ caption, point }: { caption: string; point?: TrendLinePoint }) {
  const color = !point ? "text-fg-faint" : point.below ? "text-red" : "text-green";
  return (
    <div className="w-16 shrink-0 text-center" title={point?.hint}>
      <div className="text-[9px] uppercase tracking-wide text-fg-faint">{caption}</div>
      <div className={"text-sm font-semibold " + color}>
        {point ? point.value.toFixed(2) : "—"}
      </div>
      <div className="truncate text-[10px] text-fg-muted">{point?.label ?? ""}</div>
    </div>
  );
}

function MetricRow({
  line,
  threshold,
  mode,
  selectedDate,
  onSelectDate,
}: {
  line: TrendLine;
  threshold: number;
  mode: "aggregate" | "episodes";
  selectedDate?: string | null;
  onSelectDate?: (date: string | null) => void;
}) {
  const pts = line.points;
  const first = pts[0];
  const last = pts.at(-1);
  const delta = first && last && pts.length >= 2 ? last.value - first.value : null;
  const dir = delta === null ? "→" : delta > 0.02 ? "↑" : delta < -0.02 ? "↓" : "→";
  const dirColor = dir === "↑" ? "text-green" : dir === "↓" ? "text-red" : "text-fg-faint";
  const lowDates = pts.filter((p) => p.below).map((p) => p.date);
  const lowCount = lowDates.length;

  return (
    <div className="flex items-center gap-2 border-b border-line py-2">
      <div className="w-36 shrink-0 truncate text-sm font-medium text-fg" title={line.metric}>
        {line.metric}
      </div>
      <Endpoint caption="inicio" point={first} />
      <Sparkline
        points={pts}
        threshold={threshold}
        selectedDate={mode === "aggregate" ? selectedDate : null}
        onSelectDate={mode === "aggregate" ? onSelectDate : undefined}
      />
      <Endpoint caption="actual" point={last} />
      <div
        className={"w-5 shrink-0 text-center text-sm " + dirColor}
        title={delta !== null ? `${delta >= 0 ? "+" : ""}${delta.toFixed(2)} entre inicio y actual` : ""}
      >
        {dir}
      </div>
      <div className="min-w-0 flex-1 text-xs text-fg-muted">
        {mode === "aggregate" ? (
          lowCount === 0 ? (
            <span className="text-green/70">sin días bajos</span>
          ) : (
            <span title={lowDates.join(", ")}>
              <span className="font-semibold text-red">{lowCount}</span> día
              {lowCount > 1 ? "s" : ""} bajo {threshold}:{" "}
              {lowDates.slice(-3).map((d, i, arr) => (
                <button
                  key={d}
                  type="button"
                  onClick={() => onSelectDate?.(d === selectedDate ? null : d)}
                  className={
                    "underline decoration-dotted underline-offset-2 hover:text-fg " +
                    (d === selectedDate ? "font-semibold text-fg" : "")
                  }
                  title="Ver las conversaciones evaluadas ese día"
                >
                  {d}
                  {i < arr.length - 1 ? ", " : ""}
                </button>
              ))}
              {lowCount > 3 ? "…" : ""}
            </span>
          )
        ) : pts.length < 2 ? (
          <span className="text-fg-faint">un solo episodio — elegí un rango para comparar</span>
        ) : lowCount === 0 ? (
          <span className="text-green/70">todos los episodios sobre el umbral</span>
        ) : (
          <span>
            <span className="font-semibold text-red">{lowCount}</span>/{pts.length} episodios bajo{" "}
            {threshold}
          </span>
        )}
      </div>
    </div>
  );
}

/**
 * Tendencia de calidad del agente (solo conversaciones reales — el golden corre
 * en CI y sus resultados viven en SigNoz, no en esta API). Cada línea de métrica
 * muestra su valor INICIAL (con etiqueta) y el ACTUAL flanqueando el sparkline.
 *
 * Dos modos (selector "cliente" en el header):
 *   - **Todos**: promedio diario de TODOS los episodios (salud global del agente);
 *     los días bajos son clickeables → filtran la lista de episodios.
 *   - **Un cliente**: la evolución ENTRE sus episodios (un punto por episodio).
 *     El operador elige el episodio de **inicio** y el de **actual** para ver,
 *     métrica por métrica, si mejoró o empeoró de una conversación a la otra
 *     (cada episodio se evalúa una sola vez, así que comparar SUS re-evals no
 *     dice nada — la señal está ENTRE episodios).
 */
export function EvalTrendChart({
  selectedDate,
  onSelectDate,
  selectedEpisodeKey = null,
  onSelectEpisode = () => {},
  windowDays = 30,
}: Props) {
  const { data: aggData, isLoading: aggLoading, isError: aggError } = useEvalTrend(30, "online");
  const { data: convData, isLoading: convLoading } = useConversationEvals(windowDays, "online");
  const threshold = aggData?.threshold ?? 0.7;

  const conversations = convData?.conversations ?? [];

  // Cliente en foco: derivado del episodio "actual" seleccionado
  // (`<session>::<episode>`), así que no hace falta estado extra para el cliente.
  const derivedSession = selectedEpisodeKey ? selectedEpisodeKey.split("::")[0] : null;

  // Episodios del cliente en orden del EPISODIO (ep_001 → ep_011), no por fecha
  // de evaluación: el backfill puede haber evaluado los históricos todos juntos
  // un día, así que ordenar por `last_ts` los mezcla. `episode_id` es el orden
  // real en que ocurrieron las conversaciones del cliente (numeric-aware para
  // ep_2 < ep_11). La sesión legacy sin episodio ("") va al final.
  const episodesAsc = useMemo(
    () =>
      derivedSession
        ? [...conversations.filter((c) => c.session_id === derivedSession)].sort((a, b) =>
            (a.episode_id || "zzz").localeCompare(b.episode_id || "zzz", undefined, {
              numeric: true,
            }),
          )
        : [],
    [conversations, derivedSession],
  );
  const keysAsc = useMemo(() => episodesAsc.map(episodeUnitKey), [episodesAsc]);

  // Extremos de la comparativa. "actual" = el lifted selectedEpisodeKey (lo fija
  // también la lista); "inicio" = estado local, default el episodio más viejo.
  const [startKey, setStartKey] = useState<string | null>(null);
  const actualKey =
    selectedEpisodeKey && keysAsc.includes(selectedEpisodeKey)
      ? selectedEpisodeKey
      : (keysAsc.at(-1) ?? null);
  const startKeyEff =
    startKey && keysAsc.includes(startKey) ? startKey : (keysAsc[0] ?? null);

  // Recorte [inicio..actual] en orden cronológico (tolera que se elijan al revés).
  const slice = useMemo(() => {
    const iA = keysAsc.indexOf(startKeyEff ?? "");
    const iB = keysAsc.indexOf(actualKey ?? "");
    if (iA < 0 || iB < 0) return [];
    return episodesAsc.slice(Math.min(iA, iB), Math.max(iA, iB) + 1);
  }, [episodesAsc, keysAsc, startKeyEff, actualKey]);

  const clientSelected = !!derivedSession && episodesAsc.length > 0;
  const mode: "aggregate" | "episodes" = clientSelected ? "episodes" : "aggregate";

  const clientSessions = useMemo(() => {
    const count = new Map<string, number>();
    for (const c of conversations) count.set(c.session_id, (count.get(c.session_id) ?? 0) + 1);
    return [...count.entries()].map(([session, episodes]) => ({ session, episodes }));
  }, [conversations]);

  // Episodios del cliente más recientes primero (para listar en los dropdowns).
  const episodesDesc = useMemo(() => [...episodesAsc].reverse(), [episodesAsc]);

  const pickClient = (session: string | null) => {
    setStartKey(null); // reset del extremo "inicio" al cambiar de cliente
    if (!session) return onSelectEpisode(null);
    const eps = [...conversations.filter((c) => c.session_id === session)].sort((a, b) =>
      (b.episode_id || "").localeCompare(a.episode_id || "", undefined, { numeric: true }),
    );
    onSelectEpisode(eps[0] ? episodeUnitKey(eps[0]) : null); // "actual" = el episodio más nuevo
  };

  const lines: TrendLine[] = useMemo(() => {
    if (mode === "episodes") return linesFromClientEpisodes(slice);
    return (aggData?.series ?? []).map((s) => lineFromAggregate(s, threshold));
  }, [mode, slice, aggData, threshold]);

  const epOption = (key: string) => {
    const c = episodesDesc.find((x) => episodeUnitKey(x) === key);
    if (!c) return key;
    return `${c.episode_id || "sesión completa"} · ${
      c.last_avg === null ? "—" : c.last_avg.toFixed(2)
    }${c.closing_tag ? ` · ${c.closing_tag}` : ""}`;
  };

  return (
    <section className="rounded-lg border border-line p-4 text-fg">
      <header className="mb-2 flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">Tendencia de calidad</h3>

        <div className="flex flex-wrap items-center gap-2 text-xs text-fg-muted">
          <label className="flex items-center gap-1">
            <span className="text-fg-faint">cliente:</span>
            <select
              value={derivedSession ?? ""}
              onChange={(e) => pickClient(e.target.value || null)}
              className="max-w-[12rem] truncate rounded-md border border-line bg-white/5 px-2 py-1 text-xs text-fg-soft focus:border-accent focus:outline-none"
            >
              <option value="">Todos (promedio diario)</option>
              {clientSessions.map(({ session, episodes }) => (
                <option key={session} value={session}>
                  {sessionLabel(session)} ({episodes} ep{episodes > 1 ? "s" : ""})
                </option>
              ))}
            </select>
          </label>
          {clientSelected && (
            <>
              <label className="flex items-center gap-1">
                <span className="text-fg-faint">inicio:</span>
                <select
                  value={startKeyEff ?? ""}
                  onChange={(e) => setStartKey(e.target.value || null)}
                  className="max-w-[13rem] truncate rounded-md border border-line bg-white/5 px-2 py-1 text-xs text-fg-soft focus:border-accent focus:outline-none"
                >
                  {episodesDesc.map((c) => {
                    const key = episodeUnitKey(c);
                    return (
                      <option key={key} value={key}>
                        {epOption(key)}
                      </option>
                    );
                  })}
                </select>
              </label>
              <label className="flex items-center gap-1">
                <span className="text-fg-faint">actual:</span>
                <select
                  value={actualKey ?? ""}
                  onChange={(e) => onSelectEpisode(e.target.value || null)}
                  className="max-w-[13rem] truncate rounded-md border border-line bg-white/5 px-2 py-1 text-xs text-fg-soft focus:border-accent focus:outline-none"
                >
                  {episodesDesc.map((c) => {
                    const key = episodeUnitKey(c);
                    return (
                      <option key={key} value={key}>
                        {epOption(key)}
                      </option>
                    );
                  })}
                </select>
              </label>
            </>
          )}
        </div>

        <span
          className="ml-auto text-[10px] text-fg-faint"
          title="El golden set corre en el GitHub Action (CI) y sus scores se emiten a SigNoz, no a esta API. Acá ves solo conversaciones reales."
        >
          conversaciones reales · golden → SigNoz/CI
        </span>
      </header>

      {/* Subtítulo: explica qué se está promediando (o qué episodios se comparan). */}
      {mode === "episodes" ? (
        <p className="mb-3 flex flex-wrap items-center gap-x-2 text-xs text-fg-muted">
          <span>
            Comparando episodios de{" "}
            <span className="font-mono text-fg-soft">{sessionLabel(derivedSession!)}</span> —{" "}
            {slice.length} episodio{slice.length === 1 ? "" : "s"} entre inicio y actual. Cada punto
            es un episodio; el delta dice si la métrica mejoró o empeoró.
          </span>
          <button
            type="button"
            onClick={() => onSelectEpisode(null)}
            className="underline decoration-dotted underline-offset-2 hover:text-fg"
          >
            volver al promedio
          </button>
        </p>
      ) : (
        <p className="mb-3 text-xs text-fg-faint">
          últimos 30 días · umbral {threshold} · promedio de TODOS los episodios por día — elegí un
          cliente arriba para comparar la evolución entre sus episodios.
        </p>
      )}

      {derivedSession && episodesAsc.length === 0 ? (
        convLoading ? (
          <p className="py-6 text-center text-sm text-fg-faint">Cargando episodios…</p>
        ) : (
          <p className="py-6 text-center text-sm text-fg-muted">
            Ese cliente ya no tiene episodios en la ventana.{" "}
            <button
              type="button"
              onClick={() => onSelectEpisode(null)}
              className="underline decoration-dotted underline-offset-2 hover:text-fg"
            >
              volver al promedio
            </button>
          </p>
        )
      ) : mode === "aggregate" && aggLoading ? (
        <p className="py-6 text-center text-sm text-fg-faint">Cargando tendencia…</p>
      ) : mode === "aggregate" && aggError ? (
        <p className="py-6 text-center text-sm text-red/70">No se pudo leer la tendencia.</p>
      ) : lines.length === 0 ? (
        <p className="py-6 text-center text-sm text-fg-faint">
          {mode === "episodes"
            ? "Estos episodios aún no tienen métricas registradas."
            : "Aún no hay histórico. Se llena con cada eval al cerrar un episodio real."}
        </p>
      ) : (
        <div>
          {lines.map((l) => (
            <MetricRow
              key={l.metric}
              line={l}
              threshold={threshold}
              mode={mode}
              selectedDate={selectedDate}
              onSelectDate={onSelectDate}
            />
          ))}
        </div>
      )}
    </section>
  );
}

export default EvalTrendChart;
