import { ExecInbound } from "../../../src/graph/messages";

interface VsCodeApi {
  postMessage(msg: unknown): void;
}

declare function acquireVsCodeApi(): VsCodeApi;

const vscode = acquireVsCodeApi();

export function send(msg: ExecInbound): void {
  vscode.postMessage(msg);
}
