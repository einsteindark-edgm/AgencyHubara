import * as fs from "node:fs";
import * as vscode from "vscode";
import { GA_MANIFEST_RE, GA_TOOL_RE, HUB_PLUGIN_RE } from "../diagnostics/manifestPatterns";

interface YamlExtensionApi {
  registerContributor(
    schema: string,
    requestSchema: (resource: string) => string | undefined,
    requestSchemaContent: (uri: string) => string,
  ): void;
}

const SCHEME = "hubara-schema";

interface Association {
  glob: RegExp;
  schemaFile: string;
}

/** Rutas de manifest → schema generado (`scripts/gen-schemas.mjs`). Basado en
 * la convención real del repo (F0/F1): manifests de GraphAgents viven en
 * `manifests/*.agent.yaml`/`*.taskgraph.yaml` y `tools/<id>/tool.yaml`; los
 * plugin.yaml SOLO en `frontend_dashboard/src/plugins/<id>/` (nunca en
 * `hubara_agency/`). */
const ASSOCIATIONS: Association[] = [
  { glob: GA_MANIFEST_RE, schemaFile: "graphagents.agent.schema.json" },
  { glob: GA_TOOL_RE, schemaFile: "graphagents.tool.schema.json" },
  { glob: HUB_PLUGIN_RE, schemaFile: "hubara.plugin.schema.json" },
  { glob: /test-plans[\\/].*\.hubaraplan\.yaml$/, schemaFile: "hubaraplan.schema.json" },
];

/**
 * Registra los schemas generados contra la extensión `redhat.vscode-yaml`
 * (autocomplete + validación inline en los manifests) vía su API de
 * contributor — SIN tocar `yaml.schemas` en settings.json ni los manifests
 * ajenos con comentarios `# yaml-language-server:`. Degrada limpio: si la
 * extensión YAML no está instalada, simplemente no hay validación inline
 * (el resto de Acktos Studio funciona igual).
 */
export async function registerYamlSchemas(ctx: vscode.ExtensionContext, out: vscode.OutputChannel): Promise<void> {
  const yamlExt = vscode.extensions.getExtension<YamlExtensionApi>("redhat.vscode-yaml");
  if (!yamlExt) {
    out.appendLine("[schemas] redhat.vscode-yaml no está instalado — sin validación inline de manifests (opcional).");
    return;
  }
  const api = await yamlExt.activate();
  api.registerContributor(
    SCHEME,
    (resource: string) => {
      let fsPath: string;
      try {
        fsPath = vscode.Uri.parse(resource).fsPath;
      } catch {
        return undefined;
      }
      const match = ASSOCIATIONS.find((a) => a.glob.test(fsPath));
      return match ? `${SCHEME}://schema/${match.schemaFile}` : undefined;
    },
    (uri: string) => {
      const file = uri.replace(`${SCHEME}://schema/`, "");
      const schemaPath = vscode.Uri.joinPath(ctx.extensionUri, "schemas", file).fsPath;
      try {
        return fs.readFileSync(schemaPath, "utf-8");
      } catch (e) {
        out.appendLine(`[schemas] no pude leer ${schemaPath}: ${e}`);
        return "{}";
      }
    },
  );
  out.appendLine(`[schemas] ${ASSOCIATIONS.length} asociaciones registradas contra redhat.vscode-yaml`);
}
