import { spawn } from "node:child_process";
import * as fs from "node:fs/promises";
import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";
import { JUnitCase, parseJUnitXml } from "./junit";
import { SuiteDef } from "./suites";

export interface ResolvedCommand {
  cwd: string;
  argv: string[];
  /** env extra fusionado sobre `process.env` (p. ej. dummies MEDUSA_* que el
   * backend necesita a nivel de import — ver `resolve.ts`). */
  env?: Record<string, string>;
}

export interface SuiteRunResult {
  exitCode: number | null;
  output: string;
  /** null si la suite no produce JUnit (CLI suites tipo compiler/lint-imports/build). */
  cases: JUnitCase[] | null;
}

/**
 * Corre UNA suite (comando ya resuelto por `resolveCommand`). Si `suite.junit`
 * es true, le agrega `--junitxml=<tmp>` (pytest) o sustituye `${junit}` en el
 * comando (vitest, que necesita el path explícito) y parsea el resultado para
 * granularidad por-test. Respeta cancelación matando el proceso hijo.
 */
export async function runSuite(
  suite: SuiteDef,
  resolved: ResolvedCommand,
  token: vscode.CancellationToken,
  onOutput: (chunk: string) => void,
): Promise<SuiteRunResult> {
  let argv = resolved.argv;
  let junitPath: string | undefined;
  if (suite.junit) {
    junitPath = path.join(os.tmpdir(), `hubara-junit-${Date.now()}-${Math.random().toString(36).slice(2)}.xml`);
    const hasPlaceholder = argv.some((a) => a.includes("${junit}"));
    argv = argv.map((a) => a.replace("${junit}", junitPath!));
    if (!hasPlaceholder) {
      argv = [...argv, `--junitxml=${junitPath}`];
    }
  }
  const [cmd, ...args] = argv;

  return new Promise<SuiteRunResult>((resolvePromise) => {
    let child: ReturnType<typeof spawn>;
    try {
      child = spawn(cmd, args, { cwd: resolved.cwd, env: { ...process.env, ...resolved.env } });
    } catch (e) {
      resolvePromise({ exitCode: null, output: `no pude arrancar el proceso: ${e}`, cases: null });
      return;
    }
    let output = "";
    const onData = (d: Buffer) => {
      const text = d.toString("utf-8");
      output += text;
      onOutput(text);
    };
    child.stdout?.on("data", onData);
    child.stderr?.on("data", onData);

    const cancelSub = token.onCancellationRequested(() => child.kill());

    child.on("close", async (code) => {
      cancelSub.dispose();
      let cases: JUnitCase[] | null = null;
      if (junitPath) {
        try {
          const xml = await fs.readFile(junitPath, "utf-8");
          cases = parseJUnitXml(xml);
        } catch {
          // el comando pudo fallar ANTES de escribir el xml (crash del
          // runner, no de un test individual) — cases queda null, el
          // caller cae al veredicto por exit code.
        } finally {
          void fs.unlink(junitPath).catch(() => undefined);
        }
      }
      resolvePromise({ exitCode: code, output, cases });
    });
    child.on("error", (err) => {
      cancelSub.dispose();
      resolvePromise({ exitCode: null, output: output + `\n[error de proceso] ${err.message}`, cases: null });
    });
  });
}
