// Vista "Flota" del contenedor Forge: un nodo por cliente (bundle en
// infra/forge/clients/), con su estado de redacción (TODO-BRAND pendientes,
// archivos requeridos faltantes). Read-only: las acciones (plan/apply) viven
// en los comandos del título/contexto y en la Forge Console.

import * as vscode from "vscode";
import { FleetClient, ForgeService } from "./forgeService";

type Node = ClientNode | DetailNode;

class ClientNode extends vscode.TreeItem {
  constructor(readonly client: FleetClient) {
    super(`${client.company} (${client.slug})`, vscode.TreeItemCollapsibleState.Collapsed);
    this.contextValue = "forgeClient";
    const pending = client.todoFiles.length + client.missingRequired.length;
    this.description = pending === 0 ? "listo para forjar" : `${pending} pendientes`;
    this.iconPath = new vscode.ThemeIcon(
      pending === 0 ? "pass-filled" : "flame",
      pending === 0 ? new vscode.ThemeColor("testing.iconPassed") : undefined,
    );
    this.tooltip = [
      client.repo,
      `SSM ${client.ssmPrefix}/${client.slug} · ${client.resourcePrefix}-*`,
      client.productDescription,
    ]
      .filter(Boolean)
      .join("\n");
  }
}

class DetailNode extends vscode.TreeItem {
  constructor(label: string, icon: string, opts?: { file?: string; color?: string }) {
    super(label, vscode.TreeItemCollapsibleState.None);
    this.iconPath = new vscode.ThemeIcon(
      icon,
      opts?.color ? new vscode.ThemeColor(opts.color) : undefined,
    );
    if (opts?.file) {
      this.command = {
        command: "vscode.open",
        title: "Abrir",
        arguments: [vscode.Uri.file(opts.file)],
      };
    }
  }
}

export class FleetTreeProvider implements vscode.TreeDataProvider<Node>, vscode.Disposable {
  private readonly changed = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this.changed.event;
  private readonly sub: vscode.Disposable;

  constructor(private readonly service: ForgeService) {
    this.sub = service.onFleetChanged(() => this.changed.fire());
  }

  refresh(): void {
    this.changed.fire();
  }

  getTreeItem(node: Node): vscode.TreeItem {
    return node;
  }

  getChildren(node?: Node): Node[] {
    if (!node) return this.service.listClients().map((c) => new ClientNode(c));
    if (!(node instanceof ClientNode)) return [];
    const c = node.client;
    const out: Node[] = [
      new DetailNode("client.yaml", "gear", { file: c.clientYamlPath }),
      new DetailNode(`workspace (${c.workspaceFileCount} archivos)`, "folder", {
        file: c.bundleDir + "/workspace",
      }),
    ];
    for (const missing of c.missingRequired) {
      out.push(new DetailNode(`falta ${missing}`, "error", { color: "testing.iconFailed" }));
    }
    for (const todo of c.todoFiles) {
      out.push(
        new DetailNode(`TODO-BRAND: ${todo}`, "edit", {
          file: `${c.bundleDir}/${todo}`,
          color: "list.warningForeground",
        }),
      );
    }
    return out;
  }

  dispose(): void {
    this.changed.dispose();
    this.sub.dispose();
  }
}
