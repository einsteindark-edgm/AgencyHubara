/**
 * Fila de destinatario — compartida entre el tab Audiencia del inspector y
 * el panel izquierdo del visor: nombre (o "—") + teléfono + chip de color
 * por segmento (tokens del theme via TONE_CLS, nunca hex hardcodeado).
 */

import { segmentTone } from "@plugins/marketing/frontend/entities/audience";
import type { AudienceRecipient } from "@plugins/marketing/frontend/entities/audience";
import { segmentLabel } from "@plugins/marketing/frontend/entities/segment";
import { TONE_CLS } from "@plugins/marketing/frontend/lib/tones";

interface Props {
  recipient: AudienceRecipient;
  active?: boolean;
  onClick: () => void;
}

export function RecipientRow({ recipient, active = false, onClick }: Props) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? "true" : undefined}
      className={
        "flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left transition-colors " +
        (active ? "bg-accent/15" : "hover:bg-white/[0.04]")
      }
    >
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="truncate text-[12px] font-semibold text-fg">
          {recipient.customerName ?? "—"}
        </span>
        <span className="text-[11px] tabular-nums text-fg-muted">
          {recipient.phone}
        </span>
      </span>
      <span
        className={
          "shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold " +
          TONE_CLS[segmentTone(recipient.segment)]
        }
      >
        {segmentLabel(recipient.segment)}
      </span>
    </button>
  );
}
