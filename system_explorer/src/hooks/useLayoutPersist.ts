// Persistencia de posiciones de nodos en localStorage.
//
// Key strategy: hashing simple del id-set del grafo, así si cambian los plugins,
// el layout se invalida (en lugar de aplicar posiciones obsoletas a nodos
// nuevos / huérfanos).

import type { Node } from "@xyflow/react";

const STORAGE_KEY_PREFIX = "system-explorer:layout:";

function hashIds(ids: readonly string[]): string {
  // Hash determinista, sin collision-safety (no es seguridad — es invalidación).
  // Usa djb2 hash-mod-2^32.
  let h = 5381;
  for (const id of ids) {
    for (let i = 0; i < id.length; i++) {
      h = (h * 33) ^ id.charCodeAt(i);
    }
  }
  return (h >>> 0).toString(16);
}

export function layoutKey(nodeIds: readonly string[]): string {
  return `${STORAGE_KEY_PREFIX}${hashIds(nodeIds)}`;
}

type PositionMap = Record<string, { x: number; y: number }>;

export function loadLayout(nodeIds: readonly string[]): PositionMap | null {
  try {
    const raw = localStorage.getItem(layoutKey(nodeIds));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as unknown;
    if (typeof parsed !== "object" || parsed === null) return null;
    return parsed as PositionMap;
  } catch {
    return null;
  }
}

export function saveLayout(nodeIds: readonly string[], nodes: Node[]): void {
  try {
    const positions: PositionMap = {};
    for (const n of nodes) {
      positions[n.id] = { x: n.position.x, y: n.position.y };
    }
    localStorage.setItem(layoutKey(nodeIds), JSON.stringify(positions));
  } catch {
    // Best-effort — si localStorage está lleno (quota), silently skip.
    // El user puede usar el botón "Reset layout" para limpiar.
  }
}

export function clearAllLayouts(): void {
  try {
    const toRemove: string[] = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.startsWith(STORAGE_KEY_PREFIX)) toRemove.push(k);
    }
    for (const k of toRemove) localStorage.removeItem(k);
  } catch {
    // ignore
  }
}
