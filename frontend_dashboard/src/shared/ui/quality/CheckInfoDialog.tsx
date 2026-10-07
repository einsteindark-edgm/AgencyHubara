/**
 * Qué califica un check (operador, 2026-10-07: «APE-01, ENV-06… que salga un
 * popup explicando qué califica ese item»). La abre el código del check en la
 * matriz de cumplimiento. Solo lee el registro: cuándo aplica, qué hace falta
 * para aprobar, su nivel y quién lo revisa.
 */

import { useId } from "react";

import { LEVEL_HELP, qualityStageLabel, type CheckInfoView } from "@/shared/lib";

import { Modal } from "../Modal";
import { LevelPill } from "./QualityChips";

interface Props {
  check: CheckInfoView;
  onClose: () => void;
}

const KIND: Record<string, string> = {
  code: "Lo revisa el código, con la traza del turno.",
  judge: "Lo revisa un juez con IA leyendo la conversación.",
};

export function CheckInfoDialog({ check, onClose }: Props) {
  const titleId = useId();
  const where = [check.family, check.stage ? `etapa ${qualityStageLabel(check.stage)}` : null].filter(Boolean).join(" · ");
  return (
    <Modal open labelledBy={titleId} onClose={onClose} className="min-[760px]:max-w-[600px]!">
      <div className="flex items-start gap-3 border-b border-line bg-titlebar px-4 py-3">
        <div className="min-w-0">
          <h2 id={titleId} className="m-0 text-[15px] font-semibold">
            {`${check.id} · ${check.name}`}
          </h2>
          {where ? <p className="m-0 mt-0.5 text-xs text-fg-muted">{where}</p> : null}
        </div>
        <button
          type="button"
          aria-label="Cerrar"
          onClick={onClose}
          className="ml-auto h-8 w-8 flex-none rounded-lg border border-line bg-white/[0.06] text-base text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          ✕
        </button>
      </div>
      <dl className="m-0 grid gap-3 overflow-y-auto px-4 py-3 text-[13px]">
        <div>
          <dt className="text-[11px] font-semibold uppercase tracking-wider text-fg-faint">Cuándo se califica</dt>
          <dd className="m-0 mt-0.5 text-fg-soft">{check.applies || "—"}</dd>
        </div>
        <div>
          <dt className="text-[11px] font-semibold uppercase tracking-wider text-fg-faint">Qué tiene que pasar para aprobar</dt>
          <dd className="m-0 mt-0.5 text-fg-soft">{check.rule || "—"}</dd>
        </div>
        <div>
          <dt className="text-[11px] font-semibold uppercase tracking-wider text-fg-faint">Si falla</dt>
          <dd className="m-0 mt-0.5 flex items-center gap-2 text-fg-soft">
            <LevelPill level={check.level} />
            <span>{LEVEL_HELP[check.level]}</span>
          </dd>
        </div>
        {KIND[check.kind] ? (
          <div>
            <dt className="text-[11px] font-semibold uppercase tracking-wider text-fg-faint">Quién lo revisa</dt>
            <dd className="m-0 mt-0.5 text-fg-soft">{KIND[check.kind]}</dd>
          </div>
        ) : null}
      </dl>
    </Modal>
  );
}

export default CheckInfoDialog;
