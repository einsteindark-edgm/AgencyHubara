// Panel visual del export (dist/exportview.js) — la "pantalla de relaciones":
// un grafo con las unidades (plugins + graph agents) y sus aristas
// (depends_on · seam ⚡ · agent://), con un checkbox por unidad. Devuelve los
// ids compuestos SELECCIONADOS, o null si el operador cierra sin confirmar.
// Piel pura: no computa nada, solo dibuja lo que el comando ya resolvió.

import * as vscode from "vscode";
import { ExportGraph, ExportInbound } from "./messages";

export class ExportPanel {
  private settled = false;
  private readonly disposables: vscode.Disposable[] = [];

  private constructor(
    private readonly panel: vscode.WebviewPanel,
    ctx: vscode.ExtensionContext,
    graph: ExportGraph,
    private readonly resolve: (selected: string[] | null) => void,
  ) {
    panel.webview.html = this.getHtml(panel.webview, ctx);
    this.disposables.push(
      panel.onDidDispose(() => {
        this.finish(null); // cerró sin confirmar → cancelado
        for (const d of this.disposables) {
          d.dispose();
        }
      }),
      panel.webview.onDidReceiveMessage((msg: ExportInbound) => {
        switch (msg.type) {
          case "ready":
            void panel.webview.postMessage({ type: "graph", ...graph });
            break;
          case "confirm":
            this.finish(msg.selected);
            this.panel.dispose();
            break;
          case "cancel":
            this.panel.dispose();
            break;
        }
      }),
    );
  }

  private finish(result: string[] | null): void {
    if (this.settled) {
      return;
    }
    this.settled = true;
    this.resolve(result);
  }

  /** Abre el panel y resuelve con los ids compuestos elegidos (o null). */
  static open(ctx: vscode.ExtensionContext, graph: ExportGraph): Promise<string[] | null> {
    return new Promise((resolve) => {
      const panel = vscode.window.createWebviewPanel(
        "acktos.exportView",
        `Exportar: ${graph.packageName}`,
        vscode.ViewColumn.Active,
        { enableScripts: true, retainContextWhenHidden: true },
      );
      panel.iconPath = new vscode.ThemeIcon("export");
      new ExportPanel(panel, ctx, graph, resolve);
    });
  }

  private getHtml(webview: vscode.Webview, ctx: vscode.ExtensionContext): string {
    const scriptUri = webview.asWebviewUri(vscode.Uri.joinPath(ctx.extensionUri, "dist", "exportview.js"));
    const styleUri = webview.asWebviewUri(vscode.Uri.joinPath(ctx.extensionUri, "dist", "exportview.css"));
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
  <title>Exportar paquete</title>
</head>
<body>
  <div id="root"></div>
  <script src="${scriptUri}"></script>
</body>
</html>`;
  }
}
