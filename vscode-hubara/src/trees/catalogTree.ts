import * as vscode from "vscode";
import { GRAPH_PATH, GraphNode, GraphPayload, isGraphPayload, Provider } from "../bridge/endpoints";
import { BridgeHub } from "../bridge/pythonBridge";

interface GroupNode {
  kind: "group";
  id: string;
  label: string;
  system: Provider;
  nodeKind: string;
  icon: string;
}
interface CasesGroupNode {
  kind: "casesGroup";
  id: "cases";
}
interface WorkflowsGroupNode {
  kind: "workflowsGroup";
  id: "workflows";
}
interface WorkflowLeafNode {
  kind: "workflowLeaf";
  id: string;
  rootId: string;
  label: string;
  nodeCount: number;
  system: Provider;
}
interface LeafNode {
  kind: "leaf";
  id: string;
  label: string;
  system: Provider;
  nodeId: string;
  description?: string;
}
interface CaseLeafNode {
  kind: "caseLeaf";
  id: string;
  caseId: string;
  title: string;
  target: string;
}
type TreeNode = GroupNode | CasesGroupNode | WorkflowsGroupNode | LeafNode | CaseLeafNode | WorkflowLeafNode;

interface CaseSummary {
  id: string;
  target: string;
  title: string;
}

const GROUPS: Array<Omit<GroupNode, "kind">> = [
  { id: "agents", label: "Agentes", system: "graphagents", nodeKind: "agent", icon: "hubot" },
  { id: "tools", label: "Tools", system: "graphagents", nodeKind: "tool", icon: "tools" },
  { id: "ports", label: "Ports", system: "graphagents", nodeKind: "port", icon: "plug" },
  { id: "plugins", label: "Plugins", system: "systemmap", nodeKind: "plugin", icon: "package" },
];

/** TreeView "Acktos Studio" — Plugins / Agentes / Tools / Ports / Cases del
 * catálogo. Click en una hoja enfoca el canvas en ese nodo (comando
 * `acktos.focusNode`, registrado en extension.ts). */
export class CatalogTreeProvider implements vscode.TreeDataProvider<TreeNode> {
  private readonly _onDidChangeTreeData = new vscode.EventEmitter<TreeNode | undefined>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;
  private payloadCache = new Map<Provider, GraphPayload>();
  private caseCache: CaseSummary[] | null = null;

  constructor(private readonly bridges: BridgeHub) {}

  refresh(): void {
    this.payloadCache.clear();
    this.caseCache = null;
    this._onDidChangeTreeData.fire(undefined);
  }

  getTreeItem(element: TreeNode): vscode.TreeItem {
    if (element.kind === "group") {
      const item = new vscode.TreeItem(element.label, vscode.TreeItemCollapsibleState.Collapsed);
      item.iconPath = new vscode.ThemeIcon(element.icon);
      return item;
    }
    if (element.kind === "casesGroup") {
      const item = new vscode.TreeItem("Cases", vscode.TreeItemCollapsibleState.Collapsed);
      item.iconPath = new vscode.ThemeIcon("beaker");
      return item;
    }
    if (element.kind === "workflowsGroup") {
      const item = new vscode.TreeItem("Workflows", vscode.TreeItemCollapsibleState.Expanded);
      item.iconPath = new vscode.ThemeIcon("type-hierarchy");
      return item;
    }
    if (element.kind === "workflowLeaf") {
      const item = new vscode.TreeItem(element.label, vscode.TreeItemCollapsibleState.None);
      item.description = `${element.nodeCount} nodos`;
      item.iconPath = new vscode.ThemeIcon(element.system === "graphagents" ? "rocket" : "package");
      item.tooltip = `Dibujar SOLO este flujo (todo lo conectado a ${element.rootId})`;
      item.command = {
        command: "acktos.openWorkflow",
        title: "Ver workflow",
        arguments: [element.rootId, element.label, element.system],
      };
      return item;
    }
    if (element.kind === "caseLeaf") {
      const item = new vscode.TreeItem(element.title, vscode.TreeItemCollapsibleState.None);
      item.description = element.target;
      item.iconPath = new vscode.ThemeIcon("play-circle");
      item.contextValue = "acktosCase"; // habilita ⚡/▶ Replay/▶ Run durable (package.json view/item/context)
      // default = ejecución LOCAL (en proceso, sin infraestructura) con el
      // detalle completo en el canvas; replay (golden) y durable van por menú.
      item.command = { command: "acktos.runLocalCase", title: "Ejecutar (local)", arguments: [element.caseId, element.title] };
      return item;
    }
    const item = new vscode.TreeItem(element.label, vscode.TreeItemCollapsibleState.None);
    item.description = element.description;
    item.command = {
      command: "acktos.focusNode",
      title: "Ver en grafo",
      arguments: [element.system, element.nodeId, element.label],
    };
    // "+ Conectar desde…" (§F5, marketplace-por-picker) solo tiene sentido
    // para agent:/tool: de GraphAgents — plugins/ports no son destino de `uses`/`agent`.
    if (element.system === "graphagents" && (element.nodeId.startsWith("agent:") || element.nodeId.startsWith("tool:"))) {
      item.contextValue = "acktosConnectable";
    }
    return item;
  }

