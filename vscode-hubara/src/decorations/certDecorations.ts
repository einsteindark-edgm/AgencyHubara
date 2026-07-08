import * as fs from "node:fs/promises";
import * as path from "node:path";
import * as vscode from "vscode";
import { GRAPH_PATH, isGraphPayload } from "../bridge/endpoints";
import { BridgeHub } from "../bridge/pythonBridge";

const LEVEL_COLOR: Record<string, vscode.ThemeColor> = {
  none: new vscode.ThemeColor("problemsErrorIcon.foreground"),
  C0: new vscode.ThemeColor("problemsErrorIcon.foreground"),
  C1: new vscode.ThemeColor("problemsWarningIcon.foreground"),
  C2: new vscode.ThemeColor("testing.iconPassed"),
  C3: new vscode.ThemeColor("testing.iconPassed"),
};

/** Espejo de PluginCertification (system_map/domain/contracts.py): el campo
 * es `plugin_id` (id pelado, SIN prefijo "plugin:") — no `id`. */
interface SystemMapCertification {
  plugin_id: string;
  level: string;
}

/**
 * Badge de nivel de certificación (C0–C3) sobre plugins de Hubara y
 * agentes/tools de GraphAgents en el Explorer — "¿está C2 sin abrir nada?".
 * Se refresca tras cada corrida de `ManifestDiagnostics` (mismo trigger,
 * cero listeners duplicados) + al activar.
 */
export class CertDecorationProvider implements vscode.FileDecorationProvider, vscode.Disposable {
  private readonly _onDidChangeFileDecorations = new vscode.EventEmitter<vscode.Uri[] | undefined>();
  readonly onDidChangeFileDecorations = this._onDidChangeFileDecorations.event;
  private levelByPath = new Map<string, string>();
  private bySide: Record<"backend" | "graphagents", Map<string, string>> = {
    backend: new Map(),
    graphagents: new Map(),
  };
  private warnedCertUnavailable = false;

  provideFileDecoration(uri: vscode.Uri): vscode.FileDecoration | undefined {
    const level = this.levelByPath.get(uri.fsPath);
    if (!level) {
      return undefined;
    }
    return {
      badge: level === "none" ? "✗" : level.slice(0, 2),
      color: LEVEL_COLOR[level] ?? LEVEL_COLOR.none,
      tooltip: `Hubara Studio: certificación ${level}`,
    };
  }

  /** Re-colecta las decoraciones. Con `lado` solo re-consulta ese bridge (el
   * otro conserva su último resultado) — guardar un manifest de UN lado no
   * debe golpear el bridge/TCK del otro. */
  async refresh(bridges: BridgeHub, repoRoot: string, graphagentsCwd: string, lado?: "graphagents" | "backend"): Promise<void> {
    const jobs: Promise<void>[] = [];
    if (lado !== "graphagents") {
      const m = new Map<string, string>();
      jobs.push(this.collectHubara(bridges, repoRoot, m).then(() => void (this.bySide.backend = m)));
    }
    if (lado !== "backend") {
      const m = new Map<string, string>();
      jobs.push(this.collectGraphAgents(bridges, graphagentsCwd, m).then(() => void (this.bySide.graphagents = m)));
    }
    await Promise.all(jobs);

    const next = new Map([...this.bySide.backend, ...this.bySide.graphagents]);
    const touched = new Set([...next.keys(), ...this.levelByPath.keys()]);
    this.levelByPath = next;
    this._onDidChangeFileDecorations.fire([...touched].map((p) => vscode.Uri.file(p)));
  }

  private async collectHubara(bridges: BridgeHub, repoRoot: string, out: Map<string, string>): Promise<void> {
    try {
      const res = await bridges.get("systemmap").request({ method: "GET", path: GRAPH_PATH });
      if (res.status !== 200 || !isGraphPayload(res.payload)) {
        return;
      }
      const payload = res.payload as { certifications?: SystemMapCertification[]; warnings?: string[] };
      for (const c of payload.certifications ?? []) {
        // El TCK certifica el plugin backend; el dir frontend homónimo se
        // decora también para que el badge se vea desde ambos lados.
        out.set(path.join(repoRoot, "hubara_agency", "src", "plugins", c.plugin_id), c.level);
        out.set(path.join(repoRoot, "frontend_dashboard", "src", "plugins", c.plugin_id), c.level);
      }
      const certWarning = (payload.warnings ?? []).find((w) => w.startsWith("certification_unavailable"));
      if (certWarning && !this.warnedCertUnavailable) {
        // Un TCK roto NO es "plugins sin certificar" — avisarlo una vez.
        this.warnedCertUnavailable = true;
        void vscode.window.showWarningMessage(`Hubara Studio: certificaciones no disponibles — ${certWarning}`);
      }
    } catch {
      // sin bridge/graph disponible: sin decoraciones de este lado, no crash
    }
  }

  private async collectGraphAgents(bridges: BridgeHub, cwd: string, out: Map<string, string>): Promise<void> {
    try {
      const res = await bridges.get("graphagents").request({ method: "GET", path: GRAPH_PATH });
      if (res.status !== 200 || !isGraphPayload(res.payload)) {
        return;
      }
      let manifestFiles: string[] | null = null;
      for (const n of res.payload.nodes) {
        const level = typeof n.certification === "string" ? n.certification : undefined;
        if (!level) {
          continue;
        }
        if (n.kind === "tool") {
          const id = n.id.startsWith("tool:") ? n.id.slice("tool:".length) : n.id;
          out.set(path.join(cwd, "tools", id, "tool.yaml"), level);
        } else if (n.kind === "agent") {
          const id = n.id.startsWith("agent:") ? n.id.slice("agent:".length) : n.id;
          manifestFiles ??= await this.listManifestFiles(cwd);
          const match = manifestFiles.find((f) => f === `${id}.agent.yaml` || f === `${id}.taskgraph.yaml`);
          if (match) {
            out.set(path.join(cwd, "manifests", match), level);
          }
        }
      }
    } catch {
      // ídem — degradación limpia
    }
  }

  private async listManifestFiles(cwd: string): Promise<string[]> {
    try {
      return await fs.readdir(path.join(cwd, "manifests"));
    } catch {
      return [];
    }
  }

  dispose(): void {
    this._onDidChangeFileDecorations.dispose();
  }
}
