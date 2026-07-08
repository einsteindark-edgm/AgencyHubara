import * as vscode from "vscode";
import { BridgeHub } from "../bridge/pythonBridge";

interface RunSummary {
  execution_id: string;
  agent: string;
  status: string;
  startTime?: number;
}

export type RunNode = { kind: "run"; run: RunSummary } | { kind: "info"; message: string };

const POLL_MS = 8000;

function statusIcon(status: string): string {
  const s = status.toUpperCase();
  if (s.includes("RUN")) {
    return "sync~spin";
  }
  if (s.includes("COMPLET") || s.includes("DONE")) {
    return "pass";
  }
  if (s.includes("FAIL") || s.includes("TERMINAT")) {
    return "error";
  }
  if (s.includes("PAUS")) {
    return "debug-pause";
  }
  return "circle-outline";
}

/** TreeView "Runs" — las ejecuciones recientes de AgentSpan (poll a
 * `/api/runs`, degradación limpia si `:6767` está caído). Click abre/enfoca
 * el canvas con el trace en vivo (F4). */
export class RunsTreeProvider implements vscode.TreeDataProvider<RunNode>, vscode.Disposable {
  private readonly _onDidChangeTreeData = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;
  private runs: RunSummary[] = [];
  private lastError: string | null = null;
  private pollTimer?: NodeJS.Timeout;

  constructor(private readonly bridges: BridgeHub) {
    // Sin poll eager: arrancarlo acá spawnearía el bridge Python en la
    // activación de CADA ventana aunque la vista nunca se abra — el poll
    // vive atado a la visibilidad de la vista (attach()).
  }

  /** Atar el ciclo del poll a la visibilidad del TreeView: corre solo
   * mientras la vista está visible, para y retoma con ella. */
  attach(view: vscode.TreeView<RunNode>): vscode.Disposable {
    this.setPolling(view.visible);
    return view.onDidChangeVisibility((e) => this.setPolling(e.visible));
  }

  private setPolling(active: boolean): void {
    if (active && !this.pollTimer) {
      void this.poll();
      this.pollTimer = setInterval(() => void this.poll(), POLL_MS);
    } else if (!active && this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = undefined;
    }
  }

  refresh(): void {
    void this.poll();
  }

  private async poll(): Promise<void> {
    try {
      const res = await this.bridges.get("graphagents").request({ method: "GET", path: "/api/runs", params: { limit: "25" } });
      if (res.status === 200) {
        const payload = res.payload as { runs?: RunSummary[] };
        this.runs = payload.runs ?? [];
        this.lastError = null;
      } else {
        const payload = res.payload as { error?: string };
        this.lastError = payload.error ?? `status ${res.status}`;
        this.runs = [];
      }
    } catch (e) {
      this.lastError = e instanceof Error ? e.message : String(e);
      this.runs = [];
    }
    this._onDidChangeTreeData.fire();
  }

  getTreeItem(element: RunNode): vscode.TreeItem {
    if (element.kind === "info") {
      const item = new vscode.TreeItem(element.message, vscode.TreeItemCollapsibleState.None);
      item.iconPath = new vscode.ThemeIcon("info");
      return item;
    }
    const { run } = element;
    const item = new vscode.TreeItem(`${run.agent} — ${run.execution_id.slice(0, 8)}`, vscode.TreeItemCollapsibleState.None);
    item.description = run.status;
    item.iconPath = new vscode.ThemeIcon(statusIcon(run.status));
    item.command = {
      command: "acktos.viewTrace",
      title: "Ver trace",
      arguments: [run.execution_id, run.agent],
    };
    return item;
  }

  getChildren(element?: RunNode): vscode.ProviderResult<RunNode[]> {
    if (element) {
      return [];
    }
    if (this.lastError) {
      return [{ kind: "info", message: `AgentSpan no disponible: ${this.lastError}` }];
    }
    if (this.runs.length === 0) {
      return [{ kind: "info", message: "Sin ejecuciones recientes." }];
    }
    return this.runs.map((run) => ({ kind: "run" as const, run }));
  }

  dispose(): void {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
    }
    this._onDidChangeTreeData.dispose();
  }
}
