/**
 * Los checks como los entiende el operador (revisión 2026-09-29: «EST-06 ·
 * falla» no le decía nada). Primero lo que falló, por su NOMBRE, con el
 * porqué (la crítica del juez o la evidencia del check de código), lo que
 * dijo el bot y lo que se esperaba; lo que cumplió, plegado; lo que no se
 * puede decidir con un turno solo, contado. El código queda como referencia
 * pequeña (para hablar de un check con el equipo).
 */

import {
  checkView,
  LEVEL_HELP,
  levelLabel,
  type CheckCatalog,
  type CheckLevel,
  type EvalResult,
} from "@plugins/lab/frontend/entities/lab-run";

const LEVEL_RANK: Record<CheckLevel, number> = { critico: 0, mayor: 1, menor: 2 };

const LEVEL_TONE: Record<CheckLevel, string> = {
  critico: "bg-danger-soft text-danger",
  mayor: "bg-warn-soft text-warn",
  menor: "bg-neutral-soft text-fg-muted",
};

export function LevelPill({ level }: { level: CheckLevel }) {
  return (
    <span title={LEVEL_HELP[level]} className={"whitespace-nowrap rounded-full px-1.5 py-[3px] text-[10px] font-semibold leading-none " + LEVEL_TONE[level]}>
      {levelLabel(level)}
    </span>
  );
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

interface Props {
  results: EvalResult[];
  catalog: CheckCatalog | undefined;
  /**
   * `turn`: los checks de un turno. `conversation`: los de toda la
   * conversación (cada fallo dice su turno; lo cumplido cuenta cada check una
   * vez, solo si nunca falló).
   */
  scope?: "turn" | "conversation";
  /** Abre el turno de un fallo (el «turno N» se vuelve un botón). */
  onOpenTurn?: (turn: number) => void;
}

export function CheckResults({ results, catalog, scope = "turn", onOpenTurn }: Props) {
  const whole = scope === "conversation";
  const failing = results
    .filter((r) => r.verdict === "falla")
    .map((r) => ({ r, v: checkView(catalog, r) }))
    .sort((a, b) => LEVEL_RANK[a.v.level] - LEVEL_RANK[b.v.level] || (a.r.turn ?? 0) - (b.r.turn ?? 0));
  const failedIds = new Set(failing.map(({ r }) => r.check_id));
  const seen = new Set<string>();
  const passing = results
    .filter((r) => r.verdict === "pasa")
    .filter((r) => {
      if (!whole) return true;
      if (failedIds.has(r.check_id) || seen.has(r.check_id)) return false;
      seen.add(r.check_id);
      return true;
    })
    .map((r) => ({ r, v: checkView(catalog, r) }));
  const pendingIds = results.filter((r) => r.verdict === "sin_senal").map((r) => r.check_id);
  const pending = whole ? new Set(pendingIds).size : pendingIds.length;

  return (
    <div className="grid gap-2">
      {whole && failing.length === 0 && passing.length > 0 ? <p className="m-0 text-[12.5px] text-ok">No falló ningún check.</p> : null}
      {failing.length > 0 ? (
        <ul aria-label="Lo que falló" className="m-0 grid list-none gap-2 p-0">
          {failing.map(({ r, v }, k) => (
            <li key={`${v.id}:${r.turn ?? "ep"}:${k}`} className="grid gap-1 rounded-lg border border-line bg-white/[0.03] px-3 py-2.5 text-[12.5px]">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span aria-hidden="true" className={"font-semibold " + (v.level === "menor" ? "text-fg-muted" : "text-danger")}>✕</span>
                <b className="font-semibold text-fg">{v.name}</b>
                <LevelPill level={v.level} />
                {v.byJudge ? <span className="text-[11px] text-fg-faint">según el juez</span> : null}
                <span className="ml-auto flex items-center gap-2 text-[11px] text-fg-faint">
                  {whole && r.turn !== null ? (
                    onOpenTurn ? (
                      <button
                        type="button"
                        onClick={() => onOpenTurn(r.turn as number)}
                        className="border-0 bg-transparent p-0 text-[11px] text-accent underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                      >
                        {`turno ${r.turn}`}
                      </button>
                    ) : (
                      <span>{`turno ${r.turn}`}</span>
                    )
                  ) : null}
                  <code title="Código del check" className="font-mono text-[10px]">{v.id}</code>
                </span>
              </div>
              {v.reason ? (
                <p className="m-0 text-fg-soft">
                  <span className="text-fg-faint">Por qué: </span>
                  <span>{v.reason}</span>
                </p>
              ) : null}
              {v.quote ? <p className="m-0 border-l-2 border-line-strong pl-2 italic text-fg-muted">{`«${v.quote}»`}</p> : null}
              {v.rule ? (
                <p className="m-0 text-fg-muted">
                  <span className="text-fg-faint">Se esperaba: </span>
                  <span>{v.rule}</span>
                </p>
              ) : null}
              {v.levelNote ? <p className="m-0 text-[11px] text-fg-faint">{v.levelNote}</p> : null}
            </li>
          ))}
        </ul>
      ) : null}
      {passing.length > 0 ? (
        <details className="text-[12.5px]">
          <summary className="cursor-pointer select-none text-fg-muted">
            {`${whole ? "Cumplió siempre" : "Cumplió"} ${plural(passing.length, "check", "checks")}`}
          </summary>
          <ul className="m-0 mt-1.5 grid list-none gap-1 p-0">
            {passing.map(({ r, v }, k) => (
              <li key={`${v.id}:${r.turn ?? "ep"}:${k}`} className="flex items-center gap-2 text-fg-soft">
                <span aria-hidden="true" className="text-ok">✓</span>
                <span>{v.name}</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      {pending > 0 ? (
        <p className="m-0 text-[11.5px] text-fg-faint">
          {whole
            ? pending === 1
              ? "1 check depende de lo que pasó después de cada turno y no se decidió."
              : `${pending} checks dependen de lo que pasó después de cada turno y no se decidieron.`
            : pending === 1
              ? "1 check no se puede decidir con este turno solo: depende de lo que pase después."
              : `${pending} checks no se pueden decidir con este turno solo: dependen de lo que pase después.`}
        </p>
      ) : null}
    </div>
  );
}
