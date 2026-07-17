/**
 * Modelo de dominio del producto del catálogo (para el picker del builder).
 */

export interface CatalogProduct {
  handle: string;
  title: string;
  sku: string | null;
  category: string | null;
  /** Precio numérico normalizado (COP en la práctica) o null si la variante
   *  no tiene precio en el snapshot. */
  priceAmount: number | null;
  currency: string | null;
  thumbnail: string | null;
}

/** Filtro del picker: por nombre, sku o categoría (case-insensitive). */
export function matchesProductQuery(p: CatalogProduct, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return (
    p.title.toLowerCase().includes(q) ||
    (p.sku ?? "").toLowerCase().includes(q) ||
    (p.category ?? "").toLowerCase().includes(q)
  );
}
