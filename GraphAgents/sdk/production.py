"""El snapshot de PRODUCCIÓN — el "save" del explorer.

"Guardar producción" = bendecir el cableado ACTUAL como el estado final en acción.
No es otra fuente de verdad: los manifests siguen mandando (git + cert + TDD); el
snapshot solo REGISTRA qué wiring fue bendecido y permite detectar drift (`dirty`).

- `save_production` corre los checks del SISTEMA COMPLETO y se rehúsa si algo está
  roto — no se bendice producción rota (L-19: ninguna etiqueta verde sin check).
- El `fingerprint` es determinista y captura SOLO el wiring (edges + node ids):
  cualquier connect/disconnect posterior deja el snapshot `dirty`; un cambio de
  cert/descripción no (eso lo gobiernan los checks, no el save).
- El snapshot vive en `production.yaml` en la RAÍZ del subsistema (viaja en git, pero
  FUERA de `manifests/` — el CLI `check` y los tests globean `manifests/*.yaml` y lo
  levantarían como manifest roto).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

SNAPSHOT_NAME = "production.yaml"


def _snapshot_path(ga_root: Path) -> Path:
    return Path(ga_root) / SNAPSHOT_NAME


def graph_fingerprint(graph: dict) -> str:
    """sha256 determinista del WIRING: aristas canónicas + ids de nodos. Los campos
    computados de los nodos (cert, descripciones) quedan afuera a propósito."""
    edges = sorted(
        (json.dumps(e, sort_keys=True, ensure_ascii=False) for e in graph["edges"]),
    )
    node_ids = sorted(n["id"] for n in graph["nodes"])
    blob = json.dumps({"edges": edges, "nodes": node_ids}, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def save_production(ga_root: Path) -> dict:
    """Valida TODO el sistema y, si está sano, escribe el snapshot. `{ok, errors, snapshot}`."""
    from sdk.graph import build_graph
    from sdk.inspect import system_checks

    ga_root = Path(ga_root)
    graph = build_graph(ga_root)
    checks = system_checks(graph, ga_root)
    broken = sorted(
        k for k, v in {**checks.get("nodes", {}), **checks.get("edges", {})}.items() if not v.get("ok")
    )
    if broken:
        return {"ok": False, "snapshot": None,
                "errors": [f"no se bendice producción rota — arreglá antes de guardar: {b}" for b in broken]}
    pods = sorted(p.stem.removesuffix(".taskgraph") for p in (ga_root / "manifests").glob("*.taskgraph.yaml"))
    snapshot = {
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fingerprint": graph_fingerprint(graph),
        "nodes": len(graph["nodes"]),
        "edges": len(graph["edges"]),
        "pods": pods,
    }
    header = ("# Snapshot de PRODUCCIÓN del explorer — el wiring bendecido como final.\n"
              "# Lo escribe el botón 'guardar producción' (POST /api/save) tras pasar TODOS los\n"
              "# checks. No es un manifest (los globs no lo levantan); el fingerprint detecta\n"
              "# drift: si el grafo actual no matchea, el explorer marca 'cambios sin guardar'.\n")
    _snapshot_path(ga_root).write_text(
        header + yaml.safe_dump(snapshot, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return {"ok": True, "errors": [], "snapshot": snapshot}


def _run(cmd: list[str], cwd: Path | None = None) -> tuple[int, str]:
    """Corre un comando y devuelve (returncode, salida combinada). FileNotFoundError
    (binario ausente — p. ej. git/gh en el container slim) degrada a un error legible."""
    import subprocess

    try:
        p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True)
    except FileNotFoundError:
        return 127, f"'{cmd[0]}' no está disponible en este runtime"
    return p.returncode, (p.stdout + p.stderr).strip()


def plan_publication(ga_root: Path) -> dict:
    """El PLAN de publicación SIN efectos: qué rama, qué paths, qué mensaje/PR — para que un
    frontend (Acktos Studio) lo EJECUTE con las APIs nativas de VS Code (git + GitHub) en vez
    de shellear `gh`. No muta: NO crea rama ni commitea (eso lo hace quien ejecuta el plan).

    `publish_production` sigue siendo el camino headless (git/gh por subprocess) — la verdad
    de QUÉ se publica (guard del snapshot, paths quirúrgicos, mensaje) vive una sola vez acá.
    """
    ga_root = Path(ga_root)
    out: dict = {"ok": False, "errors": [], "repo_root": None, "on_default": False,
                 "branch": None, "base": "main", "paths": [], "title": None, "body": None,
                 "fingerprint": None, "has_changes": False}

    st = production_status(ga_root)
    if not st["saved"] or st["dirty"]:
        out["errors"].append("guardá producción primero (el snapshot no está al día) — "
                             "publicar despliega SOLO lo bendecido")
        return out
    snap = st["snapshot"]
    fp = str(snap.get("fingerprint", ""))
    fp8 = fp[:8]
    out["fingerprint"] = fp

    code, top = _run(["git", "-C", str(ga_root), "rev-parse", "--show-toplevel"])
    if code != 0:
        out["errors"].append(f"no estoy dentro de un repo git — publicá desde el checkout local ({top})")
        return out
    repo = Path(top)
    out["repo_root"] = str(repo)
    git = lambda *a: _run(["git", "-C", str(repo), *a])  # noqa: E731

    code, branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if code != 0:
        out["errors"].append(f"no pude leer la rama actual: {branch}")
        return out
    on_default = branch in ("main", "master")
    out["on_default"] = on_default
    out["branch"] = f"graphagents/production-{fp8}" if on_default else branch

    # base del PR = la rama default del remoto (origin/HEAD); sin remoto, la actual/main
    code, origin_head = git("rev-parse", "--abbrev-ref", "origin/HEAD")
    if code == 0 and origin_head.startswith("origin/"):
        out["base"] = origin_head.split("/", 1)[1]
    else:
        out["base"] = branch if on_default else "main"

    ga_rel = ga_root.resolve().relative_to(repo.resolve())
    manifests_rel = str(ga_rel / "manifests")
    snap_rel = str(ga_rel / SNAPSHOT_NAME)
    out["paths"] = [manifests_rel, snap_rel]
    _, porcelain = git("status", "--porcelain", "--", manifests_rel, snap_rel)
    out["has_changes"] = bool(porcelain.strip())

    out["title"] = (f"feat(graphagents): producción {fp8} — {len(snap.get('pods', []))} pod(s), "
                    f"{snap.get('edges', 0)} relaciones")
    out["body"] = (f"Despliegue del wiring bendecido desde Acktos Studio (GraphAgents).\n\n"
                   f"- pods: {', '.join(snap.get('pods', []))}\n"
                   f"- nodos: {snap.get('nodes')} · relaciones: {snap.get('edges')}\n"
                   f"- fingerprint: `{fp}`\n"
                   f"- bendecido: {snap.get('saved_at')} (checks del sistema en verde)\n")
    out["ok"] = True
    return out


def publish_production(ga_root: Path, *, push: bool = True, pr: bool = True) -> dict:
    """El DESPLIEGUE del wiring bendecido: commit quirúrgico (+push +PR con `gh`).

    - Exige el snapshot AL DÍA (save previo = todos los checks verdes). Con drift se
      rehúsa: primero bendecís, después desplegás.
    - El commit toma SOLO `manifests/` + `production.yaml` — nunca arrastra el resto
      del working tree del operador.
    - En la rama default (main/master) crea `graphagents/production-<fp8>`; en una
      rama de trabajo commitea ahí mismo.
    - Cada capa (commit / push / pr) reporta su paso; una falla posterior no oculta
      lo ya hecho (steps honestos + ok global solo si no hubo errores).
    """
    ga_root = Path(ga_root)
    out: dict = {"ok": False, "steps": [], "branch": None, "commit": None, "pr_url": None, "errors": []}

    st = production_status(ga_root)
    if not st["saved"] or st["dirty"]:
        out["errors"].append("guardá producción primero (el snapshot no está al día) — "
                             "publicar despliega SOLO lo bendecido")
        return out
    snap = st["snapshot"]
    fp8 = str(snap.get("fingerprint", ""))[:8]

    code, top = _run(["git", "-C", str(ga_root), "rev-parse", "--show-toplevel"])
    if code != 0:
        out["errors"].append(f"no estoy dentro de un repo git — publicá desde el checkout local ({top})")
        return out
    repo = Path(top)
    git = lambda *a: _run(["git", "-C", str(repo), *a])  # noqa: E731

    ga_rel = ga_root.resolve().relative_to(repo.resolve())
    paths = [str(ga_rel / "manifests"), str(ga_rel / SNAPSHOT_NAME)]

    code, branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if code != 0:
        out["errors"].append(f"no pude leer la rama actual: {branch}")
        return out
    if branch in ("main", "master"):
        branch = f"graphagents/production-{fp8}"
        code, msg = git("checkout", "-B", branch)
        if code != 0:
            out["errors"].append(f"no pude crear la rama '{branch}': {msg}")
            return out
        out["steps"].append({"step": "branch", "ok": True, "detail": f"rama nueva '{branch}' (estabas en la default)"})
    out["branch"] = branch

    title = (f"feat(graphagents): producción {fp8} — {len(snap.get('pods', []))} pod(s), "
             f"{snap.get('edges', 0)} relaciones")
    code, msg = git("add", "--", *paths)
    if code != 0:
        out["errors"].append(f"git add falló: {msg}")
        return out
    _, staged = git("diff", "--cached", "--name-only")
    if staged:
        code, msg = git("commit", "-m", title, "-m",
                        "Wiring bendecido desde el explorer (guardar producción → publicar). "
                        f"Snapshot: {snap.get('saved_at')} · fingerprint {snap.get('fingerprint')}.")
        if code != 0:
            out["errors"].append(f"git commit falló: {msg}")
            return out
        _, sha = git("rev-parse", "--short", "HEAD")
        out["commit"] = sha
        out["steps"].append({"step": "commit", "ok": True, "detail": f"{sha} · {len(staged.splitlines())} archivo(s)"})
    else:
        _, sha = git("rev-parse", "--short", "HEAD")
        out["commit"] = sha
        out["steps"].append({"step": "commit", "ok": True,
                             "detail": "sin cambios que commitear (el wiring ya estaba desplegado)"})

    if push:
        code, msg = git("push", "-u", "origin", branch)
        ok = code == 0
        out["steps"].append({"step": "push", "ok": ok, "detail": msg.splitlines()[-1] if msg else "pusheado"})
        if not ok:
            out["errors"].append(f"git push falló (¿remote/red?): {msg}")
            return out

    if pr:
        body = (f"Despliegue del wiring bendecido desde el explorer de GraphAgents.\n\n"
                f"- pods: {', '.join(snap.get('pods', []))}\n"
                f"- nodos: {snap.get('nodes')} · relaciones: {snap.get('edges')}\n"
                f"- fingerprint: `{snap.get('fingerprint')}`\n"
                f"- bendecido: {snap.get('saved_at')} (checks del sistema en verde)\n")
        code, msg = _run(["gh", "pr", "create", "--head", branch, "--title", title, "--body", body], cwd=repo)
        if code == 0:
            url = next((ln for ln in msg.splitlines() if ln.startswith("http")), msg)
            out["pr_url"] = url
            out["steps"].append({"step": "pr", "ok": True, "detail": url})
        elif "already exists" in msg:
            code2, url = _run(["gh", "pr", "view", branch, "--json", "url", "-q", ".url"], cwd=repo)
            out["pr_url"] = url if code2 == 0 else None
            out["steps"].append({"step": "pr", "ok": True, "detail": f"ya existía un PR para la rama: {out['pr_url'] or msg}"})
        else:
            out["errors"].append(f"gh pr create falló: {msg}")
            out["steps"].append({"step": "pr", "ok": False, "detail": msg})
            return out

    out["ok"] = not out["errors"]
    return out


def production_status(ga_root: Path) -> dict:
    """`{saved, dirty, snapshot}` — lo que el header del explorer pinta. Sin snapshot:
    saved=False y dirty=True (nunca hubo bendición; todo está 'sin guardar')."""
    from sdk.graph import build_graph

    ga_root = Path(ga_root)
    p = _snapshot_path(ga_root)
    if not p.exists():
        return {"saved": False, "dirty": True, "snapshot": None}
    try:
        snapshot = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 — snapshot ilegible = como si no existiera (honesto)
        return {"saved": False, "dirty": True, "snapshot": None}
    current = graph_fingerprint(build_graph(ga_root))
    return {"saved": True, "dirty": current != snapshot.get("fingerprint"), "snapshot": snapshot}
