/**
 * Productos del carrusel de la campaña: chips ordenados (con ×) + el picker
 * del catálogo para agregar. Meta exige 2..10 tarjetas (una plantilla por
 * cantidad); con 1 producto se avisa y el envío queda bloqueado.
 */

import { Icon } from "@/shared/ui";

import {
  CAROUSEL_MAX_CARDS,
  carouselSizeError,
} from "@plugins/marketing/frontend/entities/campaign";
import { useProducts } from "@plugins/marketing/frontend/entities/product";
import { fmtCop } from "@plugins/marketing/frontend/lib/format";

import { ProductPicker } from "./ProductPicker";

interface Props {
  handles: string[];
  editable: boolean;
  onChange: (handles: string[]) => void;
}

export function CarouselPicker({ handles, editable, onChange }: Props) {
  const { data: products = [] } = useProducts();
  const sizeError = carouselSizeError(handles);
  const full = handles.length >= CAROUSEL_MAX_CARDS;

  return (
    <div className="flex flex-col gap-2">
      <div>
        <span className="text-[11px] font-medium text-fg-muted">
          Carrusel de productos (opcional)
        </span>
        <p className="text-[11px] leading-snug text-fg-faint">
          Cada producto viaja como una tarjeta con su foto, nombre y precio, y el
          botón "Me interesa" que abre la conversación con el bot. Entre 2 y{" "}
          {CAROUSEL_MAX_CARDS} productos.
        </p>
      </div>

      {handles.length > 0 ? (
        <ul className="flex flex-wrap gap-1.5" aria-label="Productos del carrusel">
          {handles.map((handle, index) => {
            const product = products.find((p) => p.handle === handle);
            const title = product?.title ?? handle;
            return (
              <li
                key={handle}
                className="flex items-center gap-1.5 rounded-full border border-line bg-white/[0.04] py-1 pl-2 pr-1 text-[11.5px] text-fg"
              >
                <span className="tabular-nums text-fg-faint">{index + 1}.</span>
                <span className="font-medium">{title}</span>
                {product?.priceAmount !== null && product?.priceAmount !== undefined ? (
                  <span className="tabular-nums text-fg-muted">{fmtCop(product.priceAmount)}</span>
                ) : null}
                <button
                  type="button"
                  disabled={!editable}
                  aria-label={`Quitar ${title} del carrusel`}
                  onClick={() => onChange(handles.filter((h) => h !== handle))}
                  className="flex h-5 w-5 items-center justify-center rounded-full text-fg-muted hover:bg-white/[0.08] hover:text-fg disabled:opacity-50"
                >
                  <Icon.x />
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}

      {!full ? (
        <ProductPicker
          value={null}
          editable={editable}
          placeholder="Agregar producto al carrusel…"
          excludeHandles={handles}
          onPick={(handle) => {
            if (!handles.includes(handle)) onChange([...handles, handle]);
          }}
        />
      ) : null}

      {sizeError ? (
        <p className="text-[11.5px] text-warn">{sizeError}</p>
      ) : null}
    </div>
  );
}
