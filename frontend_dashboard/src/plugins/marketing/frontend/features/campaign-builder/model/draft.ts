/**
 * Draft del builder — UI state colocado (regla #3): los campos editables de
 * la campaña, seed desde el server state al montar (`key={campaign.id}` en el
 * Page resetea al cambiar de campaña). El guardado es explícito (PUT al
 * blur/botón) — nunca autosave por tecla.
 */

import type {
  Campaign,
  CampaignGoal,
  CampaignMessage,
  CampaignPatch,
} from "@plugins/marketing/frontend/entities/campaign";

export interface CampaignDraft {
  name: string;
  goal: CampaignGoal;
  percent: number;
  couponCode: string;
  validUntil: string;
  productHandle: string | null;
  segments: string[];
  message: CampaignMessage;
}

export function draftFromCampaign(c: Campaign): CampaignDraft {
  return {
    name: c.name,
    goal: c.goal,
    percent: c.percent,
    couponCode: c.couponCode,
    validUntil: c.validUntil,
    productHandle: c.productHandle,
    segments: [...c.segments],
    message: { ...c.message },
  };
}

/** El PUT viaja con el set editable completo — el backend mergea el message
 *  y normaliza el cupón a mayúsculas. */
export function draftToPatch(d: CampaignDraft): CampaignPatch {
  return {
    name: d.name,
    goal: d.goal,
    percent: d.percent,
    couponCode: d.couponCode,
    validUntil: d.validUntil,
    productHandle: d.productHandle,
    segments: [...d.segments],
    message: { ...d.message },
  };
}
