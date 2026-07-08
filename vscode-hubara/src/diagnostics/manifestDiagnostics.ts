import { spawn } from "node:child_process";
import * as path from "node:path";
import * as vscode from "vscode";
import { makeSuiteResolver } from "../testing/resolve";
import { SUITES } from "../testing/suites";
import { GA_MANIFEST_RE, GA_TOOL_RE, HUB_PLUGIN_RE } from "./manifestPatterns";
import { CheckDiagnostic, parseGraphAgentsCheckOutput, parseHubaraCheckOutput } from "./parseCheck";

const DEBOUNCE_MS = 250;

function runCommand(cwd: string, argv: string[], env?: Record<string, string>): Promise<{ output: string; exitCode: number | null }> {
  return new Promise((resolve) => {
    const [cmd, ...args] = argv;
    let child: ReturnType<typeof spawn>;
    try {
      child = spawn(cmd, args, { cwd, env: { ...process.env, ...env } });
    } catch (e) {
      resolve({ output: `spawn error: ${e}`, exitCode: null });
      return;
    }
    let out = "";
    child.stdout?.on("data", (d) => (out += d));
    child.stderr?.on("data", (d) => (out += d));
    child.on("close", (code) => resolve({ output: out, exitCode: code }));
    child.on("error", (e) => resolve({ output: `spawn error: ${e.message}`, exitCode: null }));
  });
}

export interface CheckSummary {
  errors: number;
  warnings: number;
  /** exit != 0 pero CERO diagnósticos parseados: o el CLI crasheó o cambió su
   * formato de salida — NUNCA reportarlo como "check OK". */
  parseFailure: boolean;
}

/**
 * "El certificado de compilación" (F2 del plan) — corre `check` al guardar
 * un manifest y traduce el output a `vscode.Diagnostic` (Problems panel +
 * squiggles). Reusa EXACTAMENTE el comando de las suites "compiler"
 * (testing/suites.ts) — ni un flag distinto, cero drift con lo que corre el
 * Test Explorer.
 */
export class ManifestDiagnostics implements vscode.Disposable {
  private readonly collection = vscode.languages.createDiagnosticCollection("hubaraStudio");
  private resolveCommand: ReturnType<typeof makeSuiteResolver>;
  /** Un timer POR LADO: un "Save All" que toca manifests de ambos lados no
   * debe cancelar el check del otro. */
  private readonly saveDebounce: Partial<Record<"graphagents" | "backend", NodeJS.Timeout>> = {};
  private readonly disposables: vscode.Disposable[] = [this.collection];
  private readonly lastUris = { graphagents: new Set<string>(), backend: new Set<string>() };

  constructor(
    private repoRoot: string,
    private readonly resolvePathFn: (value: string, root: string) => string,
    /** notificado tras CADA corrida (activación, save, refresh manual) — lo
     * usa el provider de FileDecorations para no correr su propio listener
     * duplicado sobre los mismos 3 patrones de archivo. */
    private readonly onRan?: (lado: "graphagents" | "backend") => void,
  ) {
    this.resolveCommand = makeSuiteResolver(repoRoot, resolvePathFn);
    this.disposables.push(vscode.workspace.onDidSaveTextDocument((doc) => this.onSave(doc)));
    // corré una vez al activar — el usuario ve el estado actual sin tener
    // que tocar nada primero, como un compilador que muestra errores al abrir.
    void this.runGraphAgents();
    void this.runHubara();
  }

  onConfigChanged(repoRoot: string): void {
    this.repoRoot = repoRoot;
    this.resolveCommand = makeSuiteResolver(repoRoot, this.resolvePathFn);
  }

