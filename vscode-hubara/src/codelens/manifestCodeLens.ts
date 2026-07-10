import * as vscode from "vscode";
import { Provider } from "../bridge/endpoints";

export interface ManifestIdentity {
  system: Provider;
  nodeId: string;
}

const AGENT_FILE_RE = /manifests[\\/]([^\\/]+)\.(agent|taskgraph)\.yaml$/;
const TOOL_FILE_RE = /tools[\\/]([^\\/]+)[\\/]tool\.yaml$/;
const PLUGIN_FILE_RE = /frontend_dashboard[\\/]src[\\/]plugins[\\/]([^\\/]+)[\\/]plugin\.yaml$/;

/** Deriva (system, nodeId del grafo) a partir del path del manifest — los
 * mismos ids que produce `sdk.graph`/`system_map.builder` (`agent:<id>`,
 * `tool:<id>`, `plugin:<id>`), verificados contra el catálogo real. */
export function identifyManifest(fsPath: string): ManifestIdentity | null {
  const agent = AGENT_FILE_RE.exec(fsPath);
  if (agent) {
    return { system: "graphagents", nodeId: `agent:${agent[1]}` };
  }
  const tool = TOOL_FILE_RE.exec(fsPath);
  if (tool) {
    return { system: "graphagents", nodeId: `tool:${tool[1]}` };
  }
  const plugin = PLUGIN_FILE_RE.exec(fsPath);
  if (plugin) {
    return { system: "systemmap", nodeId: `plugin:${plugin[1]}` };
  }
  return null;
}

/** "✓ Check · ⌂ Ver en grafo" arriba de cada manifest — la experiencia
 * compilador (F2) al alcance de un click, sin salir del editor. */
export class ManifestCodeLensProvider implements vscode.CodeLensProvider {
  provideCodeLenses(document: vscode.TextDocument): vscode.CodeLens[] {
    const id = identifyManifest(document.uri.fsPath);
    if (!id) {
      return [];
    }
    const range = new vscode.Range(0, 0, 0, 0);
    return [
      new vscode.CodeLens(range, {
        title: "⌂ Ver en grafo",
        command: "acktos.focusNode",
        arguments: [id.system, id.nodeId],
      }),
      new vscode.CodeLens(range, {
        title: "✓ Check",
        command: "acktos.checkManifest",
        arguments: [id.system],
        tooltip: "Corre el compiler check de este lado — Problems panel se actualiza",
      }),
    ];
  }
}
