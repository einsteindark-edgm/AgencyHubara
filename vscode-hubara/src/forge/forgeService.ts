// Forge Console — capa de servicio. REGLA DE ORO (VINCENZO_SPLIT_PLAN.md §11):
// la UI es piel, los CLIs son músculo. Este módulo NO contiene lógica de
// clonación ni de migración: lista los bundles de forge/clients/ (con las
// reglas que declara forge/manifest.yaml, igual que forge.py) y spawnea
// `<python> forge/forge.py …` / `<python> forge/migrate.py …` streameando su
// output. Todo lo que la consola muestra sale de archivos declarativos o del
// stdout del CLI.
//
// Aislamiento deliberado del resto de Studio: no toca BridgeHub ni los paneles
// de desarrollo de agentes/plugins — la migración de clientes es otro concepto.

import { ChildProcessWithoutNullStreams, execFile, spawn } from "child_process";
import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import * as YAML from "yaml";
import { readHubaraConfig } from "../config";
import { DestState, FleetClient, MigrationStepView } from "./messages";
import {
  clientYamlWarnings,
  defaultRepoName,
  LineSplitter,
  overlayAgents,
  OverlayAgent,
  parseMigrationStatus,
  pyTitle,
  stepInfo,
  todoMarker,
} from "./pure";

export type { FleetClient } from "./messages";

export type Tone = "ok" | "warn" | "err";

export interface RunEvent {
  kind: "start" | "line" | "end" | "notice";
  runId: number;
  cmd?: string;
  line?: string;
  tone?: Tone;
  code?: number;
}

export interface RunResult {
  code: number;
  stdout: string;
  /** no corrió: había otro proceso en curso o falta Python/PyYAML */
  rejected?: boolean;
}

export interface SourceInfo {
  branch: string;
  sha: string;
  /** líneas de `git status --porcelain` (cambios sin commit) */
  dirtyCount: number;
}

/** El concepto de la tienda para el motor de decisiones (raíz del bundle). */
const DOMAIN_FILE = "domain.yaml";

type Script = "forge" | "migrate";

export class ForgeService {
  private runSeq = 0;
  private active: string | null = null;
  private readonly emitter = new vscode.EventEmitter<RunEvent>();
  readonly onRun = this.emitter.event;
  private readonly fleetEmitter = new vscode.EventEmitter<void>();
  readonly onFleetChanged = this.fleetEmitter.event;
  private readonly busyEmitter = new vscode.EventEmitter<string | null>();
  readonly onBusy = this.busyEmitter.event;
  private watcher: vscode.FileSystemWatcher | undefined;
  /** cache del pre-flight por intérprete: no re-chequea en cada botón */
  private pythonChecks = new Map<string, Promise<{ ok: boolean; message: string }>>();

  constructor(
    readonly repoRoot: string,
    private readonly output: vscode.OutputChannel,
  ) {}

  get forgeDir(): string {
    return path.join(this.repoRoot, "forge");
  }

  get clientsDir(): string {
    return path.join(this.forgeDir, "clients");
  }

  get forgePath(): string {
    return path.join(this.forgeDir, "forge.py");
  }

  get migratePath(): string {
    return path.join(this.forgeDir, "migrate.py");
  }

  get python(): string {
    return readHubaraConfig(this.repoRoot).forgePython;
  }

  /** El comando que hay en curso (null = libre). */
  get busy(): string | null {
    return this.active;
  }

  /**
   * forge existe en este checkout. En un clon forjado NO existe (forge/ no
   * viaja), y ahí Studio esconde toda la UI de Forge.
   */
  available(): boolean {
    return fs.existsSync(this.forgePath);
  }

  watch(ctx: vscode.ExtensionContext): void {
    const pattern = new vscode.RelativePattern(this.forgeDir, "clients/**");
    this.watcher = vscode.workspace.createFileSystemWatcher(pattern);
    const fire = () => this.fleetEmitter.fire();
    ctx.subscriptions.push(
      this.watcher,
      this.watcher.onDidChange(fire),
      this.watcher.onDidCreate(fire),
      this.watcher.onDidDelete(fire),
      vscode.workspace.onDidChangeConfiguration((e) => {
        if (e.affectsConfiguration("acktos.forge.python")) this.pythonChecks.clear();
      }),
    );
  }

  // ── Flota ──────────────────────────────────────────────────────────────────

  /** forge/manifest.yaml parseado (null si no se puede leer). */
  private readManifest(): unknown {
    try {
      return YAML.parse(fs.readFileSync(path.join(this.forgeDir, "manifest.yaml"), "utf8"));
    } catch (e) {
      this.output.appendLine(`forge: no pude leer forge/manifest.yaml: ${e}`);
      return null;
    }
  }

