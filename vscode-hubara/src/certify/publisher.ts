import * as vscode from "vscode";
import { BridgeHub } from "../bridge/pythonBridge";

/**
 * Ejecuta la publicación con las APIs NATIVAS de VS Code — `vscode.git` (branch/
 * commit/push) + `vscode.authentication` (token de GitHub del login que YA hiciste
 * en VS Code) → PR por REST. Así se cae la dependencia de `gh` en el runtime.
 *
 * La DECISIÓN de qué publicar (rama, paths quirúrgicos, mensaje, PR body) la calcula
 * `sdk/production.py:plan_publication` y llega como `PublishPlan` — acá solo se ejecuta.
 * Si el git nativo no está disponible, `unavailable:true` deja que el caller caiga a `gh`.
 */

export interface PublishPlan {
  ok: boolean;
  errors: string[];
  repo_root: string | null;
  on_default: boolean;
  branch: string | null;
  base: string;
  paths: string[];
  title: string | null;
  body: string | null;
  has_changes: boolean;
}

export interface PublishOutcome {
  ok: boolean;
  branch?: string;
  prUrl?: string;
  errors: string[];
  /** true si el git nativo no estaba disponible → el caller puede caer a `gh` (headless). */
  unavailable?: boolean;
}

// --- tipos estructurales MÍNIMOS de la Git Extension API (vscode.git) — SOLO los
//     miembros que este archivo usa en runtime (evita vendorar el git.d.ts entero). ---
interface GitRemote {
  name: string;
  fetchUrl?: string;
  pushUrl?: string;
}
interface GitRepositoryState {
  remotes: GitRemote[];
}
interface GitRepository {
  rootUri: vscode.Uri;
  state: GitRepositoryState;
  createBranch(name: string, checkout: boolean, ref?: string): Promise<void>;
  deleteBranch(name: string, force?: boolean): Promise<void>;
  add(resources: string[]): Promise<void>;
  commit(message: string): Promise<void>;
  push(remoteName?: string, branchName?: string, setUpstream?: boolean): Promise<void>;
}
interface GitAPI {
  repositories: GitRepository[];
  getRepository(uri: vscode.Uri): GitRepository | null;
  openRepository?(uri: vscode.Uri): Promise<GitRepository | null>;
}
interface GitExtensionExports {
  getAPI(version: 1): GitAPI;
}

async function resolveRepository(repoRoot: string): Promise<GitRepository | null | "unavailable"> {
  const gitExt = vscode.extensions.getExtension<GitExtensionExports>("vscode.git");
  if (!gitExt) {
    return "unavailable";
  }
  const exports = gitExt.isActive ? gitExt.exports : await gitExt.activate();
  const api = exports.getAPI(1);
  const uri = vscode.Uri.file(repoRoot);
  let repo = api.getRepository(uri) ?? api.repositories.find((r) => r.rootUri.fsPath === repoRoot) ?? null;
  if (!repo && api.openRepository) {
    repo = await api.openRepository(uri);
  }
  return repo;
}

export async function publishNatively(plan: PublishPlan, log: (line: string) => void): Promise<PublishOutcome> {
  if (!plan.ok || !plan.repo_root || !plan.branch) {
    return { ok: false, errors: plan.errors.length ? plan.errors : ["el plan de publicación es inválido"] };
  }
  const repo = await resolveRepository(plan.repo_root);
  if (repo === "unavailable") {
    return { ok: false, unavailable: true, errors: ["la extensión Git de VS Code (vscode.git) no está disponible"] };
  }
  if (!repo) {
    return { ok: false, unavailable: true, errors: [`VS Code no tiene abierto el repo git en ${plan.repo_root}`] };
  }

  const repoUri = vscode.Uri.file(plan.repo_root);
  try {
    if (plan.on_default) {
      log(`◍ creando rama ${plan.branch}…`);
      try {
        await repo.createBranch(plan.branch, true);
      } catch {
        // ya existía (de un publish anterior) → paridad con `git checkout -B` del camino
        // headless: la rama se RESETEA al HEAD actual (la default), no se reusa su tip
        // stale (un checkout plano commitearía encima de un commit abandonado, o git lo
        // rechazaría por el production.yaml recién escrito).
        await repo.deleteBranch(plan.branch, true);
        await repo.createBranch(plan.branch, true);
      }
    }

    if (plan.has_changes) {
      const abs = plan.paths.map((p) => vscode.Uri.joinPath(repoUri, p).fsPath);
      log(`◍ git add + commit (${plan.paths.join(", ")})…`);
      await repo.add(abs);
      try {
        await repo.commit(`${plan.title}\n\n${plan.body}`);
      } catch (e) {
        if (!/nothing to commit|no changes/i.test(String(e))) {
          throw e;
        }
        log("  · sin cambios que commitear (el wiring ya estaba desplegado)");
      }
    } else {
      log("◍ sin cambios en el wiring (ya estaba desplegado)");
    }

    log(`◍ push origin ${plan.branch}…`);
    await repo.push("origin", plan.branch, true);

    // El PR es parte del contrato ("crea rama + commit + push + PR") — si falla, el
    // resultado es un ERROR visible, no un verde engañoso. La rama ya quedó pusheada;
    // el error lo dice para que el operador pueda abrir el PR a mano.
    const pr = await ensurePullRequest(repo, plan, log);
    if (pr.error) {
      return { ok: false, branch: plan.branch, errors: [pr.error] };
    }
    return { ok: true, branch: plan.branch, prUrl: pr.url, errors: [] };
  } catch (e) {
    return { ok: false, branch: plan.branch, errors: [e instanceof Error ? e.message : String(e)] };
  }
}