  async getChildren(element?: TreeNode): Promise<TreeNode[]> {
    if (!element) {
      return [
        { kind: "workflowsGroup", id: "workflows" },
        ...GROUPS.map((g): GroupNode => ({ kind: "group", ...g })),
        { kind: "casesGroup", id: "cases" },
      ];
    }
    if (element.kind === "workflowsGroup") {
      return this.workflows();
    }
    if (element.kind === "group") {
      // Los PLUGINS se listan como workflows (§F11): click dibuja la clausura
      // dirigida completa del plugin (frontend → routers → workers → queues +
      // depends_on), no un ego-graph de 1 salto — "la relación completa".
      if (element.nodeKind === "plugin") {
        return this.pluginWorkflows();
      }
      const nodes = await this.nodesFor(element.system);
      return nodes
        .filter((n) => n.kind === element.nodeKind)
        .sort((a, b) => a.id.localeCompare(b.id))
        .map(
          (n): LeafNode => ({
            kind: "leaf",
            id: `${element.system}:${n.id}`,
            label: (n.label as string | undefined) ?? n.id,
            system: element.system,
            nodeId: n.id,
            description: describeExtra(n),
          }),
        );
    }
    if (element.kind === "casesGroup") {
      const cases = await this.cases();
      return cases.map(
        (c): CaseLeafNode => ({ kind: "caseLeaf", id: `case:${c.id}`, caseId: c.id, title: c.title, target: c.target }),
      );
    }
    return [];
  }

  private async nodesFor(system: Provider): Promise<GraphNode[]> {
    return (await this.payloadFor(system))?.nodes ?? [];
  }

  private async payloadFor(system: Provider): Promise<GraphPayload | null> {
    const cached = this.payloadCache.get(system);
    if (cached) {
      return cached;
    }
    try {
      const res = await this.bridges.get(system).request({ method: "GET", path: GRAPH_PATH });
      const payload = res.status === 200 && isGraphPayload(res.payload) ? res.payload : null;
      if (payload) {
        this.payloadCache.set(system, payload);
      }
      return payload;
    } catch {
      return null;
    }
  }

  /** Las RAÍCES de los flujos operables: (a) supervisores con sub-agentes
   * (aristas `agent` salientes) que ningún otro supervisor referencia, y
   * (b) agentes standalone ENTRY-POINT — `exposes_as_tool: false` y sin
   * supervisor (los dispatcha un sistema externo, p.ej. hubara vía SSM).
   * Un agente con `exposes_as_tool: true` es un COMPONENTE: vive bajo el
   * workflow del supervisor que lo compone, no como flujo propio. */
  private async workflows(): Promise<WorkflowLeafNode[]> {
    const payload = await this.payloadFor("graphagents");
    if (!payload) {
      return [];
    }
    const agentEdges = payload.edges.filter((e) => e.kind === "agent");
    const supervised = new Set(agentEdges.map((e) => e.target));
    const roots = payload.nodes.filter(
      (n) =>
        n.kind === "agent" &&
        !supervised.has(n.id) &&
        (agentEdges.some((e) => e.source === n.id) || n["exposes_as_tool"] === false),
    );
    return toWorkflowLeaves(roots, payload, "graphagents");
  }

  /** Cada plugin como un workflow: la raíz es `plugin:<id>` y el flujo es su
   * clausura dirigida (belongs_to → uses_api/invokes_worker → consumes_queue,
   * más los plugins de los que depende). Misma semántica que Workflows. */
  private async pluginWorkflows(): Promise<WorkflowLeafNode[]> {
    const payload = await this.payloadFor("systemmap");
    if (!payload) {
      return [];
    }
    const roots = payload.nodes.filter((n) => n.kind === "plugin");
    return toWorkflowLeaves(roots, payload, "systemmap");
  }

  private async cases(): Promise<CaseSummary[]> {
    if (this.caseCache) {
      return this.caseCache;
    }
    try {
      const res = await this.bridges.get("graphagents").request({ method: "GET", path: "/api/cases" });
      const payload = res.payload as { cases?: CaseSummary[] } | undefined;
      const cases = res.status === 200 && Array.isArray(payload?.cases) ? payload!.cases! : [];
      this.caseCache = cases;
      return cases;
    } catch {
      return [];
    }
  }
}

/** tamaño del flujo: clausura dirigida desde cada raíz (misma semántica que
 * el scope "workflow" del canvas — reachableGraph en graphOps.ts). */
function toWorkflowLeaves(roots: GraphNode[], payload: GraphPayload, system: Provider): WorkflowLeafNode[] {
  const out = new Map<string, string[]>();
  for (const e of payload.edges) {
    const list = out.get(e.source);
    if (list) {
      list.push(e.target);
    } else {
      out.set(e.source, [e.target]);
    }
  }
  return [...roots]
    .sort((a, b) => a.id.localeCompare(b.id))
    .map((root): WorkflowLeafNode => {
      const visited = new Set<string>([root.id]);
      const stack = [root.id];
      while (stack.length > 0) {
        for (const next of out.get(stack.pop()!) ?? []) {
          if (!visited.has(next)) {
            visited.add(next);
            stack.push(next);
          }
        }
      }
      return {
        kind: "workflowLeaf",
        id: `workflow:${system}:${root.id}`,
        rootId: root.id,
        label: (root.label as string | undefined) ?? root.id,
        nodeCount: visited.size,
        system,
      };
    });
}

function describeExtra(n: GraphNode): string | undefined {
  if (typeof n["certification"] === "string") {
    return n["certification"] as string;
  }
  if (typeof n["archetype"] === "string") {
    return n["archetype"] as string;
  }
  return undefined;
}
