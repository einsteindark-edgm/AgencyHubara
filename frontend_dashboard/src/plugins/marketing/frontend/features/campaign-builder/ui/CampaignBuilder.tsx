/**
 * Builder central de la campaña — 6 pasos numerados con check de completado.
 *
 * Estado (reglas #1/#3): el server state vive en TanStack (la campaña llega
 * por props desde el Page, que la lee de `useCampaigns`); el draft editable
 * es UI state local seed desde la campaña (`key={campaign.id}` en el Page
 * resetea al cambiar de selección). Guardado EXPLÍCITO: PUT al blur de cada
 * campo de texto / al click en acciones discretas / al botón Guardar — sin
 * autosave por tecla. Con status sent/sending/failed todo queda read-only
 * (el backend igual responde 409).
 */

import { useRef, useState } from "react";

import { useCampaignAudience } from "@plugins/marketing/frontend/entities/audience";
import {
  CAMPAIGN_STATUS_META,
  goalNeedsProduct,
  goalUsesDiscount,
  isCampaignEditable,
  useCancelCampaign,
  useUpdateCampaign,
  type Campaign,
} from "@plugins/marketing/frontend/entities/campaign";
import {
  recipientsForSelection,
  useSegmentsInfo,
} from "@plugins/marketing/frontend/entities/segment";
import { apiErrorDetail } from "@plugins/marketing/frontend/lib/format";
import { TONE_CLS } from "@plugins/marketing/frontend/lib/tones";

import { draftFromCampaign, draftToPatch, type CampaignDraft } from "../model/draft";
import { AudienceStep } from "./AudienceStep";
import { GoalStep } from "./GoalStep";
import { MessageStep } from "./MessageStep";
import { OfferStep } from "./OfferStep";
import { SendStep } from "./SendStep";
import { StepShell } from "./StepShell";
import { TestSendStep } from "./TestSendStep";

interface Props {
  campaign: Campaign;
}

