/**
 * Botón "Nueva corrida" y su panel (plan §3.7, diseño §09).
 *
 * Formulario: los bots (A1 siempre va: es el control), las repeticiones (1
 * rápida · 3 para decidir) y el banco (exportar ahora o reusar el último). El
 * costo lo estima el backend con lo marcado; si pasa un tope, "Lanzar
 * corrida" se apaga y se dice cuál. Con una corrida en curso el panel muestra
 * el avance y "Cancelar corrida" pide confirmar en dos pasos (sin diálogos
 * nativos, que no andan en la app Android).
 *
 * Devuelve dos hermanos: el botón (va en la barra de la sección) y el panel
 * (`basis-full`: ocupa su propia línea debajo de la barra).
 */

import { useId, useState, type ReactNode } from "react";

import {
  apiErrorDetail,
  armLabel,
  formatUsd,
  useActiveRun,
  useCancelRun,
  useLabEstimate,
  useLaunchRun,
  type ActiveStatus,
  type LabEstimate,
} from "@plugins/lab/frontend/entities/lab-run";

interface Props {
  /** Banco de la última corrida (para "Reusar el último"); null si no hay. */
  lastBenchId: string | null;
}

const OPTIONAL_ARMS = ["B", "C"] as const;
const STEPS = ["Exportar banco", "Prender la caja", "Correr bots", "Evaluar", "Listo"];

const PHASE_STEP: Record<string, number> = {
  queued: 0,
  exporting: 0,
  ordering: 1,
  starting_box: 1,
  dispatching: 2,
  running: 2,
  cancelling: 2,
  done: 4,
};

function stepOf(status: ActiveStatus): number {
  if (status.box_phase === "evaluating") return 3;
  return PHASE_STEP[status.phase] ?? 0;
}

function capMessage(estimate: LabEstimate): string | null {
  if (estimate.fits) return null;
  if (estimate.reason === "run_cap") return `Pasa el tope por corrida (${formatUsd(estimate.run_cap_usd)}).`;
  if (estimate.reason === "month_cap") return `Pasa lo que queda del mes (${formatUsd(estimate.month_left_usd)}).`;
  return "Pasa un tope de gasto.";
}

function launchErrorMessage(error: unknown): string {
  const { status, message } = apiErrorDetail(error);
  if (status === 504) return "No sé si la corrida arrancó: el servidor tardó en responder. Revisa la corrida en curso antes de volver a lanzar.";
  if (status === 502) return "El servidor del laboratorio no respondió: la corrida NO se lanzó.";
  if (status === 409) return message ?? "Ya hay una corrida en curso.";
  if (status === 422) return message ?? "La corrida pasa un tope de gasto o tiene un valor inválido.";
  return message ?? "No se pudo lanzar la corrida.";
}

const BTN = "whitespace-nowrap rounded-[7px] px-3 py-2 text-xs font-semibold leading-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";
const GHOST = "rounded-[7px] border border-line-strong bg-transparent px-3 py-2 text-xs font-medium leading-none text-fg-soft focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

export function RunLauncher({ lastBenchId }: Props) {
  const panelId = useId();
  const [open, setOpen] = useState(false);
  const activeQuery = useActiveRun();
  const status = activeQuery.data?.active ?? null;
  const progressPct = status && status.turns_total ? Math.min(100, Math.round(((status.turns_done ?? 0) / status.turns_total) * 100)) : null;

  return (
    <>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((v) => !v)}
        className={BTN + " text-white " + (open ? "bg-accent-soft" : "bg-accent")}
      >
        {status ? `Corrida en curso${progressPct !== null ? ` · ${progressPct} %` : ""}` : "Nueva corrida"}
      </button>
      {open ? (
        <div id={panelId} className="order-last grid basis-full gap-3 border-t border-line bg-canvas px-4 py-3.5">
          {status ? <Progress status={status} /> : <LaunchForm lastBenchId={lastBenchId} onClose={() => setOpen(false)} />}
        </div>
      ) : null}
    </>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-[12.5px] text-fg-soft">
      <span className="flex-[0_0_96px] text-[11.5px] text-fg-muted">{label}</span>
      {children}
    </div>
  );
}

