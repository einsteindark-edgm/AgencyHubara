/**
 * Sidebar de la central de cupones: header con contador + "Nuevo cupón" (el
 * Page abre el formulario de alta), chips de filtro por estado y cards con
 * código, %, estado y lo que queda del cupo por unidad. La lista llega por
 * props desde el Page (que la lee de `useCoupons`).
 */

import { useMemo, useState, type ReactNode } from "react";

import { Icon } from "@/shared/ui";

import {
  COUPON_STATE_META,
  couponUnitsLabel,
  type Coupon,
  type CouponState,
} from "@plugins/marketing/frontend/entities/coupon";
import { TONE_CLS } from "@plugins/marketing/frontend/lib/tones";

type StateFilter = "all" | CouponState;

const FILTERS: { key: StateFilter; label: string }[] = [
  { key: "all", label: "Todos" },
  { key: "active", label: "Activos" },
  { key: "scheduled", label: "Programados" },
  { key: "paused", label: "Pausados" },
  { key: "draft", label: "Borradores" },
  { key: "expired", label: "Vencidos" },
];

interface Props {
  coupons: Coupon[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  /** El Page muestra el formulario de alta. */
  onNew: () => void;
  /** Aviso sobre la lista (p. ej. Medusa no responde). */
  notice?: string | null;
  /** La lista todavía no llegó: ni "0" ni "Sin cupones" (D9). */
  loading?: boolean;
  /** Slot arriba del header (el selector Campañas | Cupones del Page). */
  header?: ReactNode;
}

export function CouponsList({
  coupons,
  selectedId,
  onSelect,
  onNew,
  notice,
  loading = false,
  header,
}: Props) {
  const [filter, setFilter] = useState<StateFilter>("all");

  const counts = useMemo(() => {
    const by = (f: StateFilter) =>
      f === "all" ? coupons.length : coupons.filter((c) => c.state === f).length;
    return Object.fromEntries(FILTERS.map((f) => [f.key, by(f.key)])) as Record<
      StateFilter,
      number
    >;
  }, [coupons]);

  const list = useMemo(
    () => (filter === "all" ? coupons : coupons.filter((c) => c.state === filter)),
    [coupons, filter],
  );

  return (
    <aside className="sidebar">
      <div className="side-header">
        {header}
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold tracking-tight text-fg">Cupones</span>
          {loading ? null : (
            <span className="text-[11px] text-fg-faint">{coupons.length}</span>
          )}
          <button
            type="button"
            onClick={onNew}
            className="ml-auto inline-flex items-center gap-1 rounded-md bg-accent px-2 py-1 text-[11px] font-semibold text-white hover:opacity-90"
          >
            <Icon.plus />
            Nuevo cupón
          </button>
        </div>
        {notice ? <p className="text-[11px] text-danger">{notice}</p> : null}

        <div className="side-tabs">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              className={"pill" + (filter === f.key ? " on" : "")}
              onClick={() => setFilter(f.key)}
            >
              {f.label} {loading ? null : <span className="ct">{counts[f.key]}</span>}
            </button>
          ))}
        </div>
      </div>

      <div className="side-list px-1.5">
        {list.map((c) => (
          <CouponCard
            key={c.promotionId}
            coupon={c}
            selected={c.promotionId === selectedId}
            onSelect={onSelect}
          />
        ))}
        {loading ? (
          <p className="px-3 py-4 text-center text-[11.5px] text-fg-muted">Cargando cupones…</p>
        ) : list.length === 0 ? (
          <p className="px-3 py-4 text-center text-[11.5px] text-fg-faint">
            Sin cupones en este estado.
          </p>
        ) : null}
      </div>
    </aside>
  );
}

function CouponCard({
  coupon: c,
  selected,
  onSelect,
}: {
  coupon: Coupon;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  const meta = COUPON_STATE_META[c.state];
  return (
    <button
      type="button"
      onClick={() => onSelect(c.promotionId)}
      className={
        "m-0.5 block w-[calc(100%-4px)] rounded-lg border px-2.5 py-2 text-left transition-colors " +
        (selected
          ? "border-accent bg-accent/10 shadow-[0_0_0_0.5px_var(--color-accent)]"
          : "border-transparent hover:border-line hover:bg-white/[0.03]")
      }
    >
      <div className="flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate font-mono text-[12.5px] font-semibold tracking-wide text-fg">
          {c.code}
        </span>
        {c.percentage !== null ? (
          <span className="shrink-0 rounded bg-accent/15 px-1.5 py-0.5 text-[10px] font-bold text-accent-fg">
            -{c.percentage}%
          </span>
        ) : null}
      </div>
      {c.campaignName && c.campaignName !== c.code ? (
        <div className="mt-0.5 truncate text-[11px] text-fg-muted">{c.campaignName}</div>
      ) : null}

      <div className="mt-1.5 flex items-center gap-2">
        <span
          className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[10px] font-semibold ${TONE_CLS[meta.tone]}`}
        >
          <span className="h-1.5 w-1.5 rounded-full bg-current opacity-80" />
          {meta.label}
        </span>
        {c.units ? (
          <span className="text-[10.5px] tabular-nums text-fg-faint">
            {couponUnitsLabel(c.units)}
          </span>
        ) : null}
        {!c.manageable ? (
          <span className="text-[10.5px] text-fg-faint">Solo lectura</span>
        ) : null}
      </div>
    </button>
  );
}
