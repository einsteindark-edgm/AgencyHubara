import * as vscode from "vscode";

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
  fingerprint: string | null;
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

// --- tipos estructurales MÍNIMOS de la Git Extension API (vscode.git) — evita vendorar
//     el git.d.ts entero; el objeto real en runtime expone estos miembros. ---
interface GitRemote {
  name: string;
  fetchUrl?: string;
  pushUrl?: string;
}
interface GitRepositoryState {
  HEAD?: { name?: string };
  remotes: GitRemote[];
}
interface GitRepository {
  rootUri: vscode.Uri;
  state: GitRepositoryState;
  createBranch(name: string, checkout: boolean, ref?: string): Promise<void>;
  checkout(treeish: string): Promise<void>;
  add(resources: string[]): Promise<void>;
  commit(message: string, opts?: { all?: boolean }): Promise<void>;
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
        await repo.checkout(plan.branch); // ya existía → cambiarse a ella
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

    const prUrl = await ensurePullRequest(repo, plan, log);
    return { ok: true, branch: plan.branch, prUrl, errors: [] };
  } catch (e) {
    return { ok: false, branch: plan.branch, errors: [e instanceof Error ? e.message : String(e)] };
  }
}

/** Crea (o encuentra) el PR usando el token de la sesión de GitHub de VS Code — sin `gh`. */
async function ensurePullRequest(
  repo: GitRepository,
  plan: PublishPlan,
  log: (line: string) => void,
): Promise<string | undefined> {
  const origin = repo.state.remotes.find((r) => r.name === "origin") ?? repo.state.remotes[0];
  const remoteUrl = origin?.pushUrl ?? origin?.fetchUrl;
  const slug = remoteUrl ? parseGithubSlug(remoteUrl) : null;
  if (!slug) {
    log("  ⚠ no pude derivar owner/repo del remoto — PR omitido (la rama quedó pusheada)");
    return undefined;
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
    return ((await res.json()) as { html_url: string }).html_url;
  }
  // ya existía un PR para esa rama → devolvé el existente
  const existing = await fetch(
    `https://api.github.com/repos/${slug.owner}/${slug.repo}/pulls?head=${slug.owner}:${plan.branch}&state=open`,
    { headers },
  );
  if (existing.ok) {
    const arr = (await existing.json()) as { html_url: string }[];
    if (arr.length > 0) {
      return arr[0].html_url;
    }
  }
  log(`  ⚠ no se pudo crear el PR (status ${res.status}) — la rama quedó pusheada, abrilo a mano`);
  return undefined;
}

function parseGithubSlug(remoteUrl: string): { owner: string; repo: string } | null {
  // git@github.com:owner/repo.git  ·  https://github.com/owner/repo(.git)
  const m = remoteUrl.match(/github\.com[:/]([^/]+)\/(.+?)(?:\.git)?\/?$/);
  return m ? { owner: m[1], repo: m[2] } : null;
}
