import * as fs from "node:fs/promises";
import { parse } from "yaml";
import { Seam } from "./graphOps";

interface SeamsFile {
  seams?: Array<{ id: string; from: string; to: string; label?: string; kind?: string }>;
}

/** Lee `seams.yaml` (raíz de la extensión). Si falta o está roto, devuelve []
 * — el workspace se renderiza igual, solo sin costuras (nunca crashea). */
export async function loadSeams(seamsPath: string): Promise<Seam[]> {
  let raw: string;
  try {
    raw = await fs.readFile(seamsPath, "utf-8");
  } catch {
    return [];
  }
  try {
    const doc = parse(raw) as SeamsFile;
    if (!doc?.seams || !Array.isArray(doc.seams)) {
      return [];
    }
    return doc.seams
      .filter((s) => typeof s.id === "string" && typeof s.from === "string" && typeof s.to === "string")
      .map((s) => ({ id: s.id, from: s.from, to: s.to, label: s.label, kind: s.kind }));
  } catch {
    return [];
  }
}
