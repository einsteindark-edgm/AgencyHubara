/**
 * Panel de campañas Meta con DATOS REALES (Marketing API vía `/api/ads/meta/insights`)
 * + gestión (pausar/activar) con confirm inline de dos pasos (regla 6).
 *
 * Solo se monta cuando hay conexión (si no, devuelve null y el botón "Conectar con
 * Meta" del header se ocupa). Server state SOLO en TanStack Query; los derivados
 * (CPC, costo/conversación) se computan en render con funciones puras (regla 5);
 * el estado de la mutation se DERIVA de la mutation, no se duplica en useState.
 */

import { useState } from "react";

import {
  useMetaConnection,
  useMetaInsights,
  useSetCampaignStatus,
  type MetaInsightsCampaign,
} from "@plugins/ads/frontend/entities/meta-connection";

const cop = new Intl.NumberFormat("es-CO", {
  style: "currency",
  currency: "COP",
  maximumFractionDigits: 0,
});
const num = new Intl.NumberFormat("es-CO");

export function MetaInsightsPanel() {
  const { data: conn } = useMetaConnection();
  const connected = conn?.connected ?? false;
  const { data, isLoading, isError } = useMetaInsights({ days: 30 }, connected);

  if (!connected) return null;
  if (isLoading) {
    return <div className="px-4 py-3 text-sm text-gray-500">Cargando métricas de Meta…</div>;
  }
  if (isError) {
    return (
      <div className="px-4 py-3 text-sm text-red-600">
        No se pudieron cargar las métricas de Meta.
      </div>
    );
  }

  const campaigns = data?.campaigns ?? [];
  if (campaigns.length === 0) {
    return (
      <div className="px-4 py-3 text-sm text-gray-500">
        Conectado a Meta — sin campañas con datos en la ventana.
      </div>
    );
  }

  return (
    <section className="mx-4 my-3 rounded-lg border border-gray-200">
      <header className="flex items-center justify-between border-b border-gray-100 px-4 py-2">
        <h3 className="text-sm font-semibold">Campañas Meta (datos reales)</h3>
        <span className="text-xs text-gray-500">
          {data?.since} → {data?.until}
        </span>
      </header>
      <div className="divide-y divide-gray-100">
        {campaigns.map((c) => (
          <CampaignRow key={c.campaignId} c={c} canManage={conn?.canManage ?? false} />
        ))}
      </div>
    </section>
  );
}

function CampaignRow({ c, canManage }: { c: MetaInsightsCampaign; canManage: boolean }) {
  const setStatus = useSetCampaignStatus();
  const [confirming, setConfirming] = useState(false);
  const isActive = c.status === "ACTIVE";
  const target: "PAUSED" | "ACTIVE" = isActive ? "PAUSED" : "ACTIVE";
  const cpc = c.clicks > 0 ? c.spend / c.clicks : null;
  const costPerConv =
    c.messagingConversationsStarted > 0
      ? c.spend / c.messagingConversationsStarted
      : null;

  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-1 px-4 py-2 text-sm">
      <div className="min-w-40 flex-1">
        <div className="font-medium">{c.name}</div>
        <div className="text-xs text-gray-500">{c.objective ?? "—"}</div>
      </div>
      <StatusBadge status={c.status} />
      <Metric label="Gasto" value={cop.format(c.spend)} />
      <Metric label="Clicks" value={num.format(c.clicks)} />
      <Metric label="Impr." value={num.format(c.impressions)} />
      <Metric label="Conv." value={num.format(c.messagingConversationsStarted)} />
      <Metric label="CPC" value={cpc != null ? cop.format(cpc) : "—"} />
      <Metric label="Costo/conv" value={costPerConv != null ? cop.format(costPerConv) : "—"} />
      <div className="ml-auto flex items-center gap-2">
        {setStatus.isError ? (
          <span className="text-xs text-red-600">error</span>
        ) : null}
        {!canManage || c.status == null ? null : confirming ? (
          <>
            <button
              type="button"
              disabled={setStatus.isPending}
              onClick={() =>
                setStatus.mutate(
                  { campaignId: c.campaignId, status: target },
                  { onSettled: () => setConfirming(false) },
                )
              }
              className="rounded bg-gray-900 px-2 py-1 text-xs font-medium text-white disabled:opacity-60"
            >
              {setStatus.isPending
                ? "Aplicando…"
                : `Confirmar ${isActive ? "pausa" : "activación"}`}
            </button>
            <button
              type="button"
              onClick={() => setConfirming(false)}
              className="rounded px-2 py-1 text-xs text-gray-500 hover:text-gray-800"
            >
              Cancelar
            </button>
          </>
        ) : (
          <button
            type="button"
            onClick={() => setConfirming(true)}
            className="rounded border border-gray-300 px-2 py-1 text-xs hover:bg-gray-50"
          >
            {isActive ? "Pausar" : "Activar"}
          </button>
        )}
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="text-right">
      <div className="text-[10px] uppercase tracking-wide text-gray-400">{label}</div>
      <div className="tabular-nums">{value}</div>
    </div>
  );
}

function StatusBadge({ status }: { status: string | null }) {
  const cls =
    status === "ACTIVE"
      ? "bg-green-100 text-green-800"
      : status === "PAUSED"
        ? "bg-amber-100 text-amber-800"
        : "bg-gray-100 text-gray-600";
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${cls}`}>
      {status ?? "—"}
    </span>
  );
}
