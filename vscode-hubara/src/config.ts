import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";

/**
 * Lectura ÚNICA de las settings `hubara.*` (defaults incluidos). Puentes,
 * Test Explorer, diagnostics y el canvas consumen ESTE módulo — un default
 * que cambie acá cambia en todos los consumidores a la vez (antes cada uno
 * re-tipeaba sus defaults y podían driftear en silencio).
 */
export interface HubaraConfig {
  gaPython: string;
  /** ya resuelto (sin ${workspaceFolder}) */
  gaCwd: string;
  backendCommand: string[];
  /** ya resuelto */
  backendCwd: string;
  backendEnv: Record<string, string>;
  frontendNpm: string;
  frontendNpx: string;
  /** ya resuelto */
  frontendCwd: string;
  /** override de seams.yaml; "" = usar <repoRoot>/seams.yaml o el bundled */
  seamsPath: string;
}

export function readHubaraConfig(repoRoot: string): HubaraConfig {
  const cfg = vscode.workspace.getConfiguration("hubara");
  return {
    gaPython: cfg.get<string>("graphagents.python", "python3"),
    gaCwd: resolvePath(cfg.get<string>("graphagents.cwd", "${workspaceFolder}/GraphAgents"), repoRoot),
    backendCommand: cfg.get<string[]>("backend.command", ["uv", "run", "python"]),
    backendCwd: resolvePath(cfg.get<string>("backend.cwd", "${workspaceFolder}/hubara_agency"), repoRoot),
    // Gotcha documentado (project-context.md): sin estas dos vars, el sales
    // worker construye clientes Medusa a nivel de MÓDULO y `pytest -m
    // architecture` falla con un import roto ajeno al código bajo prueba.
    // Dummies bastan — nunca pega a Medusa real acá.
    backendEnv: cfg.get<Record<string, string>>("backend.env", {
      MEDUSA_BASE_URL: "http://localhost:9000",
      MEDUSA_ADMIN_TOKEN: "dev-token",
    }),
    frontendNpm: cfg.get<string>("frontend.npm", "npm"),
    frontendNpx: cfg.get<string>("frontend.npx", "npx"),
    frontendCwd: resolvePath(cfg.get<string>("frontend.cwd", "${workspaceFolder}/frontend_dashboard"), repoRoot),
    seamsPath: cfg.get<string>("seamsPath", ""),
  };
}

/** Raíz del monorepo para sustituir `${workspaceFolder}`: el primer candidato
 * que realmente contenga GraphAgents/ y hubara_agency/. Cubre multi-root y el
 * caso "abrí vscode-hubara/ como workspace" (la raíz es el padre). */
export function repoRoot(): string {
  const folders = vscode.workspace.workspaceFolders ?? [];
  const candidates: string[] = [];
  for (const f of folders) {
    candidates.push(f.uri.fsPath, path.dirname(f.uri.fsPath));
  }
  candidates.push(process.cwd());
  for (const c of candidates) {
    if (fs.existsSync(path.join(c, "GraphAgents")) && fs.existsSync(path.join(c, "hubara_agency"))) {
      return c;
    }
  }
  return folders.length > 0 ? folders[0].uri.fsPath : process.cwd();
}

export function resolvePath(value: string, root: string): string {
  const substituted = value.replace(/\$\{workspaceFolder\}/g, root);
  return path.isAbsolute(substituted) ? substituted : path.join(root, substituted);
}

/**
 * seams.yaml describe la topología del REPO del usuario, no de la extensión:
 * instalada como VSIX, el install dir es read-only y compartido entre
 * workspaces. Orden: setting `hubara.seamsPath` → `<repoRoot>/seams.yaml` →
 * el archivo bundled con la extensión (fallback de fábrica).
 */
export function seamsFilePath(extensionRoot: string): string {
  const root = repoRoot();
  const cfg = readHubaraConfig(root);
  if (cfg.seamsPath) {
    return resolvePath(cfg.seamsPath, root);
  }
  const wsSeams = path.join(root, "seams.yaml");
  if (fs.existsSync(wsSeams)) {
    return wsSeams;
  }
  return path.join(extensionRoot, "seams.yaml");
}