  listClients(): FleetClient[] {
    if (!fs.existsSync(this.clientsDir)) return [];
    const manifest = this.readManifest();
    const agents = manifest ? overlayAgents(manifest) : {};
    const marker = todoMarker(manifest);
    const out: FleetClient[] = [];
    for (const entry of fs.readdirSync(this.clientsDir, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      const bundleDir = path.join(this.clientsDir, entry.name);
      const yamlPath = path.join(bundleDir, "client.yaml");
      if (!fs.existsSync(yamlPath)) continue;
      try {
        const doc = YAML.parse(fs.readFileSync(yamlPath, "utf8")) ?? {};
        const slug: string = typeof doc.slug === "string" && doc.slug ? doc.slug : entry.name;
        const aws = doc.aws ?? {};
        const business = doc.business ?? {};
        const repo: string = typeof doc.repo === "string" ? doc.repo : "";
        const domainPath = path.join(bundleDir, DOMAIN_FILE);
        const hasDomain = fs.existsSync(domainPath);
        const warnings = clientYamlWarnings(doc, slug);
        if (!manifest) warnings.unshift("no pude leer forge/manifest.yaml — los requeridos no se revisaron");
        out.push({
          slug,
          company: doc.company || pyTitle(slug),
          repo,
          repoName: defaultRepoName(repo, slug),
          androidAppId: doc.android_app_id || `com.acktos.${slug}`,
          region: aws.region ?? "us-east-1",
          resourcePrefix: aws.resource_prefix ?? `agency${slug}`,
          ssmPrefix: aws.ssm_prefix ?? `/${slug}`,
          productDescription: business.product_description ?? "",
          domains: business.domains ?? [],
          bundleDir,
          clientYamlPath: yamlPath,
          domainYamlPath: hasDomain ? domainPath : null,
          todoFiles: this.scanTodos(bundleDir, agents, marker, hasDomain ? domainPath : null),
          missingRequired: this.missingRequired(bundleDir, agents, hasDomain),
          clientWarnings: warnings,
          workspaceFileCount: countFiles(path.join(bundleDir, "workspace")),
        });
      } catch (e) {
        this.output.appendLine(`forge: client.yaml inválido en ${entry.name}: ${e}`);
      }
    }
    return out.sort((a, b) => a.slug.localeCompare(b.slug));
  }

  /** Igual que forge.py stage_overlay: los agentes del manifest + domain.yaml. */
  private scanTodos(
    bundleDir: string,
    agents: Record<string, OverlayAgent>,
    marker: string,
    domainPath: string | null,
  ): string[] {
    const todoFiles: string[] = [];
    const walk = (dir: string) => {
      if (!fs.existsSync(dir)) return;
      for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
        const p = path.join(dir, e.name);
        if (e.isDirectory()) walk(p);
        else if (fileHas(p, marker)) todoFiles.push(path.relative(bundleDir, p));
      }
    };
    for (const agent of Object.keys(agents)) walk(path.join(bundleDir, "workspace", agent));
    if (domainPath && fileHas(domainPath, marker)) todoFiles.push(DOMAIN_FILE);
    return todoFiles.sort();
  }

  private missingRequired(
    bundleDir: string,
    agents: Record<string, OverlayAgent>,
    hasDomain: boolean,
  ): string[] {
    const missing: string[] = [];
    for (const [agent, spec] of Object.entries(agents)) {
      const dir = path.join(bundleDir, "workspace", agent);
      if (!fs.existsSync(dir)) {
        missing.push(`${agent}/ (carpeta completa)`);
        continue;
      }
      for (const req of spec.required) {
        if (!fs.existsSync(path.join(dir, req))) missing.push(`${agent}/${req}`);
      }
    }
    if (!hasDomain) missing.push(`${DOMAIN_FILE} (el concepto de la tienda)`);
    return missing.sort();
  }

  // ── Estado de un destino + migración ─────────────────────────────────────────

  destState(dest: string): DestState {
    const d = dest.trim();
    if (!d || !path.isAbsolute(d)) return { exists: false, isClone: false, nextStepsPath: null };
    const nextSteps = path.join(d, "NEXT_STEPS.md");
    return {
      exists: fs.existsSync(d),
      isClone: fs.existsSync(path.join(d, ".git")),
      nextStepsPath: fs.existsSync(nextSteps) ? nextSteps : null,
    };
  }

