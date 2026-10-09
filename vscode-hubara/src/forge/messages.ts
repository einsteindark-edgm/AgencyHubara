// Contrato extensión ↔ webview de la Forge Console. SIN imports de `vscode`:
// este módulo lo comparte el bundle del webview (solo tipos).

export interface FleetClient {
  slug: string;
  company: string;
  repo: string;
  /** nombre de carpeta/repo del clon (sin espacios) — base del destino sugerido */
  repoName: string;
  androidAppId: string;
  region: string;
  resourcePrefix: string;
  ssmPrefix: string;
  productDescription: string;
  domains: string[];
  bundleDir: string;
  clientYamlPath: string;
  /** domain.yaml del bundle (el concepto de la tienda) — null si falta */
  domainYamlPath: string | null;
  /** Archivos del bundle (workspace + domain.yaml) que aún tienen TODO-BRAND. */
  todoFiles: string[];
  /** Archivos requeridos por el manifest que faltan en el bundle. */
  missingRequired: string[];
  /** Valores de client.yaml todavía en plantilla — avisos, NO bloquean. */
  clientWarnings: string[];
  /** Campos de `commerce` en plantilla: bloquean el forjado (salvo clon de prueba). */
  commercePending: string[];
  workspaceFileCount: number;
}

export interface StepNeedView {
  name: string;
  optional?: boolean;
  /** está en el entorno con que se abrió VS Code (el valor jamás viaja) */
  present: boolean;
}

export interface MigrationStepView {
  id: string;
  title: string;
  kind: "auto" | "guided";
  done: boolean;
  summary: string;
  action: string;
  needs: StepNeedView[];
  tools: { name: string; present: boolean }[];
  needsClone: boolean;
  printsOnly: boolean;
  note?: string;
}

export interface DestState {
  /** la carpeta existe */
  exists: boolean;
  /** hay un clon forjado (<dest>/.git con su primer commit) */
  isClone: boolean;
  /** <dest>/NEXT_STEPS.md, si existe */
  nextStepsPath: string | null;
}

/** webview → extensión */
export type ForgeInbound =
  | { type: "ready" }
  | { type: "refresh" }
  | { type: "initClient"; slug: string; company: string }
  | { type: "plan"; slug: string }
  | { type: "apply"; slug: string; dest: string; allowTodos: boolean }
  | { type: "verify"; slug: string; dest: string }
  | { type: "publish"; slug: string; dest: string }
  | { type: "inspect"; slug: string; dest: string }
  | { type: "migrateRun"; slug: string; step: string; dest: string; allowTodos: boolean }
  | { type: "migrateDone"; slug: string; step: string; dest: string }
  | { type: "openPath"; fsPath: string }
  | { type: "openSettings" }
  | { type: "pickDest"; slug: string }
  | { type: "cancel" };

/** extensión → webview */
export type ForgeOutbound =
  | { type: "fleet"; clients: FleetClient[]; repoRoot: string }
  | { type: "busy"; busy: boolean; cmd: string }
  | { type: "python"; ok: boolean; message: string }
  | { type: "runStart"; runId: number; cmd: string }
  | { type: "runLine"; runId: number; line: string; tone?: "ok" | "warn" | "err" }
  | { type: "runEnd"; runId: number; code: number }
  | { type: "notice"; text: string; tone: "ok" | "warn" | "err" }
  | {
      type: "inspected";
      slug: string;
      dest: string;
      destState: DestState;
      steps: MigrationStepView[] | null;
      /** la carpeta del clon que registró el paso S1 (null si aún no hay) */
      recordedDest: string | null;
      error: string | null;
    }
  | { type: "destPicked"; slug: string; dest: string };
