#!/usr/bin/env python3
"""forge — clonador de clientes del motor hubara (VINCENZO_SPLIT_PLAN.md §5).

Produce el repo de un cliente nuevo a partir del repo madre: exporta un snapshot
limpio (sin historia git — los IDs del cliente Hubara no viajan a terceros),
aplica el manifest de renombres (lo que colisiona a nivel cuenta AWS + identidad
de marca), instala el overlay de personalidad del agente, borra los datos del
cliente Hubara, verifica residuales (el ratchet) y deja git inicializado.

Solo stdlib + PyYAML — corre con `python3`, sin uv (misma filosofía que
infra/whatsapp-provisioning/whatsapp_provision.py).

  python3 forge/forge.py init    <slug>
  python3 forge/forge.py plan    <slug>
  python3 forge/forge.py apply   <slug> --dest <path> [--allow-todos]
  python3 forge/forge.py verify  <dest> --client <slug>
  python3 forge/forge.py publish <dest> --client <slug>   # imprime comandos, no ejecuta
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("forge necesita PyYAML: python3 -m pip install pyyaml")

ROOT = Path(__file__).resolve().parent  # forge/ (raíz del repo madre)
REPO = ROOT.parent  # repo madre
CLIENTS = ROOT / "clients"

# `mba` (Meta Business Agent) viaja habilitado: su agente se autora por tenant
# (overlay `mba_sales` del bundle) y el connector queda fail-closed (503) hasta
# que el tenant ponga HUBARA_MBA_API_KEY en SSM — no rompe nada mientras tanto.
ENABLED_PLUGINS_DEFAULT = (
    "ads,agents_admin,catalog,chats,eta,marketing,mba,order_sentinel,orders,reengagement,system_map"
)
#: La App Operador de hubara (Google Play/Firebase): ningún clon la reusa.
HUBARA_ANDROID_APP_ID = "com.acktos.operator"
#: El concepto de la tienda para el motor de decisiones, en el bundle del cliente.
DOMAIN_FILE = "domain.yaml"
PHONE_CC = {"CO": "57", "MX": "52", "AR": "54", "US": "1"}
# Palabra alrededor de un match de "hubara" — incluye '/' y '.' para que los
# paths (hubara_agency/docs/...) cuenten como una sola palabra clasificable.
WORD_RE = re.compile(r"[A-Za-z0-9_\-\./]*[Hh]ubara[A-Za-z0-9_\-\./]*")


class ForgeError(RuntimeError):
    """Error de forge con mensaje accionable para el operador."""


def main_repo_root(src: Path) -> Path:
    """El repo a PROTEGER. Si forge corre desde un worktree
    (<repo>/.claude/worktrees/<x>), el checkout productivo es <repo> — un dest
    "afuera del worktree" podría seguir estando adentro de hubara."""
    s = str(Path(src).resolve())
    marker = "/.claude/worktrees/"
    return Path(s.split(marker)[0]) if marker in s else Path(s)


# ── Carga y vars ──────────────────────────────────────────────────────────────


def load_manifest(path: Path | None = None) -> dict:
    with open(path or ROOT / "manifest.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_client(client_dir: Path) -> dict:
    f = Path(client_dir) / "client.yaml"
    if not f.exists():
        raise ForgeError(f"no existe {f} — corré `forge init <slug>` primero")
    with open(f, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _guard_not_hubara(kind: str, value: str) -> None:
    """CRÍTICO: forge clona DESDE hubara, jamás lo apunta. Ningún identificador
    del cliente puede colisionar con los del proyecto productivo."""
    v = value.lower().strip("/")
    if v == "hubara" or "agencyhubara" in v:
        raise ForgeError(
            f"{kind} {value!r} apunta al proyecto productivo hubara — prohibido: "
            "elegí identificadores propios del cliente nuevo"
        )


def render_vars(client: dict) -> dict:
    slug = client["slug"]
    if not re.fullmatch(r"[a-z][a-z0-9_]*", slug):
        raise ForgeError(f"slug inválido {slug!r}: minúsculas/dígitos/_ (va en SSM, tags, tfvars)")
    _guard_not_hubara("slug", slug)
    repo = client.get("repo") or f"TODO-owner/Agency{slug.title()}"
    if repo.lower().endswith("/agencyhubara"):
        raise ForgeError(
            f"repo {repo!r} es el repo madre AgencyHubara — el clon necesita repo propio"
        )
    repo_owner, _, repo_name = repo.partition("/")
    aws = client.get("aws") or {}
    business = client.get("business") or {}
    domains = business.get("domains") or []
    prefix = aws.get("resource_prefix") or f"agency{slug}"
    ssm_prefix = (aws.get("ssm_prefix") or f"/{slug}").rstrip("/")
    _guard_not_hubara("aws.resource_prefix", prefix)
    _guard_not_hubara("aws.ssm_prefix", ssm_prefix)
    # URL del backend del cliente (EIP sslip o dominio propio). Si aún no
    # existe (nace en S8), queda el placeholder y el runbook lo pide después.
    api_url = (client.get("api_url") or "https://TODO-EIP.sslip.io").rstrip("/")
    if "hubara" in api_url.lower() or "98-88-237-207" in api_url:
        raise ForgeError(f"api_url {api_url!r} apunta al backend productivo de hubara — prohibido")
    # App Operador (android_operator/): applicationId propio — es único en
    # Google Play y en el proyecto Firebase del cliente.
    android_app_id = client.get("android_app_id") or f"com.acktos.{slug}"
    if not re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+", android_app_id):
        raise ForgeError(f"android_app_id inválido {android_app_id!r}: p.ej. com.acktos.{slug}")
    if android_app_id == HUBARA_ANDROID_APP_ID:
        raise ForgeError(
            f"android_app_id {android_app_id!r} es la App Operador de hubara en Google Play — "
            "el clon publica la suya"
        )
    return {
        "slug": slug,
        "company": client.get("company") or slug.title(),
        "android_app_id": android_app_id,
        "repo": repo,
        "repo_owner": repo_owner,
        "repo_name": repo_name,
        "prefix": prefix,
        "ssm_prefix": ssm_prefix,
        "api_url": api_url,
        "api_host": api_url.split("://", 1)[-1],
        "region": aws.get("region") or "us-east-1",
        "image": repo_name.lower(),
        "ref_prefix": slug[:3].upper(),
        "primary_domain": domains[0] if domains else "TODO-BRAND.example.com",
        "instagram": (business.get("instagram") or "TODO-instagram").strip("@/ "),
        "country": business.get("country") or "CO",
        "currency": business.get("currency") or "COP",
        "phone_cc": PHONE_CC.get(business.get("country") or "CO", "57"),
        "enabled_plugins": client.get("enabled_plugins") or ENABLED_PLUGINS_DEFAULT,
        "engine_sha": "",  # se completa en apply
    }


# ── Política comercial (client.yaml → commerce → Terraform `store`) ─────────
#
# Lo que el bot le dice al cliente sobre envío y pagos, y cómo reconoce los
# códigos de su catálogo. El clon la escribe en tenants.auto.tfvars → SSM →
# .env (modules/store-config); sin ella el motor usaría la de la tienda madre.
#: campo → (tipo, validación, ejemplo). Orden = orden en el tfvars.
COMMERCE_FIELDS: dict[str, tuple[str, str, str]] = {
    "payment_nequi_number": ("str", r"(\d{7,15})?", "3001234567 · vacío = sin pago anticipado"),
    "payment_link_surcharge_local": ("str", r"\d+(,\d+)?%", "1,5%"),
    "payment_link_surcharge_other": ("str", r"\d+(,\d+)?%", "2,69%"),
    "shipping_local_zone": ("str", r".+", "Bogotá y municipios cercanos"),
    "shipping_local_city": ("str", r".+", "Bogotá"),
    "shipping_rate_local_cop": ("int", "", "7900"),
    "shipping_rate_national_cop": ("int", "", "16940"),
    "cash_on_delivery_min_cop": ("int", "", "45000"),
    "sku_prefix": ("str", r"[A-Z0-9]{1,12}-", "HUB-"),
    "web_domain": ("str", r"[a-z0-9.-]+", "tienda.com"),
    "catalog_collections": ("list", "", '["home_banner"]'),
}
#: Opcionales: sin valor manda el default del motor (no es dato de otra tienda).
COMMERCE_OPTIONAL = {"payment_nequi_number", "web_domain", "catalog_collections"}


def _pending(value: object) -> bool:
    return value is None or (isinstance(value, str) and "TODO" in value)


def commerce_values(client: dict, vars_: dict, allow_todos: bool) -> dict:
    """La política comercial validada; lo pendiente bloquea un clon real."""
    raw = dict(client.get("commerce") or {})
    if _pending(raw.get("web_domain")) and not vars_["primary_domain"].startswith("TODO"):
        raw["web_domain"] = vars_["primary_domain"]
    out, pending, bad = {}, [], []
    for name, (kind, pattern, example) in COMMERCE_FIELDS.items():
        value = raw.get(name)
        if _pending(value):
            if name not in COMMERCE_OPTIONAL:
                pending.append(f"{name} (p. ej. {example})")
            continue
        if kind == "int":
            ok = isinstance(value, int) and not isinstance(value, bool) and value > 0
        elif kind == "list":
            ok = isinstance(value, list) and bool(value) and all(isinstance(v, str) and v.strip() for v in value)
        else:
            ok = isinstance(value, str) and re.fullmatch(pattern, value.strip()) is not None
        if not ok:
            bad.append(f"{name}={value!r} (p. ej. {example})")
            continue
        if value == "":  # llave Nequi vacía a propósito = sin pago anticipado
            continue
        out[name] = value.strip() if isinstance(value, str) else value
    if bad:
        raise ForgeError("client.yaml → commerce mal escrito: " + "; ".join(bad))
    if pending and not allow_todos:
        raise ForgeError(
            "client.yaml → commerce incompleto (la política comercial de la tienda; usa "
            "--allow-todos solo para un clon de prueba): " + ", ".join(pending)
        )
    return out


def store_block(values: dict) -> str:
    """El bloque `store` del tfvars de platform (indentado dentro del tenant)."""
    if not values:
        return "    # store = {}  # política comercial pendiente (clon de prueba): manda el default del motor"
    width = max(len(k) for k in values)
    lines = ["    store = {"]
    for name, value in values.items():
        rendered = json.dumps(value, ensure_ascii=False) if not isinstance(value, int) else str(value)
        lines.append(f"      {name.ljust(width)} = {rendered}")
    lines.append("    }")
    return "\n".join(lines)


def _render(text: str, vars_: dict) -> str:
    for k, v in vars_.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text


def _glob_re(pattern: str) -> re.Pattern:
    out, i = "", 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if pattern[i : i + 2] == "**":
                out, i = out + ".*", i + 2
                if i < len(pattern) and pattern[i] == "/":
                    i += 1
            else:
                out, i = out + "[^/]*", i + 1
        elif c == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(c), i + 1
    return re.compile("^" + out + "$")


def _match_any(rel: str, patterns: list[str]) -> bool:
    return any(_glob_re(p).match(rel) for p in patterns)


def _walk_files(root: Path):
    for p in sorted(root.rglob("*")):
        if p.is_file() and ".git" not in p.parts:
            yield p


def _read_text(p: Path) -> str | None:
    try:
        return p.read_bytes().decode("utf-8")
    except (UnicodeDecodeError, OSError):
        return None  # binario o ilegible: fuera del alcance de texto


# ── Stages ────────────────────────────────────────────────────────────────────


def stage_export(src: Path, dest: Path) -> str:
    """git archive HEAD → dest (sin .git, sin historia). Devuelve el sha del motor."""
    if dest.exists() and any(dest.iterdir()):
        raise ForgeError(f"destino {dest} no está vacío — elegí otra carpeta o borrala")
    dest.mkdir(parents=True, exist_ok=True)
    archive = subprocess.Popen(
        ["git", "-C", str(src), "archive", "HEAD"], stdout=subprocess.PIPE
    )
    subprocess.run(["tar", "-x", "-C", str(dest)], stdin=archive.stdout, check=True)
    if archive.wait() != 0:
        raise ForgeError(f"git archive falló en {src}")
    sha = subprocess.run(
        ["git", "-C", str(src), "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return sha


def stage_prune(dest: Path, manifest: dict) -> list[str]:
    pruned = []
    for rel in manifest.get("copy_exclude", []) + manifest.get("deletes", []):
        p = dest / rel
        if p.is_dir():
            shutil.rmtree(p)
            pruned.append(rel)
        elif p.exists():
            p.unlink()
            pruned.append(rel)
    return pruned


def stage_templates(dest: Path, manifest: dict, vars_: dict) -> list[str]:
    written = []
    for target_tpl, tpl_name in (manifest.get("templates") or {}).items():
        target = dest / _render(target_tpl, vars_)
        content = _render((ROOT / "templates" / tpl_name).read_text(encoding="utf-8"), vars_)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(str(target.relative_to(dest)))
    return written


def overlay_agents(manifest: dict) -> dict[str, tuple[str, list[str]]]:
    """``{agente: (path_en_el_repo, archivos_requeridos)}`` del overlay.

    Una entrada de ``workspace_overlay.agents`` es un string (path; aplica el
    ``required`` común de los workspaces exoclaw) o un mapping ``{path,
    required}`` para agentes con otra estructura (el MBA: agent.yaml +
    skills/<title>.md).
    """
    ov = manifest["workspace_overlay"]
    out: dict[str, tuple[str, list[str]]] = {}
    for agent, spec in ov["agents"].items():
        if isinstance(spec, str):
            out[agent] = (spec, list(ov["required"]))
        else:
            out[agent] = (str(spec["path"]), list(spec.get("required") or ov["required"]))
    return out


def stage_overlay(
    dest: Path, client_dir: Path, manifest: dict, vars_: dict, allow_todos: bool
) -> dict:
    ov = manifest["workspace_overlay"]
    todo = manifest["scan"]["todo_marker"]
    agents = overlay_agents(manifest)
    installed, missing, todos = {}, [], []
    for agent, (ws_rel, required) in agents.items():
        bundle = Path(client_dir) / "workspace" / agent
        if not bundle.is_dir():
            missing.append(f"{agent}/ (carpeta completa)")
            continue
        for req in required:
            if not (bundle / req).is_file():
                missing.append(f"{agent}/{req}")
        for f in _walk_files(bundle):
            text = _read_text(f)
            if text and todo in text:
                todos.append(str(f.relative_to(client_dir)))
    domain = Path(client_dir) / DOMAIN_FILE
    if not domain.is_file():
        missing.append(f"{DOMAIN_FILE} (el concepto de la tienda para el motor de decisiones)")
    elif todo in (_read_text(domain) or ""):
        todos.append(DOMAIN_FILE)
    if missing:
        raise ForgeError(
            "overlay de workspace incompleto — faltan: " + ", ".join(sorted(missing))
        )
    if todos and not allow_todos:
        raise ForgeError(
            f"el bundle tiene {todo} sin resolver (usa --allow-todos para un clon de "
            "prueba): " + ", ".join(sorted(todos))
        )
    for agent, (ws_rel, _) in agents.items():
        bundle = Path(client_dir) / "workspace" / agent
        target = dest / ws_rel
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(bundle, target)
        catalog = target / "skills" / ov["catalog_skill_dirname"]
        if catalog.is_dir():
            catalog.rename(target / "skills" / f"{vars_['slug']}_catalog")
        installed[agent] = ws_rel
    return {"installed": installed, "todos": todos}


def stage_bundles(dest: Path, client_dir: Path, manifest: dict) -> dict:
    """Paquetes de decisión: viajan el del código y el que corre la tienda madre
    (la inteligencia del motor); cada uno recibe el `domain.yaml` del cliente
    (el concepto de la tienda). Las demás versiones no viajan."""
    db = manifest["decision_bundles"]
    root = dest / db["dir"]
    keep = {db["default"], db["store"]}
    missing = sorted(b for b in keep if not (root / b / "bundle.yaml").is_file())
    if missing:
        raise ForgeError(f"el motor no trae los paquetes {missing} (decision_bundles en manifest.yaml)")
    pruned = []
    for d in sorted(p for p in root.iterdir() if (p / "bundle.yaml").is_file()):
        if d.name not in keep:
            shutil.rmtree(d)
            pruned.append(d.name)
    domain = (Path(client_dir) / DOMAIN_FILE).read_text(encoding="utf-8")
    for b in sorted(keep):
        (root / b / "domain.yaml").write_text(domain, encoding="utf-8")
    return {"kept": sorted(keep), "store": db["store"], "pruned": pruned}


def _mask(text: str, tokens: list[str]) -> str:
    for i, tok in enumerate(tokens):
        text = text.replace(tok, f"\x00PRESERVE{i}\x00")
    return text


def _unmask(text: str, tokens: list[str]) -> str:
    for i, tok in enumerate(tokens):
        text = text.replace(f"\x00PRESERVE{i}\x00", tok)
    return text


def stage_replacements(dest: Path, manifest: dict, vars_: dict) -> dict:
    counts: dict[str, int] = {}
    preserve = manifest.get("preserve_tokens", [])
    rules = [
        (r["id"], r["files"], _render(r["from"], vars_), _render(r["to"], vars_))
        for r in manifest["replacements"]
    ]
    for p in _walk_files(dest):
        rel = p.relative_to(dest).as_posix()
        text = None
        changed = False
        for rid, globs, frm, to in rules:
            if not _match_any(rel, globs):
                continue
            if text is None:
                text = _read_text(p)
                if text is None:
                    break
                text = _mask(text, preserve)
            if frm in text:
                text = text.replace(frm, to)
                counts[rid] = counts.get(rid, 0) + 1
                changed = True
        if text is not None and changed:
            p.write_text(_unmask(text, preserve), encoding="utf-8")
    return counts


def stage_renames(dest: Path, manifest: dict, vars_: dict) -> list[str]:
    done = []
    for r in manifest.get("renames", []):
        src = dest / _render(r["from"], vars_)
        if src.exists():
            target = dest / _render(r["to"], vars_)
            target.parent.mkdir(parents=True, exist_ok=True)
            src.rename(target)
            done.append(f"{r['from']} → {target.relative_to(dest)}")
    return done


def scan_residuals(dest: Path, manifest: dict, vars_: dict) -> dict:
    """El ratchet: tier 1 forbidden (IDs reales, global), tier 2 critical
    ("hubara" sin clasificar en scopes tenant-sensibles), tier 3 warn (resto)."""
    cfg = manifest["scan"]
    forbidden_lits = cfg.get("forbidden", [])
    forbidden_res = [re.compile(p) for p in cfg.get("forbidden_patterns", [])]
    pattern_allow = set(cfg.get("forbidden_pattern_allow", []))
    crit = cfg.get("critical_globs", [])
    crit_excl = cfg.get("critical_exclude_globs", [])
    allow = cfg.get("allow_tokens", [])
    forbidden, critical, warn = [], [], 0
    for p in _walk_files(Path(dest)):
        rel = p.relative_to(dest).as_posix()
        text = _read_text(p)
        if text is None:
            continue
        for lit in forbidden_lits:
            if lit in text:
                forbidden.append(f"{rel}: {lit}")
        for rx in forbidden_res:
            for m in rx.finditer(text):
                if m.group(0) not in pattern_allow:
                    forbidden.append(f"{rel}: {m.group(0)}")
        if "hubara" not in text.lower():
            continue
        in_critical = _match_any(rel, crit) and not _match_any(rel, crit_excl)
        for lineno, line in enumerate(text.splitlines(), 1):
            for word in WORD_RE.findall(line):
                if any(tok in word for tok in allow):
                    continue
                if in_critical:
                    critical.append(f"{rel}:{lineno}: {word}")
                else:
                    warn += 1
    return {"forbidden": sorted(set(forbidden)), "critical": critical, "warn_count": warn}


def stage_git_init(dest: Path, src: Path, vars_: dict) -> None:
    g = ["git", "-C", str(dest), "-c", "user.email=forge@local", "-c", "user.name=forge"]
    subprocess.run(g + ["init", "-q"], check=True)
    subprocess.run(g + ["add", "-A"], check=True)
    subprocess.run(
        g
        + [
            "commit",
            "-qm",
            f"chore: génesis {vars_['company']} desde hubara engine {vars_['engine_sha']}",
        ],
        check=True,
    )
    origin = subprocess.run(
        ["git", "-C", str(src), "remote", "get-url", "origin"], capture_output=True, text=True
    )
    subprocess.run(
        g + ["remote", "add", "hubara", origin.stdout.strip() or str(src)], check=True
    )


# ── Orquestación ──────────────────────────────────────────────────────────────


def run_apply(
    src: Path,
    dest: Path,
    client_dir: Path,
    manifest: dict,
    allow_todos: bool = False,
    with_gates: bool = False,
) -> dict:
    if with_gates:
        raise ForgeError("--with-gates llega en v2; corré los gates del clon a mano por ahora")
    src, dest, client_dir = Path(src), Path(dest), Path(client_dir)
    protected = main_repo_root(src)
    if dest.resolve().is_relative_to(protected.resolve()):
        raise ForgeError(
            f"dest {dest} está DENTRO del repo madre/productivo ({protected}) — "
            "el clon vive afuera, nunca mezclado con hubara"
        )
    vars_ = render_vars(load_client(client_dir))
    vars_["store_bundle"] = manifest["decision_bundles"]["store"]
    vars_["store_block"] = store_block(commerce_values(load_client(client_dir), vars_, allow_todos))
    dirty = subprocess.run(
        ["git", "-C", str(src), "status", "--porcelain"], capture_output=True, text=True
    ).stdout.strip()
    if dirty:
        print(
            "⚠ el repo madre tiene cambios sin commitear — el clon sale de HEAD "
            "(git archive), esos cambios NO viajan",
            file=sys.stderr,
        )
    vars_["engine_sha"] = stage_export(src, dest)
    report: dict = {"engine_sha": vars_["engine_sha"], "stages": {}}
    report["stages"]["pruned"] = stage_prune(dest, manifest)
    report["stages"]["overlay"] = stage_overlay(dest, client_dir, manifest, vars_, allow_todos)
    report["stages"]["bundles"] = stage_bundles(dest, client_dir, manifest)
    report["stages"]["templates"] = stage_templates(dest, manifest, vars_)
    report["stages"]["replacements"] = stage_replacements(dest, manifest, vars_)
    report["stages"]["renames"] = stage_renames(dest, manifest, vars_)
    report["scan"] = scan_residuals(dest, manifest, vars_)
    hard = report["scan"]["forbidden"] + report["scan"]["critical"]
    if hard:
        raise ForgeError(
            f"el clon NO está limpio ({len(hard)} residuales) — clasificalos en "
            f"manifest.yaml (replace/delete/allow). El clon queda en {dest} para "
            "inspección; borralo antes de reintentar:\n  " + "\n  ".join(hard[:200])
        )
    stage_git_init(dest, src, vars_)
    return report


def run_plan(src: Path, client_dir: Path, manifest: dict) -> dict:
    """Dry-run sobre el repo madre: qué archivos matchea cada regla hoy."""
    src = Path(src)
    vars_ = render_vars(load_client(client_dir))
    tracked = subprocess.run(
        ["git", "-C", str(src), "ls-files"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    rules = [
        (r["id"], r["files"], _render(r["from"], vars_)) for r in manifest["replacements"]
    ]
    counts = {rid: 0 for rid, _, _ in rules}
    hits: dict[str, list[str]] = {rid: [] for rid, _, _ in rules}
    for rel in tracked:
        text = None
        for rid, globs, frm in rules:
            if not _match_any(rel, globs):
                continue
            if text is None:
                text = _read_text(src / rel)
                if text is None:
                    break
            if frm in text:
                counts[rid] += 1
                hits[rid].append(rel)
    deletes = [d for d in manifest.get("deletes", []) if (src / d).exists()]
    return {
        "vars": vars_,
        "replacement_files": counts,
        "replacement_hits": hits,
        "would_delete": deletes,
        "templates": list((manifest.get("templates") or {}).keys()),
    }


# ── init: sembrar el bundle del cliente desde el workspace madre ──────────────

_TODO_LINES = (
    "TODO-BRAND: este archivo se sembró desde el workspace del motor con la",
    "marca sustituida mecánicamente. Reescribí el CONTENIDO (producto, tono,",
    "guiones) para {company} y borrá esta marca. forge apply bloquea mientras",
    "quede TODO-BRAND (usa --allow-todos solo para clones de prueba).",
)
TODO_BANNER = "<!-- " + "\n     ".join(_TODO_LINES) + " -->\n\n"


def _todo_banner(path: Path, company: str) -> str:
    """El banner en la sintaxis de comentario del archivo: un `<!-- -->` dentro
    de un YAML (agent.yaml del MBA) lo rompería."""
    if path.suffix in (".yaml", ".yml"):
        return "".join(f"# {line}\n" for line in _TODO_LINES).format(company=company) + "\n"
    return TODO_BANNER.format(company=company)


def _with_banner(text: str, banner: str) -> str:
    """El banner va después del frontmatter `---` de una skill: antes lo
    rompería (el cargador espera el frontmatter en la primera línea)."""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            cut = end + len("\n---\n")
            return text[:cut] + "\n" + banner + text[cut:].lstrip("\n")
    return banner + text


INIT_SKIP = {"__pycache__", ".DS_Store"}
#: Datos REALES de hubara que el workspace del motor cita (la llave Nequi en
#: las políticas, el teléfono del operador). El bundle sembrado los deja como
#: marca pendiente: apply bloquea hasta que la tienda ponga los suyos.
INIT_REAL_DATA = {
    "3229041190": "TODO-BRAND-NEQUI",
    "3125671604": "TODO-BRAND-TELEFONO",
}


def run_init(slug: str, manifest: dict, src: Path = REPO, clients_dir: Path = CLIENTS) -> Path:
    client_dir = Path(clients_dir) / slug
    render_vars({"slug": slug})  # guards anti-hubara ANTES de escribir nada
    if client_dir.exists():
        raise ForgeError(f"{client_dir} ya existe — editálo o borralo")
    company = slug.title()
    (client_dir).mkdir(parents=True)
    (client_dir / "client.yaml").write_text(
        yaml.safe_dump(
            {
                "slug": slug,
                "company": company,
                "repo": f"einsteindark-edgm/Agency{company}",
                # backend del cliente: completar cuando exista (S8) o si ya hay
                # dominio propio — cablea móvil (CSP), platform tfvars y webhook
                "api_url": "",
                # App Operador (Android): id propio en Google Play + Firebase
                "android_app_id": f"com.acktos.{slug}",
                "aws": {
                    "region": "us-east-1",
                    "resource_prefix": f"agency{slug}",
                    "ssm_prefix": f"/{slug}",
                },
                "business": {
                    "country": "CO",
                    "currency": "COP",
                    "product_description": "TODO",
                    "domains": [],
                    "instagram": "",
                },
                # Política comercial: lo que el bot le dice al cliente sobre envío y
                # pagos (→ Terraform tenants.<t>.store → SSM). apply la exige completa.
                "commerce": {
                    "payment_nequi_number": "",  # llave Nequi/Bre-B; vacío = sin pago anticipado
                    "payment_link_surcharge_local": "TODO",  # recargo del link con Nequi/Bancolombia, p. ej. "1,5%"
                    "payment_link_surcharge_other": "TODO",  # con otros bancos, p. ej. "2,69%"
                    "shipping_local_zone": "TODO",  # p. ej. "Bogotá y municipios cercanos"
                    "shipping_local_city": "TODO",  # la ciudad de esa zona, p. ej. "Bogotá"
                    "shipping_rate_local_cop": "TODO",  # tarifa mínima local, p. ej. 7900
                    "shipping_rate_national_cop": "TODO",  # tarifa mínima nacional, p. ej. 16940
                    "cash_on_delivery_min_cop": "TODO",  # contra entrega desde este subtotal, p. ej. 45000
                    "sku_prefix": "TODO",  # prefijo de los SKU en Medusa, p. ej. "ACM-"
                },
                # consumido por forge/steps/ (supabase_provision + medusa_provision)
                "medusa": {
                    "repo": "TODO-owner/medusa-backend",  # de dónde jala Railway el Medusa
                    "supabase_org": "TODO-org-id",  # Supabase NO tiene namespaces: proyecto nuevo por cliente
                    "supabase_region": "us-east-1",
                },
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    domain_tpl = (ROOT / "templates" / "sales_domain.yaml.tpl").read_text(encoding="utf-8")
    domain = _render(domain_tpl, render_vars(load_client(client_dir)))
    (client_dir / DOMAIN_FILE).write_text(
        _todo_banner(client_dir / DOMAIN_FILE, company) + domain, encoding="utf-8"
    )
    ov = manifest["workspace_overlay"]
    preserve = manifest.get("preserve_tokens", [])
    for agent, (ws_rel, _) in overlay_agents(manifest).items():
        src_ws = src / ws_rel
        if not src_ws.is_dir():
            continue
        for f in _walk_files(src_ws):
            if INIT_SKIP.intersection(f.parts) or f.suffix == ".pyc":
                continue
            rel = f.relative_to(src_ws).as_posix()
            # el skill de catálogo viaja con nombre genérico `catalog/`
            rel = rel.replace("hubara_catalog/", f"{ov['catalog_skill_dirname']}/")
            target = client_dir / "workspace" / agent / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            text = _read_text(f)
            if text is None:
                shutil.copy2(f, target)
                continue
            text = _mask(text, preserve)
            for real, pending in INIT_REAL_DATA.items():
                text = text.replace(real, pending)
            text = (
                text.replace("Hubara", company)
                .replace("hubara_catalog", f"{slug}_catalog")
                # nombre del connector de MBA: es del cliente (lo ve Meta), no del motor
                .replace("hubara-commerce", f"{slug}-commerce")
            )
            text = _unmask(text, preserve)
            target.write_text(_with_banner(text, _todo_banner(f, company)), encoding="utf-8")
    return client_dir


# ── CLI ───────────────────────────────────────────────────────────────────────


def _client_dir(slug_or_path: str) -> Path:
    p = Path(slug_or_path)
    return p if p.is_dir() and (p / "client.yaml").exists() else CLIENTS / slug_or_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="forge", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="sembrar clients/<slug>/ (client.yaml + workspace)")
    p.add_argument("slug")
    p = sub.add_parser("plan", help="dry-run: qué tocaría cada regla del manifest hoy")
    p.add_argument("slug")
    p = sub.add_parser("apply", help="forjar el clon del cliente")
    p.add_argument("slug")
    p.add_argument("--dest", required=True)
    p.add_argument("--allow-todos", action="store_true")
    p = sub.add_parser("verify", help="scanner de residuales sobre un clon")
    p.add_argument("dest")
    p.add_argument("--client", required=True)
    p = sub.add_parser("publish", help="imprime los comandos gh para crear/pushear el repo")
    p.add_argument("dest")
    p.add_argument("--client", required=True)
    a = ap.parse_args(argv)
    manifest = load_manifest()

    try:
        if a.cmd == "init":
            d = run_init(a.slug, manifest)
            print(f"bundle sembrado en {d}")
            print("→ completá client.yaml y reescribí workspace/ (buscá TODO-BRAND)")
        elif a.cmd == "plan":
            plan = run_plan(REPO, _client_dir(a.slug), manifest)
            print(yaml.safe_dump(
                {
                    "replacement_files": plan["replacement_files"],
                    "would_delete": plan["would_delete"],
                    "templates": plan["templates"],
                },
                sort_keys=False,
                allow_unicode=True,
            ))
            zero = [k for k, v in plan["replacement_files"].items() if v == 0]
            if zero:
                print(f"⚠ reglas sin match (¿scope drift?): {', '.join(zero)}")
        elif a.cmd == "apply":
            report = run_apply(
                REPO, Path(a.dest), _client_dir(a.slug), manifest, allow_todos=a.allow_todos
            )
            print(yaml.safe_dump(report, sort_keys=False, allow_unicode=True))
            print(f"✓ clon forjado en {a.dest} (motor {report['engine_sha']}) — ver NEXT_STEPS.md")
        elif a.cmd == "verify":
            vars_ = render_vars(load_client(_client_dir(a.client)))
            if not Path(a.dest).is_dir():
                raise ForgeError(f"el clon {a.dest} no existe — forjalo primero (apply)")
            scan = scan_residuals(Path(a.dest), manifest, vars_)
            print(yaml.safe_dump(scan, sort_keys=False, allow_unicode=True))
            return 1 if scan["forbidden"] or scan["critical"] else 0
        elif a.cmd == "publish":
            vars_ = render_vars(load_client(_client_dir(a.client)))
            print("# forge no ejecuta esto — correlo vos (acción outward):")
            print(f"cd {a.dest}")
            print(f"gh repo create {vars_['repo']} --private --source . --push")
            print(f"python3 infra/scripts/aws_bootstrap.py github --repo {vars_['repo']}")
    except ForgeError as e:
        print(f"forge: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
