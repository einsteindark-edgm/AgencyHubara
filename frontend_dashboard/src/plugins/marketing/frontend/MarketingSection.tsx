/**
 * `MarketingSection` — la Page del plugin marketing.
 *
 * Compone los 3 paneles del "Agency Desktop · marketing": sidebar de
 * campañas (izq), builder de 6 pasos (centro) e inspector Preview/Validación
 * (der). Chrome del shell via PluginHost (F7); la campaña seleccionada vive
 * en `useSelection("marketing")` (regla #4 — cross-sección solo via
 * PluginHost). `key={campaign.id}` en el builder resetea su draft local al
 * cambiar de campaña.
 */

import { useMemo } from "react";

import { usePluginHost, useSelection } from "@/shared/sdk";
import { Icon } from "@/shared/ui";

import {
  useCampaigns,
  useCreateCampaign,
} from "@plugins/marketing/frontend/entities/campaign";
import { CampaignsList } from "@plugins/marketing/frontend/features/campaigns-list";
import { CampaignBuilder } from "@plugins/marketing/frontend/features/campaign-builder";
import { CampaignInspector } from "@plugins/marketing/frontend/features/campaign-inspector";
import { apiErrorDetail } from "@plugins/marketing/frontend/lib/format";

export function MarketingSection() {
  const { showSidebar, showInspector } = usePluginHost();
  const [selectedId, setSelectedId] = useSelection("marketing");
  const { data: campaigns = [] } = useCampaigns();
  // La creación desde el empty state es orquestación page-level (la lista
  // trae su propio botón "Nueva campaña" para el flujo normal).
  const create = useCreateCampaign();

  // Fallback en render: si la selección no existe (aún nada seleccionado o
  // campaña borrada), cae a la primera de la lista — nunca un panel vacío
  // teniendo campañas.
  const campaign = useMemo(
    () => campaigns.find((c) => c.id === selectedId) ?? campaigns[0] ?? null,
    [campaigns, selectedId],
  );

  return (
    <>
      {showSidebar && (
        <CampaignsList
          campaigns={campaigns}
          selectedId={campaign?.id ?? null}
          onSelect={setSelectedId}
          onCreated={setSelectedId}
        />
      )}

      <main className="flex min-w-0 flex-1 flex-col overflow-hidden bg-canvas">
        {campaign ? (
          <CampaignBuilder key={campaign.id} campaign={campaign} />
        ) : (
          <div className="m-auto flex max-w-sm flex-col items-center gap-3 text-center">
            <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-accent/15 text-accent-fg">
              <Icon.send />
            </span>
            <h1 className="text-[15px] font-bold tracking-tight text-fg">
              Campañas de WhatsApp
            </h1>
            <p className="text-[12.5px] leading-relaxed text-fg-muted">
              Armá una campaña con descuento o lanzamiento, elegí la audiencia
              por segmentos y envíala por el template aprobado de WhatsApp.
            </p>
            <button
              type="button"
              disabled={create.isPending}
              onClick={() =>
                create.mutate(undefined, { onSuccess: (c) => setSelectedId(c.id) })
              }
              className="rounded-md bg-accent px-4 py-2 text-[12.5px] font-semibold text-white hover:opacity-90 disabled:opacity-50"
            >
              Crear la primera campaña
            </button>
            {create.error ? (
              <p className="text-[11.5px] text-danger">
                {apiErrorDetail(create.error)}
              </p>
            ) : null}
          </div>
        )}
      </main>

      {showInspector && campaign && <CampaignInspector campaign={campaign} />}
    </>
  );
}

export default MarketingSection;
