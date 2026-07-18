// Contrato extensión ↔ webview de la Forge Console. SIN imports de `vscode`:
// este módulo lo comparte el bundle del webview (solo tipos).

export interface FleetClient {
  slug: string;
  company: string;
  repo: string;
  region: string;
  resourcePrefix: string;
  ssmPrefix: string;
  productDescription: string;
  domains: string[];
  bundleDir: string;
  clientYamlPath: string;
  /** Archivos del overlay que aún tienen TODO-BRAND (redacción pendiente). */
  todoFiles: string[];
  /** Archivos requeridos por el manifest que faltan en el bundle. */
  missingRequired: string[];
  workspaceFileCount: number;
}

/** webview → extensión */
export type ForgeInbound =
  | { type: "ready" }
  | { type: "refresh" }
  | { type: "initClient"; slug: string }
  | { type: "plan"; slug: string }
  | { type: "apply"; slug: string; dest: string; allowTodos: boolean }
  | { type: "verify"; slug: string; dest: string }
  | { type: "publish"; slug: string; dest: string }
  | { type: "openPath"; fsPath: string }
  | { type: "pickDest"; slug: string };

/** extensión → webview */
export type ForgeOutbound =
  | { type: "fleet"; clients: FleetClient[]; repoRoot: string }
  | { type: "runStart"; runId: number; cmd: string }
  | { type: "runLine"; runId: number; line: string }
  | { type: "runEnd"; runId: number; code: number }
  | { type: "destPicked"; slug: string; dest: string };
