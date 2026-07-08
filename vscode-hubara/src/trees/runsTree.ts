import * as vscode from "vscode";
import { BridgeHub } from "../bridge/pythonBridge";

interface RunSummary {
  execution_id: string;
  agent: string;
  status: string;
  startTime?: number;
}

interface LocalRunSummary {
  caseId: string;
  title: string;
  agent: string;
}

export type RunNode =
  | { kind: "run"; run: RunSummary }
  | { kind: "localRun"; run: LocalRunSummary; index: number }
  | { kind: "info"; message: string };

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
  /** ejecuciones durables LANZADAS EN ESTA SESIÓN — la vista default; el
   * histórico completo de AgentSpan se abre con el toggle (⟲). */
  private readonly sessionEids = new Set<string>();
  private localRuns: LocalRunSummary[] = [];
  private showAll = false;

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

  /** Registrar un run durable lanzado desde ESTA sesión (▶ Run Durable). */
  noteDurableLaunched(executionId: string): void {
    this.sessionEids.add(executionId);
    this._onDidChangeTreeData.fire();
  }

  /** Registrar una ejecución local (⚡ — en proceso, sin AgentSpan). */
  noteLocalRun(run: LocalRunSummary): void {
    this.localRuns = [run, ...this.localRuns.filter((r) => r.caseId !== run.caseId)];
    this._onDidChangeTreeData.fire();
  }

  /** Alterna entre "solo esta sesión" (default) y el histórico completo. */
  toggleShowAll(): boolean {
    this.showAll = !this.showAll;
    this._onDidChangeTreeData.fire();
    return this.showAll;
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
    if (element.kind === "localRun") {
      const item = new vscode.TreeItem(`⚡ ${element.run.title}`, vscode.TreeItemCollapsibleState.None);
      item.description = "local · determinista";
      item.iconPath = new vscode.ThemeIcon("zap");
      item.tooltip = "Ejecución en proceso (fixtures del caso, sin AgentSpan) — click re-ejecuta y muestra el detalle";
      item.command = {
        command: "acktos.runLocalCase",
        title: "Ejecutar local",
        arguments: [element.run.caseId, element.run.title],
      };
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
    const locals: RunNode[] = this.localRuns.map((run, index) => ({ kind: "localRun" as const, run, index }));
    // default: SOLO lo lanzado en esta sesión — el histórico completo de
    // AgentSpan no suma para el loop de desarrollo (toggle ⟲ para verlo).
    const durables = this.showAll ? this.runs : this.runs.filter((r) => this.sessionEids.has(r.execution_id));
    const durableNodes: RunNode[] = durables.map((run) => ({ kind: "run" as const, run }));

    // AgentSpan caído solo es noticia si el usuario está usando el durable
    // (lanzó runs en la sesión o pidió el histórico) — el flujo local no lo necesita.
    if (this.lastError && (this.showAll || this.sessionEids.size > 0)) {
      return [...locals, { kind: "info", message: `AgentSpan no disponible: ${this.lastError} — usá "Iniciar AgentSpan" (⚙) o corré los cases en local (⚡)` }];
    }
    if (locals.length === 0 && durableNodes.length === 0) {
      return [
        {
          kind: "info",
          message: this.showAll
            ? "Sin ejecuciones recientes."
            : "Sin ejecuciones en esta sesión — click en un case (⚡ local) o ⟲ para el histórico.",
        },
      ];
    }
    return [...locals, ...durableNodes];
  }

  dispose(): void {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
    }
    this._onDidChangeTreeData.dispose();
  }
}