/** Crea (o encuentra) el PR usando el token de la sesión de GitHub de VS Code — sin `gh`.
 * Devuelve `{url}` o `{error}` — nunca falla en silencio. */
async function ensurePullRequest(
  repo: GitRepository,
  plan: PublishPlan,
  log: (line: string) => void,
): Promise<{ url?: string; error?: string }> {
  const origin = repo.state.remotes.find((r) => r.name === "origin") ?? repo.state.remotes[0];
  const remoteUrl = origin?.pushUrl ?? origin?.fetchUrl;
  const slug = remoteUrl ? parseGithubSlug(remoteUrl) : null;
  if (!slug) {
    return { error: `no pude derivar owner/repo del remoto (${remoteUrl ?? "sin remoto"}) — la rama quedó pusheada; abrí el PR a mano` };
  }
  const session = await vscode.authentication.getSession("github", ["repo"], { createIfNone: true });
  const headers: Record<string, string> = {
    Authorization: `Bearer ${session.accessToken}`,
    Accept: "application/vnd.github+json",
    "Content-Type": "application/json",
    "User-Agent": "acktos-studio",
  };
  log(`◍ abriendo PR en ${slug.owner}/${slug.repo} (${plan.branch} → ${plan.base})…`);
  const res = await fetch(`https://api.github.com/repos/${slug.owner}/${slug.repo}/pulls`, {
    method: "POST",
    headers,
    body: JSON.stringify({ title: plan.title, head: plan.branch, base: plan.base, body: plan.body }),
  });
  if (res.status === 201) {
    return { url: ((await res.json()) as { html_url: string }).html_url };
  }
  // ya existía un PR para esa rama → devolvé el existente
  const existing = await fetch(
    `https://api.github.com/repos/${slug.owner}/${slug.repo}/pulls?head=${slug.owner}:${plan.branch}&state=open`,
    { headers },
  );
  if (existing.ok) {
    const arr = (await existing.json()) as { html_url: string }[];
    if (arr.length > 0) {
      return { url: arr[0].html_url };
    }
  }
  const detail = await res.text().then((t) => t.slice(0, 200)).catch(() => "");
  return { error: `el PR no se pudo crear (HTTP ${res.status}${detail ? ` — ${detail}` : ""}) — la rama ${plan.branch} quedó pusheada; abrí el PR a mano` };
}

function parseGithubSlug(remoteUrl: string): { owner: string; repo: string } | null {
  // git@github.com:owner/repo.git  ·  https://github.com/owner/repo(.git)
  const m = remoteUrl.match(/github\.com[:/]([^/]+)\/(.+?)(?:\.git)?\/?$/);
  return m ? { owner: m[1], repo: m[2] } : null;
}

/**
 * El flujo COMPLETO de publicación, compartido por "Guardar & certificar" y el comando
 * "Publish Production": pide el plan sin efectos a Python, lo ejecuta con las APIs
 * nativas de VS Code y, SOLO si el git nativo no está disponible, cae al camino
 * headless `/api/publish` (gh). Una sola definición para que ambos disparadores
 * publiquen idéntico.
 */
export async function publishWithFallback(
  bridges: BridgeHub,
  onPhase: (line: string) => void,
): Promise<PublishOutcome> {
  const planRes = await bridges.get("graphagents").request({ method: "GET", path: "/api/publish-plan" });
  const plan = planRes.payload as PublishPlan;
  if (!plan.ok) {
    return { ok: false, errors: plan.errors?.length ? plan.errors : [`no pude armar el plan (status ${planRes.status})`] };
  }
  const outcome = await publishNatively(plan, onPhase);
  if (!outcome.unavailable) {
    return outcome;
  }
  onPhase("Git nativo no disponible — usando gh (headless)…");
  const pub = await bridges
    .get("graphagents")
    .request({ method: "POST", path: "/api/publish", body: { push: true, pr: true } });
  const p = pub.payload as { ok?: boolean; errors?: string[]; branch?: string; pr_url?: string };
  return {
    ok: p.ok === true,
    branch: p.branch,
    prUrl: p.pr_url,
    errors: p.ok ? [] : (p.errors ?? [`publish falló (status ${pub.status})`]),
  };
}
