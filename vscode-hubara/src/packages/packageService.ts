import { spawn } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";
import { augmentedPath } from "../bridge/pythonBridge";
import { HubaraConfig, readHubaraConfig, repoRoot } from "../config";

/**
 * Export/Install de paquetes Acktos (.acktospkg) desde Studio.
 *
 * Regla de oro D-10 (misma que Forge Console): la UI es piel, los CLIs son
 * músculo. Cada paso ejecuta los verbos `package` de los DOS CLIs
 * (`src.sdk.cli` para plugins hubara · `sdk.cli` de GraphAgents para graph
 * agents) — cero lógica de empaquetado en la extensión. El flujo de install
 * replica el contrato del operador: rama nueva → instalar → codegen →
 * certificar → commit → merge a la default (o dejar la rama).
 *
 * Los dos flujos corren contra el repo ABIERTO en VS Code (repoRoot()):
 * exportás parado en el repo central, instalás parado en el clon (Vincenzo) —
 * los clones forjados comparten la topología, así que los mismos comandos
 * sirven en ambos lados.
 */

interface CliResult {
  code: number;
  stdout: string;
  stderr: string;
}

interface PlanInstallUnit {
  id: string;
  kind: string;
  action: string; // new | overwrite | foreign
  version?: string;
  target_version?: string | null;
  downgrade?: boolean;
}

interface HubaraInstallPlan {
  units: PlanInstallUnit[];
  missing_plugins: string[];
  post_steps: string[];
}

interface GaInstallPlan {
  units: PlanInstallUnit[];
  missing_agents: string[];
  post_steps: string[];
}

function run(
  cmd: string,
  args: string[],
  cwd: string,
  env: Record<string, string>,
  log: (line: string) => void,
): Promise<CliResult> {
  return new Promise((resolve, reject) => {
    log(`$ ${cmd} ${args.join(" ")}   (cwd ${cwd})`);
    const child = spawn(cmd, args, {
      cwd,
      env: { ...process.env, ...env, PATH: augmentedPath() },
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d: Buffer) => {
      stdout += d.toString();
    });
    child.stderr.on("data", (d: Buffer) => {
      stderr += d.toString();
      for (const line of d.toString().split("\n")) {
        if (line.trim()) {
          log(`  ! ${line}`);
        }
      }
    });
    child.on("error", reject);
    child.on("close", (code) => resolve({ code: code ?? 1, stdout, stderr }));
  });
}

function parseJson<T>(res: CliResult, what: string): T {
  if (res.code !== 0) {
    throw new Error(`${what} falló (exit ${res.code}): ${res.stderr.trim() || res.stdout.trim()}`);
  }
  try {
    return JSON.parse(res.stdout) as T;
  } catch {
    throw new Error(`${what}: no pude parsear la salida JSON (¿cambió el CLI?): ${res.stdout.slice(0, 200)}`);
  }
}

/** hubara CLI: cfg.backendCommand = ["uv","run","python"] + `-m src.sdk.cli …` */
function hubaraCli(cfg: HubaraConfig, args: string[], log: (l: string) => void): Promise<CliResult> {
  const [cmd, ...pre] = cfg.backendCommand;
  return run(cmd, [...pre, "-m", "src.sdk.cli", ...args], cfg.backendCwd, cfg.backendEnv, log);
}

/** GraphAgents CLI: `python3 -m sdk.cli …` con cwd en el GA root. */
function gaCli(cfg: HubaraConfig, args: string[], log: (l: string) => void): Promise<CliResult> {
  return run(cfg.gaPython, ["-m", "sdk.cli", ...args], cfg.gaCwd, cfg.gaEnv, log);
}

function git(args: string[], cwd: string, log: (l: string) => void): Promise<CliResult> {
  return run("git", args, cwd, {}, log);
}

async function gitOrThrow(args: string[], cwd: string, log: (l: string) => void): Promise<string> {
  const res = await git(args, cwd, log);
  if (res.code !== 0) {
    throw new Error(`git ${args.join(" ")} falló: ${res.stderr.trim() || res.stdout.trim()}`);
  }
  return res.stdout.trim();
}