  /** `migrate.py status <slug> [--dest D] --json`, enriquecido con la ficha de cada paso. */
  async migrationSteps(slug: string, dest: string): Promise<MigrationStepView[]> {
    const args = ["status", slug, "--json"];
    if (dest.trim()) args.push("--dest", dest.trim());
    const r = await this.capture("migrate", args);
    if (r.code !== 0) {
      throw new Error(lastLines(r.stderr || r.stdout) || `migrate.py salió con código ${r.code}`);
    }
    const status = parseMigrationStatus(r.stdout);
    return status.steps.map((s) => {
      const info = stepInfo(s.id, s.kind);
      return {
        ...s,
        summary: info.summary,
        action: info.action,
        needs: info.needs.map((n) => ({ ...n, present: !!process.env[n.name]?.trim() })),
        tools: (info.tools ?? []).map((t) => ({ name: t, present: onPath(t) })),
        needsClone: info.needsClone,
        printsOnly: !!info.printsOnly,
        note: info.note,
      };
    });
  }

  /** De dónde sale el clon: rama, commit y si hay cambios sin commit. */
  async sourceInfo(): Promise<SourceInfo> {
    const git = (args: string[]) =>
      new Promise<string>((resolve) => {
        execFile("git", ["-C", this.repoRoot, ...args], { encoding: "utf8" }, (err, stdout) =>
          resolve(err ? "" : stdout),
        );
      });
    const [branch, sha, porcelain] = await Promise.all([
      git(["rev-parse", "--abbrev-ref", "HEAD"]),
      git(["rev-parse", "--short", "HEAD"]),
      git(["status", "--porcelain"]),
    ]);
    return {
      branch: branch.trim() || "?",
      sha: sha.trim() || "?",
      dirtyCount: porcelain.split("\n").filter((l) => l.trim() !== "").length,
    };
  }

  // ── Python ──────────────────────────────────────────────────────────────────

  /** Pre-flight: el intérprete existe y tiene PyYAML (forge lo necesita). */
  checkPython(): Promise<{ ok: boolean; message: string }> {
    const py = this.python;
    let p = this.pythonChecks.get(py);
    if (!p) {
      p = new Promise((resolve) => {
        execFile(py, ["-c", "import yaml"], { cwd: this.repoRoot }, (err) => {
          if (!err) return resolve({ ok: true, message: `Python listo (${py})` });
          const code = (err as NodeJS.ErrnoException).code;
          if (code === "ENOENT") {
            resolve({
              ok: false,
              message:
                `No encontré Python («${py}»). Instálalo o indica la ruta correcta en el ` +
                "ajuste acktos.forge.python.",
            });
          } else {
            resolve({
              ok: false,
              message:
                `A Python («${py}») le falta PyYAML. En una terminal ejecuta: ` +
                `${py} -m pip install pyyaml — y vuelve a intentar.`,
            });
          }
        });
      });
      this.pythonChecks.set(py, p);
      // un fallo no se cachea: el operador lo arregla y reintenta
      void p.then((r) => {
        if (!r.ok) this.pythonChecks.delete(py);
      });
    }
    return p;
  }

  // ── Ejecución ─────────────────────────────────────────────────────────────────

  runForge(cmd: string, args: string[]): Promise<RunResult> {
    return this.run("forge", [cmd, ...args]);
  }

  runMigrate(args: string[]): Promise<RunResult> {
    return this.run("migrate", args);
  }

  /**
   * Spawnea el CLI y streamea stdout/stderr línea a línea. UN proceso a la
   * vez: si hay otro en curso, no corre y lo avisa (dos forjas sobre el mismo
   * destino, o un migrate en medio de un apply, se pisarían).
   */
  private async run(script: Script, args: string[]): Promise<RunResult> {
    const label = `${script} ${args.join(" ")}`;
    if (this.active) {
      const msg = `Forge: ya hay un proceso en curso (${this.active}). Espera a que termine.`;
      void vscode.window.showWarningMessage(msg);
      this.emitter.fire({ kind: "notice", runId: 0, line: msg, tone: "warn" });
      return { code: -1, stdout: "", rejected: true };
    }
    this.setBusy(label);
    try {
      const runId = ++this.runSeq;
      this.emitter.fire({ kind: "start", runId, cmd: label });
      const py = await this.checkPython();
      if (!py.ok) {
        this.output.appendLine(py.message);
        this.emitter.fire({ kind: "line", runId, line: py.message, tone: "err" });
        this.emitter.fire({ kind: "end", runId, code: -1 });
        void vscode.window
          .showErrorMessage(py.message, "Abrir ajuste")
          .then((b) => {
            if (b) void vscode.commands.executeCommand("workbench.action.openSettings", "acktos.forge.python");
          });
        return { code: -1, stdout: "", rejected: true };
      }
      return await this.spawnStreaming(runId, script, args);
    } finally {
      this.setBusy(null);
      this.fleetEmitter.fire();
    }
  }