  private onSave(doc: vscode.TextDocument): void {
    const fsPath = doc.uri.fsPath;
    const isGraphAgents = GA_MANIFEST_RE.test(fsPath) || GA_TOOL_RE.test(fsPath);
    const isHubara = HUB_PLUGIN_RE.test(fsPath);
    if (!isGraphAgents && !isHubara) {
      return;
    }
    const lado = isGraphAgents ? "graphagents" : "backend";
    const prev = this.saveDebounce[lado];
    if (prev) {
      clearTimeout(prev);
    }
    this.saveDebounce[lado] = setTimeout(() => {
      void (isGraphAgents ? this.runGraphAgents() : this.runHubara());
    }, DEBOUNCE_MS);
  }

  async runGraphAgents(): Promise<CheckSummary> {
    const suite = SUITES.find((s) => s.id === "graphagents/compiler")!;
    const resolved = this.resolveCommand(suite);
    const { output, exitCode } = await runCommand(resolved.cwd, resolved.argv, resolved.env);
    const diags = parseGraphAgentsCheckOutput(output);
    // GraphAgents solo emite el BASENAME del manifest — se resuelve contra
    // manifests/ del propio cwd de la suite.
    const summary = this.apply("graphagents", diags, (loc) => path.join(resolved.cwd, "manifests", loc));
    this.onRan?.("graphagents");
    return { ...summary, parseFailure: (exitCode ?? 1) !== 0 && diags.length === 0 };
  }

  async runHubara(): Promise<CheckSummary> {
    const suite = SUITES.find((s) => s.id === "backend/compiler")!;
    const resolved = this.resolveCommand(suite);
    const { output, exitCode } = await runCommand(resolved.cwd, resolved.argv, resolved.env);
    const diags = parseHubaraCheckOutput(output);
    // Hubara mezcla locations absolutas y relativas al MONOREPO root (no a
    // hubara_agency/ — `ctx.repo_root` en checks.py es 4 parents arriba).
    const summary = this.apply("backend", diags, (loc) => (path.isAbsolute(loc) ? loc : path.join(this.repoRoot, loc)));
    this.onRan?.("backend");
    return { ...summary, parseFailure: (exitCode ?? 1) !== 0 && diags.length === 0 };
  }

  private apply(
    lado: "graphagents" | "backend",
    diags: CheckDiagnostic[],
    resolveLocation: (loc: string) => string,
  ): { errors: number; warnings: number } {
    const byFile = new Map<string, vscode.Diagnostic[]>();
    for (const d of diags) {
      if (!d.location) {
        continue; // sin location no hay archivo al que anclar el squiggle (v1 — se pierde el mensaje standalone)
      }
      const fsPath = resolveLocation(d.location);
      const range = new vscode.Range(0, 0, 0, 1);
      const message = d.code ? `[${d.code}] ${d.message}` : d.message;
      const diag = new vscode.Diagnostic(
        range,
        message,
        d.severity === "error" ? vscode.DiagnosticSeverity.Error : vscode.DiagnosticSeverity.Warning,
      );
      diag.source = "Hubara Studio";
      diag.code = d.code;
      const list = byFile.get(fsPath) ?? [];
      list.push(diag);
      byFile.set(fsPath, list);
    }

    const prevUris = this.lastUris[lado];
    for (const stale of prevUris) {
      if (!byFile.has(stale)) {
        this.collection.delete(vscode.Uri.file(stale));
      }
    }
    const nextUris = new Set<string>();
    for (const [fsPath, list] of byFile) {
      this.collection.set(vscode.Uri.file(fsPath), list);
      nextUris.add(fsPath);
    }
    this.lastUris[lado] = nextUris;

    // El conteo va sobre TODO lo parseado, no solo lo anclado a un archivo:
    // los warnings del CLI backend no traen location y el toast diría
    // "0 warnings" con warnings reales.
    let errors = 0;
    let warnings = 0;
    for (const d of diags) {
      if (d.severity === "error") {
        errors++;
      } else {
        warnings++;
      }
    }
    return { errors, warnings };
  }

  dispose(): void {
    for (const t of Object.values(this.saveDebounce)) {
      if (t) {
        clearTimeout(t);
      }
    }
    for (const d of this.disposables) {
      d.dispose();
    }
  }
}
