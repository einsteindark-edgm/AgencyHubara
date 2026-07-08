import type { InboundMessage } from "../../src/graph/messages";

interface VsCodeApi {
  postMessage(msg: InboundMessage): void;
  getState(): unknown;
  setState(state: unknown): void;
}
declare function acquireVsCodeApi(): VsCodeApi;

// `acquireVsCodeApi()` solo puede llamarse UNA VEZ por webview — singleton
// para que ningún módulo lo re-invoque por accidente (React StrictMode
// remonta componentes en dev y llamarla dos veces tira una excepción).
export const vscode: VsCodeApi = acquireVsCodeApi();

export function send(msg: InboundMessage): void {
  vscode.postMessage(msg);
}
