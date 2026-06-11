import { useMemo, useState } from "react";

import {
  episodeUnitKey,
  useConversationEvals,
} from "@plugins/agents_admin/frontend/entities/episode-eval";
import { useEvalTrend } from "@plugins/agents_admin/frontend/entities/eval-trend";

import {
  lineFromAggregate,
  linesFromEpisode,
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
  /** Episodio aislado (`<session>::<episode>`) o null = promedio de todos. */
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

/** Bloque de un extremo de la serie: valor + su fecha (inicio o actual). */
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
  mode: "aggregate" | "episode";
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
          <span className="text-fg-faint">una sola eval — sin evolución todavía</span>
        ) : lowCount === 0 ? (
          <span className="text-green/70">todas las evals sobre el umbral</span>
        ) : (
          <span>
            <span className="font-semibold text-red">{lowCount}</span>/{pts.length} evals bajo {threshold}
          </span>
        )}
      </div>
    </div>
  );
}

/**
 * Tendencia de calidad del agente. Cada línea de métrica muestra su valor
 * INICIAL (con fecha) y el ACTUAL (con fecha) flanqueando el sparkline, para ver
 * de un vistazo el avance o la pérdida.
 *
 * Dos modos (selector "Conversación" en el header, solo `online`):
 *   - **Todas**: promedio diario de TODOS los episodios (salud global del agente);
 *     los días bajos son clickeables → filtran la lista de episodios.
 *   - **Un episodio**: su evolución por métrica a través de SUS evals, sin
 *     contaminación de otros episodios (cada episodio es una intención distinta).
 */
export function EvalTrendChart({
  selectedDate,
  onSelectDate,
  selectedEpisodeKey = null,
  onSelectEpisode = () => {},
  windowDays = 30,
}: Props) {
  const [suite, setSuite] = useState<"online" | "golden">("online");
  const { data: aggData, isLoading: aggLoading, isError: aggError } = useEvalTrend(30, suite);
  const { data: convData, isLoading: convLoading } = useConversationEvals(windowDays, "online");
  const threshold = aggData?.threshold ?? 0.7;

  const conversations = convData?.conversations ?? [];
  const selectedConv =
    selectedEpisodeKey && suite === "online"
      ? conversations.find((c) => episodeUnitKey(c) === selectedEpisodeKey) ?? null
      : null;
  const episodeSelected = !!selectedEpisodeKey && suite === "online";
  const mode: "aggregate" | "episode" = selectedConv ? "episode" : "aggregate";

  // Cascada cliente → episodio. El cliente se DERIVA del episodio seleccionado
  // (`<session>::<episode>`), así que no hace falta estado extra: elegir un
  // cliente auto-enfoca su episodio más reciente.
  const derivedSession = selectedEpisodeKey ? selectedEpisodeKey.split("::")[0] : null;
  const clientSessions = useMemo(() => {
    const count = new Map<string, number>();
    for (const c of conversations) count.set(c.session_id, (count.get(c.session_id) ?? 0) + 1);
    return [...count.entries()].map(([session, episodes]) => ({ session, episodes }));
  }, [conversations]);
  const episodesOfClient = useMemo(
    () =>
      derivedSession
        ? [...conversations.filter((c) => c.session_id === derivedSession)].sort((a, b) =>
            (b.last_date + b.last_ts).localeCompare(a.last_date + a.last_ts),
          )
        : [],
    [conversations, derivedSession],
  );

  const pickClient = (session: string | null) => {
    if (!session) return onSelectEpisode(null);
    const eps = [...conversations.filter((c) => c.session_id === session)].sort((a, b) =>
      (b.last_date + b.last_ts).localeCompare(a.last_date + a.last_ts),
    );
    onSelectEpisode(eps[0] ? episodeUnitKey(eps[0]) : null); // el más reciente
  };

  const lines: TrendLine[] = useMemo(() => {
    if (selectedConv) return linesFromEpisode(selectedConv);
    return (aggData?.series ?? []).map((s) => lineFromAggregate(s, threshold));
  }, [selectedConv, aggData, threshold]);

  const switchSuite = (s: "online" | "golden") => {
    if (s === "golden") onSelectEpisode(null); // los goldens no son episodios reales
    setSuite(s);
  };

  return (
    <section className="rounded-lg border border-line p-4 text-fg">
      <header className="mb-2 flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">Tendencia de calidad</h3>

        {suite === "online" && (
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
            {derivedSession && (
              <label className="flex items-center gap-1">
                <span className="text-fg-faint">episodio:</span>
                <select
                  value={selectedEpisodeKey ?? ""}
                  onChange={(e) => onSelectEpisode(e.target.value || null)}
                  className="max-w-[13rem] truncate rounded-md border border-line bg-white/5 px-2 py-1 text-xs text-fg-soft focus:border-accent focus:outline-none"
                >
                  {episodesOfClient.map((c) => {
                    const key = episodeUnitKey(c);
                    return (
                      <option key={key} value={key}>
                        {c.episode_id || "sesión completa"} ·{" "}
                        {c.last_avg === null ? "—" : c.last_avg.toFixed(2)}
                        {c.closing_tag ? ` · ${c.closing_tag}` : ""}
                      </option>
                    );
                  })}
                </select>
              </label>
            )}
          </div>
        )}

        <div className="ml-auto flex gap-1 rounded-md bg-white/5 p-0.5 text-xs">
          {(["online", "golden"] as const).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => switchSuite(s)}
              className={
                "rounded px-2 py-0.5 " +
                (suite === s ? "bg-white/15 font-semibold text-fg" : "text-fg-muted")
              }
            >
              {s === "online" ? "conversaciones reales" : "golden (CI)"}
            </button>
          ))}
        </div>
      </header>

      {/* Subtítulo: explica qué se está promediando (o que es un episodio aislado). */}
      {mode === "episode" && selectedConv ? (
        <p className="mb-3 flex flex-wrap items-center gap-x-2 text-xs text-fg-muted">
          <span>
            Episodio aislado:{" "}
            <span className="font-mono text-fg-soft">
              {sessionLabel(selectedConv.session_id)} · {selectedConv.episode_id || "sesión completa"}
            </span>{" "}
            — {selectedConv.evals_count} eval{selectedConv.evals_count === 1 ? "" : "s"}, sin mezclar
            con otros episodios.
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
          últimos 30 días · umbral {threshold} · promedio de TODOS los episodios por día — elegí una
          conversación arriba para aislar un episodio y ver su evolución sin contaminación.
        </p>
      )}

      {episodeSelected && !selectedConv ? (
        convLoading ? (
          <p className="py-6 text-center text-sm text-fg-faint">Cargando episodio…</p>
        ) : (
          <p className="py-6 text-center text-sm text-fg-muted">
            Ese episodio ya no está en la ventana.{" "}
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
          {mode === "episode"
            ? "Este episodio aún no tiene métricas registradas."
            : `Aún no hay histórico para esta suite. Se llena con cada corrida del eval${
                suite === "online" ? " diario sobre conversaciones reales" : " golden"
              }.`}
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
