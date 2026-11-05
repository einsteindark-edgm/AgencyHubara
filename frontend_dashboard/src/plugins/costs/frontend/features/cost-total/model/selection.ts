/**
 * Qué proveedores suma el total — preferencia del operador en ESTE navegador
 * (localStorage), no del servidor ni de la cuenta.
 *
 * Se guardan los EXCLUIDOS (no los incluidos): un proveedor nuevo aparece
 * sumando sin que nadie tenga que marcarlo. Leer o escribir nunca lanza: en
 * modo privado o con el almacenamiento bloqueado, el total suma todo.
 */

import { z } from "zod";

export const EXCLUDED_STORAGE_KEY = "hubara.costs.excludedFromTotal.v1";

const excludedSchema = z.array(z.string());

export function readExcluded(): Set<string> {
  try {
    const raw = localStorage.getItem(EXCLUDED_STORAGE_KEY);
    if (!raw) return new Set();
    const parsed = excludedSchema.safeParse(JSON.parse(raw));
    return parsed.success ? new Set(parsed.data) : new Set();
  } catch {
    return new Set();
  }
}

export function writeExcluded(excluded: Set<string>): void {
  try {
    if (excluded.size === 0) localStorage.removeItem(EXCLUDED_STORAGE_KEY);
    else localStorage.setItem(EXCLUDED_STORAGE_KEY, JSON.stringify([...excluded].sort()));
  } catch {
    // Sin almacenamiento la selección vive solo mientras la pestaña esté abierta.
  }
}

/** Marca o desmarca `id`; devuelve un conjunto nuevo (estado inmutable). */
export function toggled(excluded: Set<string>, id: string): Set<string> {
  const next = new Set(excluded);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  return next;
}
