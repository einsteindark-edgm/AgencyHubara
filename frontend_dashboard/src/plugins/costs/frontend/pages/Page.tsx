/**
 * Sección Costos — cuánto cuesta el sistema productivo en un rango de fechas:
 * DeepSeek, Jev y Gemini con lo que cada conversación registró; WhatsApp con
 * la cifra de Meta y Temporal Cloud con su uso diario × su tarifa.
 *
 * Por defecto las últimas dos semanas (el rango lo manda el backend junto con
 * el catálogo). Cada tarjeta es su propia consulta: una lenta o caída no frena
 * a las demás ni al total, que dice qué le falta. Solo composición: los datos
 * vienen de la entity local, que solo llama a `/api/costs/*`.
 */

import { useState } from "react";

import {
  costErrorDetail,
  totalEntries,
  useCostCatalog,
  useProviderReports,
  type CostRange,
} from "@plugins/costs/frontend/entities/cost-report";
import { CostRangePicker } from "@plugins/costs/frontend/features/cost-range";
import { CostTotalCard } from "@plugins/costs/frontend/features/cost-total";
import { ProviderCostCard } from "@plugins/costs/frontend/features/provider-cost";

export default function Page() {
  const catalog = useCostCatalog();
  const [picked, setPicked] = useState<CostRange | null>(null);
  const data = catalog.data;
  const range = picked ?? (data ? { start: data.defaultStart, end: data.defaultEnd } : null);
  const providers = data?.providers ?? [];
  const queries = useProviderReports(
    providers.map((p) => p.id),
    range,
  );
  const fxNote = queries.find((q) => q.data?.fxNote)?.data?.fxNote ?? null;

  return (
    <section
      aria-label="Costos"
      className="flex h-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-win-bg text-fg"
    >
      <header className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-line bg-titlebar px-4 py-2.5">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
          <h1 className="m-0 text-[13px] font-semibold">Costos</h1>
          <p className="m-0 text-xs text-fg-muted">
            Cuánto cuesta el sistema productivo en las fechas que elijas.
          </p>
        </div>
        {data && range ? (
          <CostRangePicker value={range} onChange={setPicked} today={data.today} maxDays={data.maxDays} />
        ) : null}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto bg-canvas p-4">
        {catalog.isPending ? (
          <p className="m-0 text-[12.5px] text-fg-muted">Cargando los proveedores…</p>
        ) : !data || !range ? (
          <div role="alert" className="flex flex-col gap-1">
            <p className="m-0 text-[12.5px] font-semibold text-danger">No se pudo abrir la pestaña de costos.</p>
            <p className="m-0 text-[12px] text-fg-muted">
              {costErrorDetail(catalog.error)} Si dice 404, el servicio de costos no está activado en este
              entorno (ENABLED_PLUGINS).
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            <CostTotalCard entries={totalEntries(providers, queries)} range={range} fxNote={fxNote} />
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 2xl:grid-cols-3">
              {providers.map((provider, i) => (
                <ProviderCostCard key={provider.id} provider={provider} query={queries[i]} range={range} />
              ))}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