// ---------------------------------------------------------------------------
// catálogo local (solo para poblar el picker — las operaciones van por CLI)
// ---------------------------------------------------------------------------

function listPlugins(root: string): string[] {
  const pluginsDir = path.join(root, "frontend_dashboard", "src", "plugins");
  if (!fs.existsSync(pluginsDir)) {
    return [];
  }
  return fs
    .readdirSync(pluginsDir)
    .filter((d) => !d.startsWith("_") && fs.existsSync(path.join(pluginsDir, d, "plugin.yaml")))
    .sort();
}

function listGraphAgents(gaCwd: string): Array<{ id: string; kind: "agent" | "taskgraph" }> {
  const manifests = path.join(gaCwd, "manifests");
  if (!fs.existsSync(manifests)) {
    return [];
  }
  const out: Array<{ id: string; kind: "agent" | "taskgraph" }> = [];
  for (const f of fs.readdirSync(manifests).sort()) {
    if (f.endsWith(".agent.yaml")) {
      out.push({ id: f.slice(0, -".agent.yaml".length), kind: "agent" });
    } else if (f.endsWith(".taskgraph.yaml")) {
      out.push({ id: f.slice(0, -".taskgraph.yaml".length), kind: "taskgraph" });
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Exportar
// ---------------------------------------------------------------------------

export async function exportPackage(output: vscode.OutputChannel): Promise<void> {
  const root = repoRoot();
  const cfg = readHubaraConfig(root);
  const log = (l: string) => output.appendLine(`[package] ${l}`);

  const plugins = listPlugins(root);
  const agents = listGraphAgents(cfg.gaCwd);
  if (plugins.length === 0 && agents.length === 0) {
    void vscode.window.showWarningMessage("Acktos Studio: no encontré plugins ni graph agents en este repo.");
    return;
  }

  type Item = vscode.QuickPickItem & { unitKind: "plugin" | "graphagent"; id: string };
  const items: Item[] = [
    ...plugins.map<Item>((id) => ({ label: `$(package) ${id}`, description: "plugin hubara", unitKind: "plugin", id })),
    ...agents.map<Item>((a) => ({
      label: `$(type-hierarchy) ${a.id}`,
      description: a.kind === "taskgraph" ? "graph agent (taskgraph)" : "graph agent",
      unitKind: "graphagent",
      id: a.id,
    })),
  ];
  const picked = await vscode.window.showQuickPick(items, {
    canPickMany: true,
    placeHolder: "¿Qué exportar al paquete? (la clausura de dependencias se agrega sola)",
    title: "Acktos: exportar paquete",
  });
  if (!picked || picked.length === 0) {
    return;
  }
  const pluginIds = picked.filter((i) => i.unitKind === "plugin").map((i) => i.id);
  const agentIds = picked.filter((i) => i.unitKind === "graphagent").map((i) => i.id);

  // planes (sin efectos) — muestran la clausura + requirements antes de sellar
  const detail: string[] = [];
  try {
    if (pluginIds.length > 0) {
      const plan = parseJson<{
        units: Array<{ id: string; archetype: string; requires: { plugins: string[]; env_vars: string[]; secrets: string[] } }>;
      }>(await hubaraCli(cfg, ["package", "plan", ...pluginIds, "--json"], log), "package plan (hubara)");
      for (const u of plan.units) {
        const extras = [
          u.requires.env_vars.length ? `env: ${u.requires.env_vars.join(", ")}` : "",
          u.requires.secrets.length ? `secrets: ${u.requires.secrets.join(", ")}` : "",
        ]
          .filter(Boolean)
          .join(" · ");
        detail.push(`plugin ${u.id} [${u.archetype}]${extras ? ` — ${extras}` : ""}`);
      }
    }
    if (agentIds.length > 0) {
      const plan = parseJson<{
        units: Array<{ id: string; archetype: string; files: string[]; requires: { ports: string[] } }>;
      }>(await gaCli(cfg, ["package", "plan", ...agentIds, "--json"], log), "package plan (GraphAgents)");
      for (const u of plan.units) {
        const ports = u.requires.ports.length ? ` — ports: ${u.requires.ports.join(", ")}` : "";
        detail.push(`graphagent ${u.id} [${u.archetype}] (${u.files.length} archivos)${ports}`);
      }
    }
  } catch (e) {
    void vscode.window.showErrorMessage(`Acktos Studio: no pude armar el plan de export — ${e instanceof Error ? e.message : e}`);
    return;
  }

  const confirm = await vscode.window.showInformationMessage(
    `¿Exportar ${detail.length} unidad(es) al paquete?`,
    { modal: true, detail: detail.join("\n") },
    "Exportar",
  );
  if (confirm !== "Exportar") {
    return;
  }

  const defaultName = [...pluginIds, ...agentIds].join("+");
  const target = await vscode.window.showSaveDialog({
    defaultUri: vscode.Uri.file(path.join(root, "dist", `${defaultName}.acktospkg`)),
    filters: { "Acktos package": ["acktospkg"] },
    title: "Guardar paquete Acktos",
  });
  if (!target) {
    return;
  }

  try {
    await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: "Acktos: exportando paquete…" },
      async (progress) => {
        let staging: string | undefined;
        if (agentIds.length > 0 && pluginIds.length > 0) {
          staging = fs.mkdtempSync(path.join(os.tmpdir(), "acktos-stage-"));
        }
        if (agentIds.length > 0) {
          progress.report({ message: "stageando graph agents…" });
          if (pluginIds.length > 0) {
            const res = await gaCli(cfg, ["package", "stage", ...agentIds, "--staging", staging!], log);
            if (res.code !== 0) {
              throw new Error(`package stage (GraphAgents) falló: ${res.stderr.trim()}`);
            }
          } else {
            const res = await gaCli(
              cfg,
              ["package", "build", ...agentIds, "-o", target.fsPath, "--name", defaultName],
              log,
            );
            if (res.code !== 0) {
              throw new Error(`package build (GraphAgents) falló: ${res.stderr.trim()}`);
            }
          }
        }
        if (pluginIds.length > 0) {
          progress.report({ message: "sellando el paquete…" });
          const args = ["package", "build", ...pluginIds, "-o", target.fsPath, "--name", defaultName];
          if (staging) {
            args.push("--staging", staging);
          }
          const res = await hubaraCli(cfg, args, log);
          if (res.code !== 0) {
            throw new Error(`package build (hubara) falló: ${res.stderr.trim()}`);
          }
        }
      },
    );
  } catch (e) {
    void vscode.window.showErrorMessage(`Acktos Studio: export falló — ${e instanceof Error ? e.message : e}`);
    return;
  }

  const action = await vscode.window.showInformationMessage(
    `Acktos Studio: paquete exportado → ${path.basename(target.fsPath)}`,
    "Revelar",
  );
  if (action === "Revelar") {
    void vscode.commands.executeCommand("revealFileInOS", target);
  }
}

// ---------------------------------------------------------------------------
// Instalar
// ---------------------------------------------------------------------------

/** El start point de la rama de install: la default LOCAL si existe, si no
 * origin/<default>, si no el HEAD actual (con nota en el log). */
async function resolveStartPoint(root: string, base: string, log: (l: string) => void): Promise<string> {
  if ((await git(["rev-parse", "--verify", base], root, () => void 0)).code === 0) {
    return base;
  }
  if ((await git(["rev-parse", "--verify", `origin/${base}`], root, () => void 0)).code === 0) {
    return `origin/${base}`;
  }
  log(`  ! sin ref local ni origin/ para '${base}' — forkeo desde HEAD`);
  return "HEAD";
}

/** El flujo falló a mitad de camino. El working tree estaba LIMPIO (tracked)
 * al arrancar, así que volver al estado previo es seguro: reset --hard
 * restaura lo tracked, checkout -f vuelve a la ref previa y se borra la rama
 * de install. Archivos NUEVOS del install quedan untracked (visibles en git
 * status) — se avisa en vez de correr un clean -fd que podría llevarse
 * untracked del operador. */
async function offerRollback(
  root: string,
  prevRef: string,
  branch: string,
  log: (l: string) => void,
  err: string,
): Promise<void> {
  if (!branch || !prevRef) {
    void vscode.window.showErrorMessage(`Acktos Studio: install falló — ${err}`);
    return;
  }
  const choice = await vscode.window.showErrorMessage(
    `Acktos Studio: install falló — ${err}`,
    "Rollback al estado previo",
    "Dejar como está",
  );
  if (choice !== "Rollback al estado previo") {
    return;
  }
  try {
    await gitOrThrow(["reset", "--hard"], root, log);
    await gitOrThrow(["checkout", "-f", prevRef], root, log);
    await git(["branch", "-D", branch], root, log);
    void vscode.window.showInformationMessage(
      `Acktos Studio: rollback listo — de vuelta en ${prevRef}. Si el install llegó a escribir archivos nuevos, quedaron untracked (git status los muestra).`,
    );
  } catch (e) {
    void vscode.window.showErrorMessage(`Acktos Studio: el rollback falló — ${e instanceof Error ? e.message : e}. Revisá git status.`);
  }
}

async function defaultBranch(root: string, log: (l: string) => void): Promise<string> {
  const head = await git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], root, log);
  if (head.code === 0 && head.stdout.trim()) {
    return head.stdout.trim().replace(/^origin\//, "");
  }
  for (const candidate of ["main", "master"]) {
    const res = await git(["rev-parse", "--verify", candidate], root, log);
    if (res.code === 0) {
      return candidate;
    }
  }
  return "main";
}

