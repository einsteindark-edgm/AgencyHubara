import * as vscode from "vscode";
import { GRAPH_PATH, GraphNode, isGraphPayload, Provider } from "../bridge/endpoints";
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
type TreeNode = GroupNode | CasesGroupNode | LeafNode | CaseLeafNode;

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
  private nodeCache = new Map<Provider, GraphNode[]>();
  private caseCache: CaseSummary[] | null = null;

  constructor(private readonly bridges: BridgeHub) {}

  refresh(): void {
    this.nodeCache.clear();
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
    if (element.kind === "caseLeaf") {
      const item = new vscode.TreeItem(element.title, vscode.TreeItemCollapsibleState.None);
      item.description = element.target;
      item.iconPath = new vscode.ThemeIcon("play-circle");
      item.contextValue = "acktosCase"; // habilita "▶ Replay"/"▶ Run durable" (package.json view/item/context)
      item.command = { command: "acktos.replayCase", title: "Replay", arguments: [element.caseId, element.title] };
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
      return [...GROUPS.map((g): GroupNode => ({ kind: "group", ...g })), { kind: "casesGroup", id: "cases" }];
    }
    if (element.kind === "group") {
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
    const cached = this.nodeCache.get(system);
    if (cached) {
      return cached;
    }
    try {
      const res = await this.bridges.get(system).request({ method: "GET", path: GRAPH_PATH });
      const nodes = res.status === 200 && isGraphPayload(res.payload) ? res.payload.nodes : [];
      this.nodeCache.set(system, nodes);
      return nodes;
    } catch {
      return [];
    }
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

function describeExtra(n: GraphNode): string | undefined {
  if (typeof n["certification"] === "string") {
    return n["certification"] as string;
  }
  if (typeof n["archetype"] === "string") {
    return n["archetype"] as string;
  }
  return undefined;
}
