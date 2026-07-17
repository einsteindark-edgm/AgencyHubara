/**
 * Paso 1 — Objetivo: 3 cards seleccionables (descuento general / producto
 * existente / lanzamiento). El click guarda de inmediato (acción discreta).
 */

import {
  CAMPAIGN_GOALS,
  type CampaignGoal,
} from "@plugins/marketing/frontend/entities/campaign";

interface Props {
  goal: CampaignGoal;
  editable: boolean;
  onPick: (goal: CampaignGoal) => void;
}

export function GoalStep({ goal, editable, onPick }: Props) {
  return (
    <div className="grid grid-cols-3 gap-2.5 max-[900px]:grid-cols-1">
      {CAMPAIGN_GOALS.map((g) => {
        const on = goal === g.key;
        return (
          <button
            key={g.key}
            type="button"
            disabled={!editable}
            aria-pressed={on}
            onClick={() => onPick(g.key)}
            className={
              "rounded-lg border px-3 py-2.5 text-left transition-colors disabled:opacity-60 " +
              (on
                ? "border-accent bg-accent/10"
                : "border-line hover:border-fg-faint hover:bg-white/[0.03]")
            }
          >
            <span className="block text-[12.5px] font-semibold text-fg">{g.label}</span>
            <span className="mt-0.5 block text-[11px] leading-snug text-fg-muted">
              {g.description}
            </span>
          </button>
        );
      })}
    </div>
  );
}
