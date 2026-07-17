/**
 * Picker de producto (dropdown buscable sobre GET /products): thumbnail,
 * título, sku, categoría y precio COP; busca por nombre/sku/categoría.
 */

import { useState } from "react";

import { Icon } from "@/shared/ui";

import {
  matchesProductQuery,
  useProducts,
  type CatalogProduct,
} from "@plugins/marketing/frontend/entities/product";
import { fmtCop } from "@plugins/marketing/frontend/lib/format";

interface Props {
  value: string | null;
  editable: boolean;
  onPick: (handle: string) => void;
}

export function ProductPicker({ value, editable, onPick }: Props) {
  const { data: products = [], isPending } = useProducts();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");

  const selected = products.find((p) => p.handle === value) ?? null;
  const filtered = products.filter((p) => matchesProductQuery(p, query));

  return (
    <div className="relative">
      <button
        type="button"
        disabled={!editable}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2.5 rounded-lg border border-line px-3 py-2 text-left hover:border-fg-faint disabled:opacity-60"
      >
        {selected ? (
          <>
            <Thumb product={selected} />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[12.5px] font-semibold text-fg">
                {selected.title}
              </span>
              <span className="block truncate text-[10.5px] text-fg-muted">
                {[selected.sku, selected.category].filter(Boolean).join(" · ") || "—"}
              </span>
            </span>
            {selected.priceAmount !== null ? (
              <span className="shrink-0 text-[12px] font-semibold tabular-nums text-fg">
                {fmtCop(selected.priceAmount)}
              </span>
            ) : null}
          </>
        ) : (
          <span className="flex-1 text-[12.5px] text-fg-muted">
            {isPending ? "Cargando catálogo…" : "Elegir producto del catálogo…"}
          </span>
        )}
        <span className="shrink-0 text-fg-faint">
          <Icon.caret />
        </span>
      </button>

      {open ? (
        <div className="absolute inset-x-0 top-full z-20 mt-1 overflow-hidden rounded-lg border border-line bg-canvas shadow-xl">
          <div className="flex items-center gap-2 border-b border-line px-3 py-2 text-fg-muted">
            <Icon.search />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Buscar por nombre, SKU o categoría…"
              className="flex-1 bg-transparent text-[12px] text-fg outline-none placeholder:text-fg-faint"
              aria-label="Buscar producto"
            />
          </div>
          <ul className="max-h-56 overflow-y-auto py-1">
            {filtered.map((p) => (
              <li key={p.handle}>
                <button
                  type="button"
                  onClick={() => {
                    onPick(p.handle);
                    setOpen(false);
                    setQuery("");
                  }}
                  className="flex w-full items-center gap-2.5 px-3 py-1.5 text-left hover:bg-white/[0.05]"
                >
                  <Thumb product={p} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[12px] font-medium text-fg">
                      {p.title}
                    </span>
                    <span className="block truncate text-[10.5px] text-fg-muted">
                      {[p.sku, p.category].filter(Boolean).join(" · ") || "—"}
                    </span>
                  </span>
                  {p.priceAmount !== null ? (
                    <span className="shrink-0 text-[11.5px] tabular-nums text-fg-soft">
                      {fmtCop(p.priceAmount)}
                    </span>
                  ) : null}
                </button>
              </li>
            ))}
            {filtered.length === 0 ? (
              <li className="px-3 py-3 text-center text-[11.5px] text-fg-faint">
                {isPending ? "Cargando…" : "Sin resultados en el catálogo."}
              </li>
            ) : null}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function Thumb({ product }: { product: CatalogProduct }) {
  if (product.thumbnail) {
    return (
      <img
        src={product.thumbnail}
        alt=""
        className="h-8 w-8 shrink-0 rounded-md border border-line object-cover"
      />
    );
  }
  return (
    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-line bg-line/40 text-fg-faint">
      <Icon.img />
    </span>
  );
}
