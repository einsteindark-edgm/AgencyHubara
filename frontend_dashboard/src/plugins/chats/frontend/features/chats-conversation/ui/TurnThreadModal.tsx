/**
 * Hilo de un turno del bot en Chats (plan del laboratorio PR 17, diseño
 * §11): el mismo diagrama de secuencia del Laboratorio, con lo que pasó de
 * verdad en producción. Diagrama a la izquierda, detalle del paso a la
 * derecha; en el celular el diagrama se vuelve una lista (`SequenceTrace`).
 * Solo lectura: no toca la conversación.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { useTurnThread } from "@plugins/chats/frontend/entities/turn-trace";
import { layoutSequence, type TraceStep } from "@/shared/lib";
import { Modal, SequenceTrace, TraceStepDetail } from "@/shared/ui";

interface Props {
  sid: string;
  turnKey: string;
  onClose: () => void;
}

const TITLE_ID = "chats-turn-thread-title";

const MODE_LABEL: Record<string, string> = {
  shadow: "bot nuevo · sombra",
  canary: "bot nuevo · canary",
  on: "bot nuevo · encendido",
};

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function TurnThreadModal({ sid, turnKey, onClose }: Props) {
  const thread = useTurnThread(sid, turnKey);
  const [selected, setSelected] = useState(0);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
  }, []);

  const steps = useMemo(() => (thread.data?.steps ?? []) as TraceStep[], [thread.data]);
  const layout = useMemo(() => layoutSequence(steps, { classifierLabel: "Clasificador" }), [steps]);
  const row = layout.rows[Math.min(selected, Math.max(layout.rows.length - 1, 0))];

  const trace = thread.data?.trace ?? {};
  const turn = num(trace.turn);
  const started = num(trace.turn_started_ms);
  const mode = typeof trace.mode === "string" ? MODE_LABEL[trace.mode] : undefined;
  const meta = [
    started !== null
      ? new Intl.DateTimeFormat("es-CO", {
          timeZone: "America/Bogota",
          day: "numeric",
          month: "short",
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hour12: false,
        }).format(new Date(started))
      : "",
    mode ?? "",
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <Modal open labelledBy={TITLE_ID} onClose={onClose}>
      <div className="flex flex-wrap items-start gap-3 border-b border-line bg-titlebar px-4 py-3">
        <div>
          <p id={TITLE_ID} className="m-0 text-[15px] font-semibold">
            {turn !== null ? `Hilo del turno ${turn}` : "Hilo del turno"}
          </p>
          {meta ? <div className="text-xs tabular-nums text-fg-muted">{meta}</div> : null}
        </div>
        <button
          ref={closeRef}
          type="button"
          aria-label="Cerrar el hilo"
          onClick={onClose}
          className="ml-auto h-8 w-8 flex-none rounded-lg border border-line bg-white/[0.06] text-base text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          ✕
        </button>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 overflow-auto min-[760px]:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)] min-[760px]:overflow-hidden">
        <div className="bg-canvas p-3 min-[760px]:overflow-auto min-[760px]:border-r min-[760px]:border-line min-[760px]:px-2 min-[760px]:pb-4 min-[760px]:pt-2">
          {thread.isPending ? <p className="p-3 text-[12.5px] text-fg-muted">Cargando la traza…</p> : null}
          {thread.isError ? <p className="p-3 text-[12.5px] text-fg-muted">No se pudo leer la traza de este turno.</p> : null}
          {thread.isSuccess && layout.rows.length === 0 ? (
            <p className="p-3 text-[12.5px] text-fg-muted">La traza de este turno no trae pasos.</p>
          ) : null}
          {thread.isSuccess && layout.rows.length > 0 ? (
            <SequenceTrace layout={layout} selected={row?.index ?? 0} onSelect={setSelected} idPrefix="chats-seq" />
          ) : null}
        </div>
        <div aria-live="polite" className="bg-inspector px-[18px] py-4 min-[760px]:overflow-auto">
          {thread.isSuccess && row ? <TraceStepDetail step={steps[row.stepIndex]} row={row} lanes={layout.lanes} /> : null}
        </div>
      </div>

      {thread.data?.fidelity === "v1" ? (
        <div className="border-t border-line bg-titlebar px-4 py-2.5 text-[12.5px] text-fg-soft">
          Traza v1: sin tiempos y con las guardas sin orden.
        </div>
      ) : null}
    </Modal>
  );
}
