import * as fs from "node:fs/promises";
import * as path from "node:path";
import { parse } from "yaml";
import { Lado, SuiteDef, Tipo } from "./suites";

export interface PlanSelector {
  /** matchea contra `suite.lado` O `suite.tipo` (mismo campo en el YAML por simplicidad). */
  tag?: Lado | Tipo;
  /** matchea el id exacto de una suite, p. ej. "graphagents/graphs". */
  suite?: string;
}

export interface HubaraPlan {
  id: string;
  name: string;
  description?: string;
  include: PlanSelector[];
  exclude: PlanSelector[];
  failFast: boolean;
  parallel: boolean;
}

interface PlanFile {
  name?: string;
  description?: string;
  include?: Array<{ tag?: string; suite?: string }>;
  exclude?: Array<{ tag?: string; suite?: string }>;
  options?: { failFast?: boolean; parallel?: boolean };
}

/** Descubre `*.hubaraplan.yaml` en el directorio dado (no recursivo — carpeta
 * plana `test-plans/`). Un plan roto se salta con un warning, no tumba el
 * resto (mismo principio que `loadSeams`). */
export async function discoverPlans(dir: string): Promise<HubaraPlan[]> {
  let entries: string[];
  try {
    entries = await fs.readdir(dir);
  } catch {
    return [];
  }
  const plans: HubaraPlan[] = [];
  for (const entry of entries) {
    if (!entry.endsWith(".hubaraplan.yaml")) {
      continue;
    }
    const id = entry.replace(/\.hubaraplan\.yaml$/, "");
    try {
      const raw = await fs.readFile(path.join(dir, entry), "utf-8");
      const doc = parse(raw) as PlanFile;
      plans.push({
        id,
        name: doc.name ?? id,
        description: doc.description,
        include: (doc.include ?? []).map(toSelector),
        exclude: (doc.exclude ?? []).map(toSelector),
        failFast: doc.options?.failFast ?? false,
        parallel: doc.options?.parallel ?? true,
      });
    } catch {
      // plan roto: se ignora, no rompe el descubrimiento de los demás.
      continue;
    }
  }
  return plans.sort((a, b) => a.name.localeCompare(b.name));
}

function toSelector(raw: { tag?: string; suite?: string }): PlanSelector {
  return { tag: raw.tag as Lado | Tipo | undefined, suite: raw.suite };
}

function matchesSelector(suite: SuiteDef, sel: PlanSelector): boolean {
  if (sel.suite) {
    return suite.id === sel.suite;
  }
  if (sel.tag) {
    return suite.lado === sel.tag || suite.tipo === sel.tag;
  }
  return false;
}

/** Las suites que un plan selecciona: include menos exclude. Un plan sin
 * `include` no selecciona nada (explícito > implícito — nunca "todo por
 * accidente"). */
export function suitesForPlan(plan: HubaraPlan, allSuites: SuiteDef[]): SuiteDef[] {
  return allSuites.filter(
    (s) => plan.include.some((sel) => matchesSelector(s, sel)) && !plan.exclude.some((sel) => matchesSelector(s, sel)),
  );
}