function Seg({ label, options, value, onChange }: { label: string; options: Array<{ value: string; text: string; disabled?: boolean }>; value: string; onChange: (v: string) => void }) {
  return (
    <div role="group" aria-label={label} className="inline-flex flex-wrap rounded-lg border border-line bg-white/[0.06] p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={o.value === value}
          disabled={o.disabled}
          onClick={() => onChange(o.value)}
          className={
            "rounded-md border-0 px-2.5 py-[7px] text-xs font-medium leading-none disabled:cursor-not-allowed disabled:opacity-45 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent " +
            (o.value === value ? "bg-accent-soft text-white" : "bg-transparent text-fg-muted")
          }
        >
          {o.text}
        </button>
      ))}
    </div>
  );
}

function LaunchForm({ lastBenchId, onClose }: { lastBenchId: string | null; onClose: () => void }) {
  const [picked, setPicked] = useState<Record<string, boolean>>({ B: true, C: true });
  const [reps, setReps] = useState(1);
  const [bench, setBench] = useState<"new" | "reuse">("new");
  const arms = ["A1", ...OPTIONAL_ARMS.filter((a) => picked[a])];
  const input = { arms, reps, bench: bench === "reuse" && lastBenchId ? lastBenchId : "new" };
  const estimate = useLabEstimate(input, true);
  const launch = useLaunchRun();

  const cap = estimate.data ? capMessage(estimate.data) : null;
  const estimateError = estimate.isError ? apiErrorDetail(estimate.error) : null;
  const canLaunch = estimate.isSuccess && estimate.data.fits && !launch.isPending;

  return (
    <>
      <Row label="Bots">
        <label className="inline-flex cursor-not-allowed items-center gap-1.5 text-fg-faint">
          <input type="checkbox" checked disabled className="accent-accent" /> A1 · {armLabel("A1")} (control)
        </label>
        {OPTIONAL_ARMS.map((a) => (
          <label key={a} className="inline-flex cursor-pointer items-center gap-1.5">
            <input
              type="checkbox"
              checked={!!picked[a]}
              onChange={(e) => setPicked((p) => ({ ...p, [a]: e.target.checked }))}
              className="accent-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            />
            {a} · {armLabel(a)}
          </label>
        ))}
      </Row>
      <Row label="Repeticiones">
        <Seg
          label="Repeticiones"
          value={String(reps)}
          onChange={(v) => setReps(Number(v))}
          options={[
            { value: "1", text: "1 · rápida" },
            { value: "3", text: "3 · decisión" },
          ]}
        />
      </Row>
      <Row label="Banco">
        <Seg
          label="Banco"
          value={bench}
          onChange={(v) => setBench(v as "new" | "reuse")}
          options={[
            { value: "new", text: "Exportar ahora" },
            { value: "reuse", text: lastBenchId ? `Reusar el último (${lastBenchId})` : "Reusar el último (no hay)", disabled: !lastBenchId },
          ]}
        />
      </Row>
      <Row label="Costo">
        <span className="tabular-nums text-fg">
          {estimate.isPending ? "Calculando…" : null}
          {estimate.data
            ? `≈ ${formatUsd(estimate.data.estimate_usd)} estimado · ${estimate.data.turns.toLocaleString("es-CO")} turnos del banco · tope por corrida ${formatUsd(estimate.data.run_cap_usd)} · quedan ${formatUsd(estimate.data.month_left_usd)} del mes`
            : null}
          {estimateError ? (estimateError.message ?? "No se pudo estimar el costo.") : null}
        </span>
      </Row>
      {cap ? <p className="m-0 text-[12.5px] text-warn">{cap}</p> : null}
      {launch.isError ? (
        <p role="alert" className="m-0 text-[12.5px] text-danger">
          {launchErrorMessage(launch.error)}
        </p>
      ) : null}
      {launch.isSuccess ? <p className="m-0 text-[12.5px] text-ok">{`Corrida ${launch.data.run_id} lanzada. Preparando…`}</p> : null}
      <div className="flex flex-wrap justify-end gap-2">
        <button type="button" onClick={onClose} className={GHOST}>
          Cerrar
        </button>
        <button type="button" disabled={!canLaunch} onClick={() => launch.mutate(input)} className={BTN + " bg-accent text-white disabled:cursor-not-allowed disabled:opacity-45"}>
          Lanzar corrida
        </button>
      </div>
    </>
  );
}

