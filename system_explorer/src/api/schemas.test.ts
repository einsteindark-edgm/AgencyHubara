/**
 * Contrato del boundary del explorer (primer test del proyecto — premortem F-SDK).
 *
 * Regla L-10: un valor/campo nuevo del backend sin su contrato actualizado =
 * sección vacía sin diagnóstico. Acá se fija lo contrario: el schema acepta
 * el payload NUEVO (con `certifications`, F-SDK-5) Y el VIEJO (sin el campo —
 * `.default([])`), y rechaza basura.
 */
import { describe, expect, it } from "vitest";

import { PluginCertificationSchema, SystemGraphSchema } from "./schemas";

const baseGraph = {
  version: "1",
  generated_at: "2026-06-12T00:00:00Z",
  nodes: [
    {
      id: "plugin:eta",
      kind: "plugin",
      plugin_id: "eta",
      label: "eta",
      data: {},
      is_orphan: false,
      orphan_reason: null,
    },
  ],
  edges: [],
  plugins: [
    {
      id: "eta",
      display_name: "ETA",
      version: "0.1.0",
      description: null,
      has_frontend: true,
      has_api: true,
      has_agent: true,
      completeness: "complete",
      node_count: 3,
      orphan_count: 0,
    },
  ],
  stats: {
    total_nodes: 1,
    total_edges: 0,
    total_plugins: 1,
    orphan_count: 0,
    by_kind: { plugin: 1 },
  },
  warnings: [],
};

const certification = {
  plugin_id: "eta",
  archetype: "notifier",
  level: "C2",
  fails: 0,
  warns: 0,
  failed_checks: [],
  warning_checks: [{ code: "P-29", detail: "recomendado: domain/" }],
  sdk: "0.1.0",
  git_sha: "abc1234",
  generated_at: "2026-06-12T00:00:00Z",
};

describe("SystemGraphSchema ↔ backend (F-SDK-5)", () => {
  it("acepta el payload nuevo con certifications", () => {
    const parsed = SystemGraphSchema.parse({
      ...baseGraph,
      certifications: [certification],
    });
    expect(parsed.certifications).toHaveLength(1);
    expect(parsed.certifications[0]?.level).toBe("C2");
  });

  it("tolera backends pre-F-SDK-5 (campo ausente → default [])", () => {
    const parsed = SystemGraphSchema.parse(baseGraph);
    expect(parsed.certifications).toEqual([]);
  });

  it("rechaza un nivel de certificación fuera del dominio", () => {
    expect(() =>
      PluginCertificationSchema.parse({ ...certification, level: "C9" }),
    ).toThrow();
  });
});
