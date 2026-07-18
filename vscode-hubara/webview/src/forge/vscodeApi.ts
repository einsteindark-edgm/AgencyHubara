import { ForgeInbound } from "../../../src/forge/messages";

interface VsCodeApi {
  postMessage(msg: unknown): void;
}

declare function acquireVsCodeApi(): VsCodeApi;

const vscode = acquireVsCodeApi();

export function send(msg: ForgeInbound): void {
  vscode.postMessage(msg);
}
