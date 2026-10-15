/**
 * Genera el `outputSchema` (JSON Schema draft-07) de cada tool del gateway desde los
 * contratos Zod del dashboard — la spec de los bodies de `/api/ads/*`.
 *
 *   cd frontend_dashboard && npx tsx ../agent_gateway/scripts/gen_output_schemas.mts
 *
 * Reglas (el cliente TS del plugin valida `structuredContent` con Ajv draft-07):
 * - raíz siempre `type: "object"` (spec MCP); un array va como `{items: [...]}`;
 * - `io: "input"`: un campo con `.default()` es opcional, como lo tolera el dashboard;
 * - sin `additionalProperties: false`: un campo NUEVO del backend no rompe al plugin
 *   (Zod tampoco lo rechaza, solo lo descarta);
 * - sin `$schema` (Ajv por defecto no conoce draft 2020-12).
 * El test `tests/test_output_schemas.py` falla si el JSON commiteado no coincide.
 */
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, "../..");
const fe = join(repo, "frontend_dashboard");
const entities = join(fe, "src/plugins/ads/frontend/entities");

// El mismo `zod` que usan los contratos (agent_gateway no tiene node_modules).
const { z } = await import(pathToFileURL(join(fe, "node_modules/zod/index.js")).href);
const campaign = await import(pathToFileURL(join(entities, "ads-campaign/contracts.ts")).href);
const meta = await import(pathToFileURL(join(entities, "meta-connection/contracts.ts")).href);
const run = await import(pathToFileURL(join(entities, "ad-analysis-run/contracts.ts")).href);

const TOOLS: Record<string, unknown> = {
  ads_get_campaigns: campaign.backendAdsCampaignsResponseSchema,
  ads_get_adsets: campaign.backendAdsAdsetsResponseSchema,
  ads_get_ads: campaign.backendAdsAdsResponseSchema,
  ads_get_conversations: campaign.backendAttributedConversationsResponseSchema,
  ads_get_daily: campaign.backendAdsDailyResponseSchema,
  ads_get_creative: campaign.backendAdCreativeSchema,
  ads_get_agent_referrals: campaign.backendAgentReferralsSchema,
  ads_get_meta_status: meta.backendMetaStatusSchema,
  ads_get_analysis_agents: z.object({ items: run.agentsListResponseSchema }),
  ads_get_analysis_runs: run.runsListResponseSchema,
  ads_get_analysis_run: run.runRecordSchema,
};

type Json = null | boolean | number | string | Json[] | { [k: string]: Json };

function relax(node: Json): Json {
  if (Array.isArray(node)) return node.map(relax);
  if (node && typeof node === "object") {
    const out: { [k: string]: Json } = {};
    for (const [k, v] of Object.entries(node)) {
      if (k === "$schema") continue;
      if (k === "additionalProperties" && v === false) continue;
      out[k] = relax(v);
    }
    return out;
  }
  return node;
}

const outDir = process.env.SCHEMAS_OUT_DIR ?? join(repo, "agent_gateway/hubara_gateway/schemas");
mkdirSync(outDir, { recursive: true });
for (const [tool, schema] of Object.entries(TOOLS)) {
  const json = relax(z.toJSONSchema(schema, { target: "draft-7", io: "input" })) as { [k: string]: Json };
  // Una unión (p. ej. meta-status connected true|false) no trae `type` en la raíz.
  const rooted = json.type === "object" ? json : { type: "object", ...json };
  writeFileSync(join(outDir, `${tool}.json`), JSON.stringify(rooted, null, 2) + "\n");
  console.log(`wrote ${tool}.json`);
}