export async function installPackage(output: vscode.OutputChannel): Promise<void> {
  const root = repoRoot();
  const cfg = readHubaraConfig(root);
  const log = (l: string) => output.appendLine(`[package] ${l}`);

  const pickedFile = await vscode.window.showOpenDialog({
    canSelectMany: false,
    filters: { "Acktos package": ["acktospkg"] },
    title: "Elegí el paquete Acktos a instalar",
  });
  if (!pickedFile || pickedFile.length === 0) {
    return;
  }
  const pkg = pickedFile[0].fsPath;
  const pkgName = path.basename(pkg, ".acktospkg");

  // plan-install (sin efectos): el CLI hubara lista TODAS las unidades (las
  // graphagent le son foreign) — de ahí salen los kinds presentes.
  let hubaraPlan: HubaraInstallPlan;
  try {
    hubaraPlan = parseJson<HubaraInstallPlan>(
      await hubaraCli(cfg, ["package", "plan-install", pkg, "--json", "--repo", root], log),
      "package plan-install (hubara)",
    );
  } catch (e) {
    void vscode.window.showErrorMessage(
      `Acktos Studio: no pude leer el paquete — ${e instanceof Error ? e.message : e}. ¿La plataforma del repo destino soporta paquetes (verbo \`package\` del CLI)?`,
    );
    return;
  }
  const pluginUnits = hubaraPlan.units.filter((u) => u.kind === "plugin");
  const hasGraphAgents = hubaraPlan.units.some((u) => u.kind === "graphagent");

  let gaPlan: GaInstallPlan | undefined;
  if (hasGraphAgents) {
    try {
      gaPlan = parseJson<GaInstallPlan>(
        await gaCli(cfg, ["package", "plan-install", pkg, "--json", "--root", cfg.gaCwd], log),
        "package plan-install (GraphAgents)",
      );
    } catch (e) {
      void vscode.window.showErrorMessage(`Acktos Studio: plan-install GraphAgents falló — ${e instanceof Error ? e.message : e}`);
      return;
    }
  }
  const agentUnits = (gaPlan?.units ?? []).filter((u) => u.kind === "graphagent");

  const pluginLine = (u: PlanInstallUnit) => {
    const versions = u.target_version ? ` (${u.target_version} → ${u.version ?? "?"})` : "";
    const flag = u.downgrade ? "  ⚠ DOWNGRADE" : "";
    return `${u.action === "overwrite" ? "~ actualiza" : "+ instala"} plugin ${u.id}${versions}${flag}`;
  };
  const detail: string[] = [
    ...pluginUnits.map(pluginLine),
    ...agentUnits.map((u) => `${u.action === "overwrite" ? "~ actualiza" : "+ instala"} graphagent ${u.id}`),
  ];
  const missing = [...hubaraPlan.missing_plugins, ...(gaPlan?.missing_agents ?? [])];
  if (missing.length > 0) {
    detail.push("", `⚠ dependencias que faltan en este repo: ${missing.join(", ")}`);
  }
  detail.push("", "Flujo: rama nueva → instalar → codegen → certificar → commit → merge (elegís al final).");

  const confirm = await vscode.window.showInformationMessage(
    `¿Instalar el paquete '${pkgName}' en este repo?`,
    { modal: true, detail: detail.join("\n") },
    "Instalar",
  );
  if (confirm !== "Instalar") {
    return;
  }

  const certifyProblems: string[] = [];
  const base = await defaultBranch(root, log);
  let branch = "";
  let prevRef = "";
  try {
    await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: `Acktos: instalando '${pkgName}'…` },
      async (progress) => {
        // 1. working tree limpio (solo TRACKED: un .acktospkg descargado u
        //    otro untracked no bloquea el install ni entra al commit — los
        //    paths del paquete quedan ignorados por .gitignore)
        const status = await gitOrThrow(["status", "--porcelain", "--untracked-files=no"], root, log);
        if (status) {
          throw new Error("el working tree tiene cambios sin commitear — commiteá o stasheá antes de instalar");
        }
        prevRef = await gitOrThrow(["rev-parse", "--abbrev-ref", "HEAD"], root, () => void 0);
        if (prevRef === "HEAD") {
          prevRef = await gitOrThrow(["rev-parse", "HEAD"], root, () => void 0); // detached
        }

        // 2. rama de instalación — SIEMPRE forkea de la default. Si forkeara
        //    del HEAD del operador (una feature branch), el merge final
        //    arrastraría esa rama entera a la default.
        const startPoint = await resolveStartPoint(root, base, log);
        const stamp = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 12);
        branch = `acktos/install-${pkgName.replace(/[^a-zA-Z0-9._-]/g, "-")}-${stamp}`;
        progress.report({ message: `creando rama ${branch} desde ${startPoint}…` });
        await gitOrThrow(["checkout", "-B", branch, startPoint], root, log);

        // 3. instalar (cada CLI su kind)
        if (pluginUnits.length > 0) {
          progress.report({ message: "instalando plugins…" });
          const res = await hubaraCli(cfg, ["package", "install", pkg, "--json", "--repo", root], log);
          if (res.code !== 0) {
            throw new Error(`package install (hubara) falló: ${res.stderr.trim()}`);
          }
        }
        if (agentUnits.length > 0) {
          progress.report({ message: "instalando graph agents…" });
          const res = await gaCli(cfg, ["package", "install", pkg, "--json", "--root", cfg.gaCwd], log);
          if (res.code !== 0) {
            throw new Error(`package install (GraphAgents) falló: ${res.stderr.trim()}`);
          }
        }

        // 4. codegen — lo compartido se REGENERA, nunca se edita a mano
        if (pluginUnits.length > 0) {
          progress.report({ message: "regenerando registry + compose…" });
          const sync = await run(cfg.frontendNpm, ["run", "plugins:sync"], cfg.frontendCwd, {}, log);
          if (sync.code !== 0) {
            throw new Error(`plugins:sync falló: ${sync.stderr.trim() || sync.stdout.trim()}`);
          }
          const [cmd, ...pre] = cfg.backendCommand;
          const compose = await run(cmd, [...pre, "scripts/render-compose.py"], cfg.backendCwd, cfg.backendEnv, log);
          if (compose.code !== 0) {
            throw new Error(`render-compose falló: ${compose.stderr.trim() || compose.stdout.trim()}`);
          }
        }

        // 5. certificación en el DESTINO (los gates rápidos; las suites
        //    completas corren después como siempre). No frena el commit: un
        //    fallo deja la rama para diagnóstico, sin merge.
        progress.report({ message: "certificando…" });
        if (pluginUnits.length > 0) {
          const ids = pluginUnits.map((u) => u.id);
          const cert = await hubaraCli(cfg, ["certify", ...ids], log);
          if (cert.code !== 0) {
            certifyProblems.push(`certify hubara (${ids.join(", ")}) — ver Output`);
            log(cert.stdout);
          }
        }
        if (agentUnits.length > 0) {
          const check = await gaCli(cfg, ["check", ...agentUnits.map((u) => u.id)], log);
          if (check.code !== 0) {
            certifyProblems.push("check GraphAgents — ver Output");
            log(check.stdout);
          }
        }

        // 6. commit
        progress.report({ message: "commiteando…" });
        await gitOrThrow(["add", "-A"], root, log);
        const unitList = [...pluginUnits.map((u) => `plugin:${u.id}`), ...agentUnits.map((u) => `graphagent:${u.id}`)];
        await gitOrThrow(
          ["commit", "-m", `feat(acktos): instala paquete ${pkgName} (${unitList.join(", ")})`],
          root,
          log,
        );
      },
    );
  } catch (e) {
    await offerRollback(root, prevRef, branch, log, e instanceof Error ? e.message : String(e));
    return;
  }

  // 7. merge a la default — la decisión final es del operador
  if (certifyProblems.length > 0) {
    void vscode.window.showWarningMessage(
      `Acktos Studio: instalado y commiteado en ${branch}, pero la certificación reportó problemas (${certifyProblems.join(" · ")}). La rama queda SIN mergear para diagnóstico.`,
    );
    return;
  }
  const decision = await vscode.window.showInformationMessage(
    `Acktos Studio: '${pkgName}' instalado y certificado en la rama ${branch}.`,
    { modal: true, detail: `¿Mergear a ${base} ahora?\n\nPost-install pendiente (manual): ENABLED_PLUGINS + env vars/secrets que pida el paquete.` },
    `Merge a ${base}`,
    "Dejar la rama",
  );
  if (decision !== `Merge a ${base}`) {
    return;
  }
  try {
    await gitOrThrow(["checkout", base], root, () => void 0);
    await gitOrThrow(["merge", "--no-ff", branch, "-m", `merge ${branch}: instala paquete ${pkgName}`], root, log);
  } catch (e) {
    // no dejar el árbol a mitad de merge (markers + MERGE_HEAD): abortar y
    // volver a la rama del install, que queda intacta para resolver a mano
    await git(["merge", "--abort"], root, log);
    await git(["checkout", branch], root, log);
    void vscode.window.showErrorMessage(
      `Acktos Studio: merge falló — ${e instanceof Error ? e.message : e}. Aborté el merge; la rama ${branch} queda intacta (probablemente conflicto con cambios locales de ${base} — resolvé a mano).`,
    );
    return;
  }
  const push = await vscode.window.showInformationMessage(
    `Acktos Studio: mergeado a ${base}.`,
    "Push origin",
    "Listo",
  );
  if (push === "Push origin") {
    try {
      await gitOrThrow(["push", "origin", base], root, log);
      void vscode.window.showInformationMessage(`Acktos Studio: ${base} pusheado a origin.`);
    } catch (e) {
      void vscode.window.showErrorMessage(`Acktos Studio: push falló — ${e instanceof Error ? e.message : e}`);
    }
  }
}