  private setBusy(label: string | null): void {
    this.active = label;
    this.busyEmitter.fire(label);
  }

  private scriptPath(script: Script): string {
    return script === "forge" ? this.forgePath : this.migratePath;
  }

  private childEnv(): NodeJS.ProcessEnv {
    // sin buffer (el stream llega en vivo) y en UTF-8 (tildes, ✓, ⚠)
    return { ...process.env, PYTHONUNBUFFERED: "1", PYTHONIOENCODING: "utf-8" };
  }

  private spawnStreaming(runId: number, script: Script, args: string[]): Promise<RunResult> {
    const py = this.python;
    const argv = [this.scriptPath(script), ...args];
    this.output.appendLine(
      `\n$ ${py} ${argv.map((a) => a.replace(this.repoRoot + path.sep, "")).join(" ")}`,
    );
    return new Promise((resolve) => {
      let settled = false;
      let stdout = "";
      const outSplit = new LineSplitter();
      const errSplit = new LineSplitter();
      const emit = (line: string, isErr: boolean) => {
        if (!isErr) stdout += line + "\n";
        this.output.appendLine(line);
        this.emitter.fire({ kind: "line", runId, line });
      };
      const finish = (code: number) => {
        if (settled) return;
        settled = true;
        outSplit.flush().forEach((l) => emit(l, false));
        errSplit.flush().forEach((l) => emit(l, true));
        this.emitter.fire({ kind: "end", runId, code });
        resolve({ code, stdout });
      };
      let child: ChildProcessWithoutNullStreams;
      try {
        child = spawn(py, argv, { cwd: this.repoRoot, env: this.childEnv() });
      } catch (e) {
        const msg = `No pude ejecutar «${py}»: ${e instanceof Error ? e.message : e}`;
        this.output.appendLine(msg);
        this.emitter.fire({ kind: "line", runId, line: msg, tone: "err" });
        finish(-1);
        return;
      }
      child.stdout.setEncoding("utf8");
      child.stderr.setEncoding("utf8");
      child.stdout.on("data", (c: string) => outSplit.push(c).forEach((l) => emit(l, false)));
      child.stderr.on("data", (c: string) => errSplit.push(c).forEach((l) => emit(l, true)));
      child.on("error", (err) => {
        const msg = `No pude ejecutar «${py}»: ${err.message} — revisa el ajuste acktos.forge.python.`;
        this.output.appendLine(msg);
        this.emitter.fire({ kind: "line", runId, line: msg, tone: "err" });
        this.pythonChecks.delete(py);
        finish(-1);
      });
      child.on("close", (code) => finish(code ?? -1));
    });
  }

  /** Corre el CLI SIN streamear (lecturas: `migrate.py status --json`). */
  private capture(
    script: Script,
    args: string[],
  ): Promise<{ code: number; stdout: string; stderr: string }> {
    const py = this.python;
    return new Promise((resolve) => {
      execFile(
        py,
        [this.scriptPath(script), ...args],
        { cwd: this.repoRoot, env: this.childEnv(), encoding: "utf8", timeout: 60_000 },
        (err, stdout, stderr) => {
          if (err && (err as NodeJS.ErrnoException).code === "ENOENT") {
            resolve({ code: -1, stdout: "", stderr: `No encontré Python («${py}») — ajuste acktos.forge.python.` });
            return;
          }
          const code = err ? (typeof err.code === "number" ? err.code : -1) : 0;
          resolve({ code, stdout, stderr });
        },
      );
    });
  }

  dispose(): void {
    this.emitter.dispose();
    this.fleetEmitter.dispose();
    this.busyEmitter.dispose();
  }
}

// ── helpers de disco ─────────────────────────────────────────────────────────

function fileHas(p: string, marker: string): boolean {
  try {
    return fs.readFileSync(p, "utf8").includes(marker);
  } catch {
    return false; // ilegible: no cuenta para TODOs
  }
}

function countFiles(dir: string): number {
  if (!fs.existsSync(dir)) return 0;
  let n = 0;
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    n += e.isDirectory() ? countFiles(path.join(dir, e.name)) : 1;
  }
  return n;
}

/** ¿El programa está en el PATH del entorno de VS Code? */
function onPath(bin: string): boolean {
  const exts = process.platform === "win32" ? [".exe", ".cmd", ""] : [""];
  for (const dir of (process.env.PATH ?? "").split(path.delimiter)) {
    if (!dir) continue;
    for (const ext of exts) {
      if (fs.existsSync(path.join(dir, bin + ext))) return true;
    }
  }
  return false;
}

function lastLines(text: string, n = 4): string {
  return text
    .split(/\r?\n/)
    .filter((l) => l.trim() !== "")
    .slice(-n)
    .join("\n");
}