export function CampaignBuilder({ campaign }: Props) {
  const editable = isCampaignEditable(campaign.status);
  const meta = CAMPAIGN_STATUS_META[campaign.status];

  const [draft, setDraft] = useState<CampaignDraft>(() =>
    draftFromCampaign(campaign),
  );
  const update = useUpdateCampaign(campaign.id);
  const cancel = useCancelCampaign(campaign.id);
  const { data: segmentsInfo } = useSegmentsInfo();
  const sendStepRef = useRef<HTMLElement | null>(null);

  /** Edición local (mientras tipea) — NO pega al backend. */
  const patchDraft = (p: Partial<CampaignDraft>) =>
    setDraft((d) => ({ ...d, ...p }));

  /** Guardado explícito: merge + PUT con el set editable completo. */
  const commit = (p?: Partial<CampaignDraft>) => {
    if (!editable) return;
    const next = { ...draft, ...p };
    setDraft(next);
    update.mutate(draftToPatch(next));
  };

  const toggleSegment = (key: string) => {
    const segments = draft.segments.includes(key)
      ? draft.segments.filter((s) => s !== key)
      : [...draft.segments, key];
    commit({ segments });
  };

  // Completitud por paso — derivada del draft en render.
  const done1 = draft.goal !== "";
  const done2 =
    done1 &&
    (!goalNeedsProduct(draft.goal) || Boolean(draft.productHandle)) &&
    (!goalUsesDiscount(draft.goal) || draft.percent > 0);
  const done3 = draft.message.body.trim() !== "";
  const done4 = draft.segments.length > 0;
  const done5 = campaign.testSends.length > 0;
  const done6 = campaign.status !== "draft" && campaign.status !== "failed";
  const canSend = done1 && done2 && done3 && done4;

  // Total REAL del endpoint de audiencia (incluye la curaduría manual:
  // quitados/agregados a mano); mientras carga, fallback a la estimación
  // por segmento. El costo sigue al mismo número.
  const { data: audience } = useCampaignAudience(
    campaign.id,
    campaign.updatedAtMs,
  );
  const recipients =
    audience?.total ?? recipientsForSelection(segmentsInfo, draft.segments);
  const totalCostMicros = (segmentsInfo?.unitCostUsdMicros ?? 0) * recipients;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex shrink-0 items-center gap-3 border-b border-line px-5 py-3">
        <input
          type="text"
          aria-label="Nombre de la campaña"
          maxLength={120}
          disabled={!editable}
          value={draft.name}
          placeholder="Nombre de la campaña"
          onChange={(e) => patchDraft({ name: e.target.value })}
          onBlur={() => commit()}
          className="min-w-0 flex-1 bg-transparent text-[17px] font-bold tracking-tight text-fg outline-none disabled:opacity-70 placeholder:text-fg-faint"
        />
        <span
          className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-[10.5px] font-semibold ${TONE_CLS[meta.tone]}`}
        >
          <span className="h-1.5 w-1.5 rounded-full bg-current opacity-80" />
          {meta.label}
        </span>
        {update.isPending ? (
          <span className="text-[11px] text-fg-muted">Guardando…</span>
        ) : null}
        {campaign.status === "scheduled" ? (
          <button
            type="button"
            disabled={cancel.isPending}
            onClick={() => cancel.mutate()}
            className="rounded-md border border-warn/40 px-3 py-1.5 text-[12px] font-semibold text-warn hover:bg-warn-soft disabled:opacity-50"
          >
            Cancelar programación
          </button>
        ) : null}
        <button
          type="button"
          disabled={!editable || update.isPending}
          onClick={() => commit()}
          className="rounded-md border border-line px-3 py-1.5 text-[12px] font-semibold text-fg hover:bg-white/[0.05] disabled:opacity-50"
        >
          Guardar
        </button>
        <button
          type="button"
          onClick={() =>
            sendStepRef.current?.scrollIntoView?.({
              behavior: "smooth",
              block: "start",
            })
          }
          className="rounded-md bg-accent px-3 py-1.5 text-[12px] font-semibold text-white hover:opacity-90"
        >
          Enviar
        </button>
      </header>

      {!editable ? (
        <div className="shrink-0 border-b border-line bg-warn-soft px-5 py-2 text-[11.5px] font-medium text-warn">
          Campaña {meta.label.toLowerCase()} — solo lectura
        </div>
      ) : null}
      {update.error ? (
        <div className="shrink-0 border-b border-line bg-danger-soft px-5 py-2 text-[11.5px] text-danger">
          No se pudo guardar: {apiErrorDetail(update.error)}
        </div>
      ) : null}

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-5 py-4">
          <StepShell n={1} title="Objetivo" done={done1}>
            <GoalStep
              goal={draft.goal}
              editable={editable}
              onPick={(goal) => commit({ goal })}
            />
          </StepShell>

          <StepShell n={2} title="Descuento / Producto" done={done2}>
            <OfferStep
              draft={draft}
              editable={editable}
              onPatch={patchDraft}
              onCommit={commit}
            />
          </StepShell>

          <StepShell n={3} title="Mensaje" done={done3}>
            <MessageStep
              draft={draft}
              editable={editable}
              onPatch={patchDraft}
              onCommit={commit}
            />
          </StepShell>

          <StepShell n={4} title="Audiencia" done={done4}>
            <AudienceStep
              selected={draft.segments}
              info={segmentsInfo}
              editable={editable}
              recipients={recipients}
              totalCostMicros={totalCostMicros}
              onToggle={toggleSegment}
            />
          </StepShell>

          <StepShell
            n={5}
            title="Envío de prueba"
            done={done5}
            hint="Opcional pero recomendado"
          >
            <TestSendStep campaign={campaign} editable={editable} />
          </StepShell>

          <StepShell n={6} title="Enviar" done={done6} sectionRef={sendStepRef}>
            <SendStep
              campaign={campaign}
              editable={editable}
              canSend={canSend}
              recipients={recipients}
              totalCostMicros={totalCostMicros}
            />
          </StepShell>
        </div>
      </div>
    </div>
  );
}
