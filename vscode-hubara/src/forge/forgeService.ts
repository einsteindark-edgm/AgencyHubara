// Forge Console — capa de servicio. REGLA DE ORO (VINCENZO_SPLIT_PLAN.md §11):
// la UI es piel, los CLIs son músculo. Este módulo NO contiene lógica de
// clonación: lista los bundles de forge/clients/ y spawnea
// `python3 forge/forge.py <cmd>` streameando su output. Todo lo que la
// consola muestra sale de archivos declarativos o del stdout del CLI.
//
// Aislamiento deliberado del resto de Studio: no toca BridgeHub ni los paneles
// de desarrollo de agentes/plugins — la migración de clientes es otro concepto.

import { spawn } from "child_process";
import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import * as YAML from "yaml";
import { FleetClient } from "./messages";

export type { FleetClient } from "./messages";

export interface RunEvent {
  kind: "start" | "line" | "end";
  runId: number;
  cmd?: string;
  line?: string;
  code?: number;
}

const REQUIRED_OVERLAY = [
  "IDENTITY.md",
  "SOUL.md",
  "TOOLS.md",
  "memory/MEMORY.md",
  "skills/catalog/SKILL.md",
];
const AGENTS = ["sales", "remarketing"];
const TODO_MARKER = "TODO-BRAND";

export class ForgeService {
  private runSeq = 0;
  private readonly emitter = new vscode.EventEmitter<RunEvent>();
  readonly onRun = this.emitter.event;
  private readonly fleetEmitter = new vscode.EventEmitter<void>();
  readonly onFleetChanged = this.fleetEmitter.event;
  private watcher: vscode.FileSystemWatcher | undefined;

  constructor(
    readonly repoRoot: string,
    private readonly output: vscode.OutputChannel,
  ) {}

  get clientsDir(): string {
    return path.join(this.repoRoot, "forge", "clients");
  }

  get forgePath(): string {
    return path.join(this.repoRoot, "forge", "forge.py");
  }

  /** forge existe en este checkout (la vista se esconde si no). */
  available(): boolean {
    return fs.existsSync(this.forgePath);
  }

  watch(ctx: vscode.ExtensionContext): void {
    const pattern = new vscode.RelativePattern(
      path.join(this.repoRoot, "forge"),
      "clients/**",
    );
    this.watcher = vscode.workspace.createFileSystemWatcher(pattern);
    const fire = () => this.fleetEmitter.fire();
    ctx.subscriptions.push(
      this.watcher,
      this.watcher.onDidChange(fire),
      this.watcher.onDidCreate(fire),
      this.watcher.onDidDelete(fire),
    );
  }

  listClients(): FleetClient[] {
    if (!fs.existsSync(this.clientsDir)) return [];
    const out: FleetClient[] = [];
    for (const entry of fs.readdirSync(this.clientsDir, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      const bundleDir = path.join(this.clientsDir, entry.name);
      const yamlPath = path.join(bundleDir, "client.yaml");
      if (!fs.existsSync(yamlPath)) continue;
      try {
        const doc = YAML.parse(fs.readFileSync(yamlPath, "utf8")) ?? {};
        const aws = doc.aws ?? {};
        const business = doc.business ?? {};
        const { todoFiles, fileCount } = this.scanOverlay(bundleDir);
        out.push({
          slug: doc.slug ?? entry.name,
          company: doc.company ?? entry.name,
          repo: doc.repo ?? "",
          region: aws.region ?? "us-east-1",
          resourcePrefix: aws.resource_prefix ?? `agency${entry.name}`,
          ssmPrefix: aws.ssm_prefix ?? `/${entry.name}`,
          productDescription: business.product_description ?? "",
          domains: business.domains ?? [],
          bundleDir,
          clientYamlPath: yamlPath,
          todoFiles,
          missingRequired: this.missingRequired(bundleDir),
          workspaceFileCount: fileCount,
        });
      } catch (e) {
        this.output.appendLine(`forge: client.yaml inválido en ${entry.name}: ${e}`);
      }
    }
    return out.sort((a, b) => a.slug.localeCompare(b.slug));
  }

  private scanOverlay(bundleDir: string): { todoFiles: string[]; fileCount: number } {
    const todoFiles: string[] = [];
    let fileCount = 0;
    const wsDir = path.join(bundleDir, "workspace");
    const walk = (dir: string) => {
      if (!fs.existsSync(dir)) return;
      for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
        const p = path.join(dir, e.name);
        if (e.isDirectory()) walk(p);
        else {
          fileCount += 1;
          try {
            if (fs.readFileSync(p, "utf8").includes(TODO_MARKER)) {
              todoFiles.push(path.relative(bundleDir, p));
            }
          } catch {
            /* binario: no cuenta para TODOs */
          }
        }
      }
    };
    walk(wsDir);
    return { todoFiles, fileCount };
  }

  private missingRequired(bundleDir: string): string[] {
    const missing: string[] = [];
    for (const agent of AGENTS) {
      for (const req of REQUIRED_OVERLAY) {
        if (!fs.existsSync(path.join(bundleDir, "workspace", agent, req))) {
          missing.push(`${agent}/${req}`);
        }
      }
    }
    return missing;
  }

  /** Spawnea el CLI y streamea stdout/stderr línea a línea. */
  run(cmd: string, args: string[]): Promise<{ code: number; stdout: string }> {
    const runId = ++this.runSeq;
    const argv = [this.forgePath, cmd, ...args];
    this.output.appendLine(`\n$ python3 ${argv.map((a) => a.replace(this.repoRoot + "/", "")).join(" ")}`);
    this.emitter.fire({ kind: "start", runId, cmd: `forge ${cmd} ${args.join(" ")}` });
    return new Promise((resolve) => {
      const child = spawn("python3", argv, { cwd: this.repoRoot });
      let stdout = "";
      const feed = (chunk: Buffer, isErr: boolean) => {
        for (const line of chunk.toString().split("\n")) {
          if (!line.trim()) continue;
          stdout += isErr ? "" : line + "\n";
          this.output.appendLine(line);
          this.emitter.fire({ kind: "line", runId, line });
        }
      };
      child.stdout.on("data", (c: Buffer) => feed(c, false));
      child.stderr.on("data", (c: Buffer) => feed(c, true));
      child.on("close", (code) => {
        this.emitter.fire({ kind: "end", runId, code: code ?? -1 });
        this.fleetEmitter.fire();
        resolve({ code: code ?? -1, stdout });
      });
    });
  }

  dispose(): void {
    this.emitter.dispose();
    this.fleetEmitter.dispose();
  }
}
