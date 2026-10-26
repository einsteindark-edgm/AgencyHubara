"""Qué secretos de SSM recibe cada contenedor de prod (`infra/compose/service-secrets.json`).

Cada servicio del compose de prod recibe solo los `SecureString` que su código
lee (revisión de seguridad del 2026-10-26). El mapa se CALCULA, no se escribe a
mano:

1. Raíces de cada servicio: el módulo de su `command` (`python -m …`); la API,
   además, los routers que `src/main.py` importa por nombre desde los manifests.
2. Cierre de imports con grimp, siguiendo también los nombres de módulo escritos
   como texto (`"src.x.y"` / `"src.x.y:attr"`), que es como se cargan los builtins
   del motor de decisiones y los conectores del SDK.
3. En cada módulo del cierre se busca el nombre de cada secreto. `config.py` define
   todos, así que en vez de mirarlo entero se cuenta qué símbolo suyo usa cada
   secreto (p. ej. `LLM_API_KEY_ENV` → `DEEPSEEK_API_KEY`) y se buscan esos símbolos.

Es una cota por arriba: si un módulo del cierre nombra el secreto, el servicio lo
recibe aunque esa rama no corra. LiteLLM recibe las llaves que nombra su config.

  cd hubara_agency && uv run python scripts/service_secrets.py --write   # regenerar
  cd hubara_agency && uv run python scripts/service_secrets.py --check   # comparar

Guarda: hubara_agency/tests/infra/test_service_secrets.py.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from functools import cache
from pathlib import Path

import grimp
import yaml

_AGENCY = Path(__file__).resolve().parents[1]
_REPO = _AGENCY.parent
_COMPOSE = _REPO / "infra" / "compose" / "docker-compose.prod.yml"
_MAP = _REPO / "infra" / "compose" / "service-secrets.json"
_VARIABLES = _REPO / "infra" / "terraform" / "platform" / "variables.tf"
_LITELLM_CONFIG = _REPO / "exoclaw-temporal" / "litellm_config.yaml"
_MANIFESTS = _REPO / "frontend_dashboard" / "src" / "plugins"
_CONFIG = _AGENCY / "src" / "platform" / "config.py"

#: SecureString que existen en `/hubara/<tenant>/` sin estar en `secret_keys` de
#: Terraform (se crearon a mano). Se listan acá para que tengan dueño.
SSM_ONLY_SECRETS = (
    "MEDUSA_DEFAULT_SHIPPING_OPTION_ID",
    "MEDUSA_REGION_ID",
    "MEDUSA_SALES_CHANNEL_ID",
    "META_CAPI_ACCESS_TOKEN",
    "META_CAPI_DATASET_ID",
    "META_EXTRA_CATALOG_IDS",
    "PAYMENT_TRANSFER_ACCOUNT_NUMBER",
    "WHATSAPP_BUSINESS_ACCOUNT_ID",
)
#: Lo usa solo la caja (el `docker login` del deploy), nunca un contenedor.
HOST_ONLY = ("GHCR_PULL_TOKEN",)
#: Se leen por la API de SSM, no por el entorno: no van a ningún archivo.
#: `oauth` es el basename de `/hubara/<tenant>/meta/oauth` (token OAuth de Meta del plugin ads).
RUNTIME_ONLY = ("oauth",)

_MODULE_LITERAL = re.compile(r"""["'](src(?:\.[A-Za-z_][A-Za-z0-9_]*)+)(?::[A-Za-z_][A-Za-z0-9_]*)?["']""")


def terraform_secret_keys() -> set[str]:
    text = _VARIABLES.read_text(encoding="utf-8")
    block = text[text.index('variable "secret_keys"') :].split("]", 1)[0]
    return set(re.findall(r'"([A-Z][A-Z0-9_]+)"', block))


def all_secrets() -> set[str]:
    return terraform_secret_keys() | set(SSM_ONLY_SECRETS) | set(HOST_ONLY) | set(RUNTIME_ONLY)


def service_roots() -> dict[str, set[str]]:
    services = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))["services"]
    roots: dict[str, set[str]] = {}
    for name, svc in services.items():
        if svc.get("image") != "${HUBARA_IMAGE}":
            continue
        cmd = svc["command"]
        roots[name] = {cmd[cmd.index("-m") + 1]} if "-m" in cmd else {"src.main"}
    api_routers: set[str] = set()
    for manifest in sorted(_MANIFESTS.glob("*/plugin.yaml")):
        api = (yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}).get("api") or {}
        if api.get("python_module"):
            api_routers.add(api["python_module"])
        api_routers |= {r["module"] for r in api.get("legacy_routers") or []}
    for name, mods in roots.items():
        if "src.main" in mods:
            mods |= api_routers
    return roots