function Progress({ status }: { status: ActiveStatus }) {
  const [confirming, setConfirming] = useState(false);
  const cancel = useCancelRun();
  const current = stepOf(status);
  const pct = status.turns_total ? Math.min(100, Math.round(((status.turns_done ?? 0) / status.turns_total) * 100)) : 0;
  const cancelling = status.phase === "cancelling" || cancel.isSuccess;

  return (
    <>
      <ol className="m-0 flex list-none flex-wrap gap-1.5 p-0">
        {STEPS.map((s, i) => (
          <li
            key={s}
            aria-current={i === current ? "step" : undefined}
            className={
              "rounded-full border px-[9px] py-[5px] text-[11.5px] " +
              (i < current ? "border-transparent bg-ok-soft text-ok" : i === current ? "border-accent bg-accent-soft text-white" : "border-line text-fg-muted")
            }
          >
            {s}
          </li>
        ))}
      </ol>
      <div className="h-1.5 overflow-hidden rounded-md bg-white/[0.08]" aria-hidden="true">
        <b className="block h-full bg-accent" style={{ width: `${pct}%` }} />
      </div>
      <Row label="Avance">
        <span className="tabular-nums text-fg">
          {status.turns_total
            ? `${(status.turns_done ?? 0).toLocaleString("es-CO")} de ${status.turns_total.toLocaleString("es-CO")} turnos · ${formatUsd(status.spent_usd ?? 0)} gastados de ≈ ${formatUsd(status.estimate_usd)}`
            : "Preparando la corrida…"}
        </span>
      </Row>
      <Row label="Corrida">
        <span className="tabular-nums">
          {[status.run_id, status.arms.map(armLabel).join(" · "), status.reps ? `${status.reps} ${status.reps === 1 ? "repetición" : "repeticiones"}` : ""].filter(Boolean).join(" · ")}
        </span>
      </Row>
      {status.error ? <p className="m-0 text-[12.5px] text-danger">{status.error}</p> : null}
      {cancel.isError ? (
        <p role="alert" className="m-0 text-[12.5px] text-danger">
          {apiErrorDetail(cancel.error).message ?? "No se pudo pedir la cancelación."}
        </p>
      ) : null}
      <div className="flex flex-wrap items-center justify-end gap-2">
        {cancelling ? (
          <span className="text-[12.5px] text-fg-muted">Cancelando: la caja termina el turno en curso y se apaga.</span>
        ) : confirming ? (
          <>
            <span className="text-[12.5px] text-fg-soft">¿Cancelar la corrida? Lo gastado no se recupera.</span>
            <button type="button" onClick={() => setConfirming(false)} className={GHOST}>
              No
            </button>
            <button type="button" disabled={cancel.isPending} onClick={() => cancel.mutate()} className={BTN + " bg-danger text-white disabled:opacity-45"}>
              Sí, cancelar
            </button>
          </>
        ) : (
          <button type="button" onClick={() => setConfirming(true)} className={GHOST}>
            Cancelar corrida
          </button>
        )}
      </div>
    </>
  );
}
