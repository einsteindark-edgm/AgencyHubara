import { readHubaraConfig } from "../config";
import { ResolvedCommand } from "./runner";
import { SuiteDef } from "./suites";

/**
 * Resuelve el comando concreto de una suite a partir de la config `acktos.*`
 * — las MISMAS settings (misma lectura: src/config.ts) que usan los puentes.
 * `${py}`/`${pyRun}`/`${npm}`/`${npx}` se expanden acá; `${junit}` lo
 * resuelve el runner (necesita el tmp path en tiempo de ejecución).
 */
export function makeSuiteResolver(
  repoRoot: string,
  _resolvePath: (value: string, root: string) => string,
): (suite: SuiteDef) => ResolvedCommand {
  const cfg = readHubaraConfig(repoRoot);
  const backendRun =
    cfg.backendCommand[cfg.backendCommand.length - 1] === "python" ? cfg.backendCommand.slice(0, -1) : cfg.backendCommand;

  return (suite: SuiteDef): ResolvedCommand => {
    const map: Record<string, string[]> = {
      "${py}": suite.lado === "graphagents" ? [cfg.gaPython] : suite.lado === "backend" ? cfg.backendCommand : [],
      "${pyRun}": backendRun,
      "${npm}": [cfg.frontendNpm],
      "${npx}": [cfg.frontendNpx],
    };
    const cwd = suite.lado === "graphagents" ? cfg.gaCwd : suite.lado === "backend" ? cfg.backendCwd : cfg.frontendCwd;
    const argv = suite.command.flatMap((token) => map[token] ?? [token]);
    return { cwd, argv, env: suite.lado === "backend" ? cfg.backendEnv : undefined };
  };
}
