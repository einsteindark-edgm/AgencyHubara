// Forge Console — el panel webview (dist/forgeview.js). Piel sobre el CLI:
// cada acción del panel se traduce a `forge <cmd>` vía ForgeService y el
// output vuelve streameado. Los secretos NUNCA pasan por acá (van a SSM por
// los CLIs de provisioning, fuera de banda).

import * as vscode from "vscode";
import { ForgeService } from "./forgeService";
import { ForgeInbound, ForgeOutbound } from "./messages";

export class ForgeConsolePanel {
  static current: ForgeConsolePanel | undefined;
  private readonly disposables: vscode.Disposable[] = [];

  static open(ctx: vscode.ExtensionContext, service: ForgeService): void {
    if (ForgeConsolePanel.current) {
      ForgeConsolePanel.current.panel.reveal();
      return;
    }
    const panel = vscode.window.createWebviewPanel(
      "forge.console",
      "Forge Console",
      vscode.ViewColumn.Active,
      { enableScripts: true, retainContextWhenHidden: true },
    );
    panel.iconPath = new vscode.ThemeIcon("flame");
    ForgeConsolePanel.current = new ForgeConsolePanel(panel, ctx, service);
  }

  private constructor(
    private readonly panel: vscode.WebviewPanel,
    ctx: vscode.ExtensionContext,
    private readonly service: ForgeService,
  ) {
    panel.webview.html = this.getHtml(panel.webview, ctx);
    this.disposables.push(
      panel.onDidDispose(() => this.dispose()),
      panel.webview.onDidReceiveMessage((msg: ForgeInbound) => void this.onMessage(msg)),
      service.onRun((ev) => {
        if (ev.kind === "start") this.post({ type: "runStart", runId: ev.runId, cmd: ev.cmd ?? "" });
        if (ev.kind === "line") this.post({ type: "runLine", runId: ev.runId, line: ev.line ?? "" });
        if (ev.kind === "end") this.post({ type: "runEnd", runId: ev.runId, code: ev.code ?? -1 });
      }),
      service.onFleetChanged(() => this.sendFleet()),
    );
  }

  private post(msg: ForgeOutbound): void {
    void this.panel.webview.postMessage(msg);
  }

  private sendFleet(): void {
    this.post({
      type: "fleet",
      clients: this.service.listClients(),
      repoRoot: this.service.repoRoot,
    });
  }

  private async onMessage(msg: ForgeInbound): Promise<void> {
    switch (msg.type) {
      case "ready":
      case "refresh":
        this.sendFleet();
        break;
      case "initClient":
        if (/^[a-z][a-z0-9_]*$/.test(msg.slug)) {
          await this.service.run("init", [msg.slug]);
        } else {
          void vscode.window.showErrorMessage(
            `forge: slug inválido "${msg.slug}" (minúsculas/dígitos/_)`,
          );
        }
        break;
      case "plan":
        await this.service.run("plan", [msg.slug]);
        break;
      case "apply": {
        // Acción con efectos fuera del repo (crea la carpeta del clon):
        // confirmación modal SIEMPRE, con el destino visible.
        const ok = await vscode.window.showWarningMessage(
          `Forjar el clon de "${msg.slug}" en:\n${msg.dest}`,
          { modal: true, detail: "Crea la carpeta del repo nuevo. No toca AWS ni GitHub." },
          "Forjar",
        );
        if (ok === "Forjar") {
          const args = [msg.slug, "--dest", msg.dest];
          if (msg.allowTodos) args.push("--allow-todos");
          await this.service.run("apply", args);
        }
        break;
      }
      case "verify":
        await this.service.run("verify", [msg.dest, "--client", msg.slug]);
        break;
      case "publish":
        // publish solo IMPRIME los comandos gh (el operador los corre a mano).
        await this.service.run("publish", [msg.dest, "--client", msg.slug]);
        break;
      case "openPath":
        void vscode.commands.executeCommand("vscode.open", vscode.Uri.file(msg.fsPath));
        break;
      case "pickDest": {
        const picked = await vscode.window.showOpenDialog({
          canSelectFiles: false,
          canSelectFolders: true,
          canSelectMany: false,
          openLabel: "Carpeta destino del clon",
        });
        if (picked?.[0]) {
          this.post({ type: "destPicked", slug: msg.slug, dest: picked[0].fsPath });
        }
        break;
      }
    }
  }

  private getHtml(webview: vscode.Webview, ctx: vscode.ExtensionContext): string {
    const scriptUri = webview.asWebviewUri(
      vscode.Uri.joinPath(ctx.extensionUri, "dist", "forgeview.js"),
    );
    const styleUri = webview.asWebviewUri(
      vscode.Uri.joinPath(ctx.extensionUri, "dist", "forgeview.css"),
    );
    const csp = [
      "default-src 'none'",
      `img-src ${webview.cspSource} data:`,
      `style-src ${webview.cspSource} 'unsafe-inline'`,
      `script-src ${webview.cspSource}`,
      `font-src ${webview.cspSource}`,
    ].join("; ");
    return `<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="Content-Security-Policy" content="${csp}" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <link rel="stylesheet" href="${styleUri}" />
  <title>Forge Console</title>
</head>
<body>
  <div id="root"></div>
  <script src="${scriptUri}"></script>
</body>
</html>`;
  }

  private dispose(): void {
    ForgeConsolePanel.current = undefined;
    for (const d of this.disposables) d.dispose();
  }
}
