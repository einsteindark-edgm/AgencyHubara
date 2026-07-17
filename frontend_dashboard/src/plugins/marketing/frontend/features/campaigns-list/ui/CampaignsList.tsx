/**
 * Sidebar de campañas de marketing: header con contador + "Nueva campaña"
 * (POST y el Page selecciona), pills de filtro por estado y cards con
 * nombre, badge de %, chips de segmentos y pill de estado. Para campañas
 * enviadas, el resumen viene del `send_result` que ya viaja en la lista
 * (las stats de atribución viven en el inspector — un fetch por campaña acá
 * martillaría el vault).
 */

import { useMemo, useState } from "react";

import { Icon } from "@/shared/ui";

import {
  CAMPAIGN_STATUS_META,
  useCreateCampaign,
  type Campaign,
  type CampaignStatus,
} from "@plugins/marketing/frontend/entities/campaign";
import { segmentLabel } from "@plugins/marketing/frontend/entities/segment";
import { apiErrorDetail } from "@plugins/marketing/frontend/lib/format";
import { TONE_CLS } from "@plugins/marketing/frontend/lib/tones";

type StatusFilter = "all" | Exclude<CampaignStatus, "failed">;

const FILTERS: { key: StatusFilter; label: string }[] = [
  { key: "all", label: "Todas" },
  { key: "draft", label: "Borrador" },
  { key: "scheduled", label: "Programada" },
  { key: "sending", label: "Enviando" },
  { key: "sent", label: "Enviada" },
];

interface Props {
  campaigns: Campaign[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  /** El Page selecciona la campaña recién creada (useSelection). */
  onCreated: (id: string) => void;
}

export function CampaignsList({ campaigns, selectedId, onSelect, onCreated }: Props) {
  const [filter, setFilter] = useState<StatusFilter>("all");
  const create = useCreateCampaign();

  const counts = useMemo(() => {
    const by = (s: StatusFilter) =>
      s === "all" ? campaigns.length : campaigns.filter((c) => c.status === s).length;
    return Object.fromEntries(FILTERS.map((f) => [f.key, by(f.key)])) as Record<
      StatusFilter,
      number
    >;
  }, [campaigns]);

  const list = useMemo(
    () => (filter === "all" ? campaigns : campaigns.filter((c) => c.status === filter)),
    [campaigns, filter],
  );

  return (
    <aside className="sidebar">
      <div className="side-header">
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold tracking-tight text-fg">Campañas</span>
          <span className="text-[11px] text-fg-faint">{campaigns.length}</span>
          <button
            type="button"
            className="ml-auto inline-flex items-center gap-1 rounded-md bg-accent px-2 py-1 text-[11px] font-semibold text-white hover:opacity-90 disabled:opacity-50"
            disabled={create.isPending}
            onClick={() =>
              create.mutate(undefined, { onSuccess: (c) => onCreated(c.id) })
            }
          >
            <Icon.plus />
            Nueva campaña
          </button>
        </div>
        {create.error ? (
          <p className="text-[11px] text-danger">{apiErrorDetail(create.error)}</p>
        ) : null}

        <div className="side-tabs">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              className={"pill" + (filter === f.key ? " on" : "")}
              onClick={() => setFilter(f.key)}
            >
              {f.label} <span className="ct">{counts[f.key]}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="side-list px-1.5">
        {list.map((c) => (
          <CampaignCard
            key={c.id}
            campaign={c}
            selected={c.id === selectedId}
            onSelect={onSelect}
          />
        ))}
        {list.length === 0 ? (
          <p className="px-3 py-4 text-center text-[11.5px] text-fg-faint">
            Sin campañas en este estado.
          </p>
        ) : null}
      </div>
    </aside>
  );
}

function CampaignCard({
  campaign: c,
  selected,
  onSelect,
}: {
  campaign: Campaign;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  const meta = CAMPAIGN_STATUS_META[c.status];
  return (
    <button
      type="button"
      onClick={() => onSelect(c.id)}
      className={
        "m-0.5 block w-[calc(100%-4px)] rounded-lg border px-2.5 py-2 text-left transition-colors " +
        (selected
          ? "border-accent bg-accent/10 shadow-[0_0_0_0.5px_var(--color-accent)]"
          : "border-transparent hover:border-line hover:bg-white/[0.03]")
      }
    >
      <div className="flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-[12.5px] font-semibold tracking-tight text-fg">
          {c.name || "Sin título"}
        </span>
        {c.percent > 0 ? (
          <span className="shrink-0 rounded bg-accent/15 px-1.5 py-0.5 text-[10px] font-bold text-accent-fg">
            -{c.percent}%
          </span>
        ) : null}
      </div>

      {c.segments.length > 0 ? (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {c.segments.map((s) => (
            <span
              key={s}
              className="rounded-full bg-white/[0.06] px-1.5 py-0.5 text-[9.5px] font-medium text-fg-soft"
            >
              {segmentLabel(s)}
            </span>
          ))}
        </div>
      ) : null}

      <div className="mt-1.5 flex items-center gap-2">
        <span
          className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[10px] font-semibold ${TONE_CLS[meta.tone]}`}
        >
          <span className="h-1.5 w-1.5 rounded-full bg-current opacity-80" />
          {meta.label}
        </span>
        {c.status === "sent" && c.sendResult ? (
          <span className="text-[10.5px] tabular-nums text-fg-faint">
            {c.sendResult.sent} enviados
            {c.sendResult.failed.length > 0
              ? ` · ${c.sendResult.failed.length} fallidos`
              : ""}
          </span>
        ) : null}
      </div>
    </button>
  );
}