@cache
def _graph() -> grimp.ImportGraph:
    if str(_AGENCY) not in sys.path:  # corrido como script, `src` no está en el path
        sys.path.insert(0, str(_AGENCY))
    return grimp.build_graph("src", "exoclaw_temporal", include_external_packages=True, cache_dir=None)


def _source(module: str) -> str | None:
    base = _AGENCY if module.startswith("src") else _REPO / "exoclaw-temporal" / "src"
    for path in (base / (module.replace(".", "/") + ".py"), base / module.replace(".", "/") / "__init__.py"):
        if path.exists():
            return path.read_text(encoding="utf-8", errors="replace")
    return None


def _closure(start: set[str]) -> set[str]:
    graph = _graph()
    internal = {m for m in graph.modules if m.startswith(("src", "exoclaw_temporal"))}
    seen: set[str] = set()
    todo = set(start)
    while todo:
        module = todo.pop()
        if module in seen or module not in internal:
            continue
        seen.add(module)
        todo |= {m for m in graph.find_upstream_modules(module) if m in internal} - seen
        text = _source(module) or ""
        todo |= {lit for lit in _MODULE_LITERAL.findall(text) if lit in internal} - seen
    return seen


def _config_symbols(secrets: set[str]) -> dict[str, set[str]]:
    """Símbolo de `config.py` → secretos que lee (para las lecturas indirectas)."""
    text = _CONFIG.read_text(encoding="utf-8")
    out: dict[str, set[str]] = {}
    for node in ast.parse(text).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names = [node.name]
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        else:
            continue
        segment = ast.get_source_segment(text, node) or ""
        used = {s for s in secrets if re.search(rf"\b{s}\b", segment)}
        for name in names:
            if used and name not in secrets:
                out[name] = used
    return out


def _secrets_read_by(modules: set[str], secrets: set[str], symbols: dict[str, set[str]]) -> set[str]:
    found: set[str] = set()
    for module in modules:
        if module == "src.platform.config":
            continue
        text = _source(module)
        if not text:
            continue
        found |= {s for s in secrets if re.search(rf"\b{s}\b", text)}
        for symbol, secs in symbols.items():
            if re.search(rf"\b{symbol}\b", text):
                found |= secs
    return found


def litellm_secrets(secrets: set[str]) -> list[str]:
    refs = set(re.findall(r"os\.environ/([A-Z0-9_]+)", _LITELLM_CONFIG.read_text(encoding="utf-8")))
    return sorted(refs & secrets)


def compute_map() -> dict[str, list[str]]:
    secrets = all_secrets()
    searchable = secrets - set(HOST_ONLY) - set(RUNTIME_ONLY)
    symbols = _config_symbols(searchable)
    result: dict[str, list[str]] = {}
    for service, roots in sorted(service_roots().items()):
        result[service] = sorted(_secrets_read_by(_closure(roots), searchable, symbols))
    result["litellm"] = litellm_secrets(searchable)
    used = {k for keys in result.values() for k in keys}
    result["_host"] = sorted(HOST_ONLY)
    result["_none"] = sorted(secrets - used - set(HOST_ONLY))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="regenera infra/compose/service-secrets.json")
    mode.add_argument("--check", action="store_true", help="falla si el mapa quedó viejo")
    args = parser.parse_args(argv)
    computed = compute_map()
    rendered = json.dumps(computed, indent=2, ensure_ascii=False) + "\n"
    if args.write:
        _MAP.write_text(rendered, encoding="utf-8")
        print(f"escrito {_MAP.relative_to(_REPO)} ({len(computed)} entradas)")
        return 0
    current = json.loads(_MAP.read_text(encoding="utf-8")) if _MAP.exists() else None
    if current != computed:
        print("el mapa quedó viejo: cd hubara_agency && uv run python scripts/service_secrets.py --write", file=sys.stderr)
        return 1
    print("mapa al día")
    return 0


if __name__ == "__main__":
    sys.exit(main())
