import { ExportInbound } from "../../../src/export/messages";

interface VsCodeApi {
  postMessage(msg: unknown): void;
}

declare function acquireVsCodeApi(): VsCodeApi;

const vscode = acquireVsCodeApi();

export function send(msg: ExportInbound): void {
  vscode.postMessage(msg);
}
