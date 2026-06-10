"""Conformance gate del PROTOCOLO de plugin — "lo declarado existe".

Concepto (estilo protocol-oriented, à la Swift/Apple): el ``plugin.yaml`` es la
**declaración de conformidad** a un protocolo; este archivo es el **checker
estructural** que la hace cumplir. Un manifest puede DECIR cualquier cosa
(``agentic``, ``dashboard.workspace``, ``task_queue``…) — cada claim que ningún
gate verifica es una mentira en potencia (PM-3: "schema miente sobre el
código"). Regla de oro: **ningún campo del manifest puede existir sin su check
de conformidad** — si agregás un campo al schema, agregá acá (o en
``test_manifest_orchestration_consistency.py``) el test que lo ata al código.

Cobertura actual (cada uno mapea a una regla P-# del PLUGIN_CONTRACT.md y a un
modo de fallo PM-# del pre-mortem §9):

  * P-6  (P-ENABLED)   — ``validate_enabled`` exige deps duras habilitadas.
  * P-15 (P-WORKSPACE) — todo ``dashboard.workspace`` existe en disco (PM-6).
  * P-16 (P-SELFQUEUE) — el worker se auto-referencia con SU (plugin, worker)
                         en ``get_task_queue`` (PM-5 / AP-9).
  * P-17 (P-AGENTIC)   — ``agentic: true`` ⟺ ≥1 worker con bloque
                         ``dashboard:`` (PM-3 / AP-11). El código de
                         agents_admin ahora FILTRA por ``agentic`` — este test
                         garantiza que el flag no drifte de la realidad.

Complementos en otros archivos: identidad de workflows + transitions
(``test_manifest_orchestration_consistency.py``), aislamiento de imports y
módulos (``test_plugin_contract.py``), mecanismo deploy
(``tests/plugins/test_premortem_invariants.py``).

Introspección pura (filesystem + AST) — sin importar módulos de plugins.
"""
from __future__ import annotations

import ast

import pytest

from tests.architecture._plugin_contract_helpers import (
    BE_PLUGINS,
    HUBARA_ROOT,
    REPO_ROOT,
    all_manifests,
    backend_plugin_ids,
    manifest_ids,
)


# ----------------------------------------------------------------------------
# P-6 · P-ENABLED — validate_enabled (loader nuevo, src/platform/plugin_loader)
# ----------------------------------------------------------------------------


def test_p6_validate_enabled_raises_on_missing_dep() -> None:
    """Habilitar un plugin sin sus deps duras = error de boot, no silencio."""
    from src.platform.plugin_loader import PluginDependencyError, validate_enabled

    manifests = {
        "a": {"depends_on": ["b"]},
        "b": {"depends_on": []},
        "c": {},
    }
    # a sin b → error.
    with pytest.raises(PluginDependencyError):
        validate_enabled({"a"}, manifests)
    # typo / plugin inexistente → error.
    with pytest.raises(PluginDependencyError):
        validate_enabled({"nope"}, manifests)
    # set coherente → no levanta.
    validate_enabled({"a", "b"}, manifests)
    validate_enabled({"c"}, manifests)
    # None = todos habilitados → no hay nada que validar.
    validate_enabled(None, manifests)


def test_p6_eta_requires_chats_in_live_manifests() -> None:
    """Candado de la decisión D4a: `eta` declara la dep dura REAL (el ingest
    de WhatsApp vive en `chats`) — habilitar eta sin chats debe fallar.

    Si este test te molesta porque moviste el ingest a platform (plan F6/D4b),
    el fix es: eliminar `depends_on: [chats]` del manifest de eta Y este test
    en el mismo PR.
    """
    from src.platform.plugin_loader import PluginDependencyError, validate_enabled

    manifests = dict(all_manifests())
    assert "chats" in (manifests.get("eta") or {}).get("depends_on", []), (
        "eta/plugin.yaml debe declarar depends_on: [chats] mientras el ingest "
        "de WhatsApp viva en chats (N-2 / D4a)"
    )
    with pytest.raises(PluginDependencyError):
        validate_enabled({"eta"}, manifests)
    # F4c: chats declara depends_on: [orders] (cast order-ref) — el cierre
    # transitivo mínimo de eta es {eta, chats, orders}.
    with pytest.raises(PluginDependencyError):
        validate_enabled({"eta", "chats"}, manifests)
    validate_enabled({"eta", "chats", "orders"}, manifests)


# ----------------------------------------------------------------------------
# P-15 · P-WORKSPACE — dashboard.workspace existe en disco (PM-6)
# ----------------------------------------------------------------------------


def test_p15_dashboard_workspace_paths_exist() -> None:
    """Todo ``agent.workers[].dashboard.workspace`` resuelve a un dir real.

    El path es repo-root-relative y se mantiene A MANO en el manifest (PM-6):
    tras mover un agente, un path stale produce una card de agente sin sus
    archivos de workspace — silencioso. Este gate lo vuelve un fallo de CI.
    """
    bad: list[str] = []
    for pid, manifest in all_manifests():
        for w in (manifest.get("agent") or {}).get("workers") or []:
            if not isinstance(w, dict):
                continue
            ws = str(((w.get("dashboard") or {}).get("workspace")) or "").strip()
            if ws and not (REPO_ROOT / ws).is_dir():
                bad.append(f"{pid}/{w.get('name')}: workspace {ws!r} no existe en disco")
    assert not bad, "dashboard.workspace stale (P-15 / PM-6):\n  " + "\n  ".join(bad)


# ----------------------------------------------------------------------------
# P-16 · P-SELFQUEUE — self-reference del worker (PM-5 / AP-9)
# ----------------------------------------------------------------------------


def _get_task_queue_first_args(pyfile) -> list[str]:
    """Valores del primer arg constante de toda llamada a ``get_task_queue``."""
    tree = ast.parse(pyfile.read_text(encoding="utf-8"), filename=str(pyfile))
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else (
            fn.attr if isinstance(fn, ast.Attribute) else None
        )
        if name != "get_task_queue" or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            out.append(first.value)
    return out


def test_p16_worker_task_queue_self_reference_matches_dir() -> None:
    """Ningún ``src/plugins/X/workers/*.py`` llama ``get_task_queue("Y", ...)``
    con Y≠X.

    PM-5: al extraer eta hubo que repointar ``get_task_queue("chats","eta")``
    → ``("eta","eta")`` a mano. Un grep de imports no lo ve (es un string, no
    un import). Si un día un worker legítimamente necesita resolver la queue
    de OTRO worker… no lo hagas: eso es orquestación y va por el dispatcher.
    """
    bad: list[str] = []
    for pdir in sorted(BE_PLUGINS.iterdir()):
        if not pdir.is_dir() or pdir.name.startswith((".", "_")):
            continue
        workers_dir = pdir / "workers"
        if not workers_dir.is_dir():
            continue
        for py in workers_dir.glob("*.py"):
            for plugin_arg in _get_task_queue_first_args(py):
                if plugin_arg != pdir.name:
                    bad.append(
                        f"{py.relative_to(HUBARA_ROOT).as_posix()} → "
                        f"get_task_queue({plugin_arg!r}, ...) ≠ plugin {pdir.name!r}"
                    )
    assert not bad, "Worker con self-reference ajena (P-16 / PM-5):\n  " + "\n  ".join(bad)


# ----------------------------------------------------------------------------
# P-13 / P-26 · P-PARITY — ids coherentes cross-stack, sin huérfanos
# ----------------------------------------------------------------------------


def test_p13_p26_backend_dirs_and_manifests_are_coherent() -> None:
    """Todo dir backend `src/plugins/<id>/` tiene su manifest (P-26: un dir
    huérfano es código muerto invisible — ningún loader lo levanta) y todo
    manifest id es un id válido. La dirección inversa (manifest que declara
    backend sin código) la cubre P-2."""
    manifests = set(manifest_ids())
    orphans = sorted(set(backend_plugin_ids()) - manifests)
    assert not orphans, (
        "Dirs backend sin manifest (P-26 — código muerto invisible):\n  - "
        + "\n  - ".join(f"hubara_agency/src/plugins/{o}/" for o in orphans)
        + "\nCreá frontend_dashboard/src/plugins/<id>/plugin.yaml o borrá el dir."
    )


# ----------------------------------------------------------------------------
# P-18 · P-ROUTE — route registry declarativo (PM-2 / AP-10)
# ----------------------------------------------------------------------------


def test_p18_route_registry_resolves_declared_routes() -> None:
    """El registry (platform/routing) construye desde los manifests reales:
    rutas únicas, no-core, template con {session_id}. `eta` resuelve a su
    dueño declarado."""
    from src.platform.routing import _build_registry

    registry = _build_registry(None)  # None = todos (validación del universo)
    assert "eta" in registry, "eta/plugin.yaml debe declarar owns_route: eta"
    target = registry["eta"]
    assert (target.plugin_id, target.worker_name) == ("eta", "eta")
    assert target.workflow_id("wa_123") == "eta-wa_123"


def test_p18_transitions_match_route_owner_template() -> None:
    """PM-2 (el coupling tolerante más peligroso): el template del workflow_id
    de la ruta vive UNA vez (en el manifest del dueño). Toda transition de
    CUALQUIER manifest que targetee a un worker dueño de ruta debe usar el
    MISMO prefijo — si drifta, el inbound rutearía a un workflow inexistente
    y caería a Sales EN SILENCIO."""
    owners: dict[tuple[str, str], str] = {}
    for pid, manifest in all_manifests():
        for w in (manifest.get("agent") or {}).get("workers") or []:
            if isinstance(w, dict) and w.get("owns_route"):
                tpl = str(w.get("route_workflow_id_template") or "")
                owners[(pid, str(w.get("name")))] = tpl.split("{")[0]

    bad: list[str] = []
    for pid, manifest in all_manifests():
        for w in (manifest.get("agent") or {}).get("workers") or []:
            if not isinstance(w, dict):
                continue
            for t in w.get("transitions") or []:
                action = (t or {}).get("action") or {}
                key = (
                    str(action.get("target_plugin") or pid),
                    str(action.get("target_worker") or ""),
                )
                if key not in owners:
                    continue
                tpl = str(action.get("workflow_id_template") or "")
                prefix = tpl.split("{")[0]
                if prefix != owners[key]:
                    bad.append(
                        f"{pid}: transition {t.get('id')!r} → {key[0]}/{key[1]} usa "
                        f"prefijo {prefix!r} ≠ {owners[key]!r} declarado por el dueño"
                    )
    assert not bad, "Template de ruta drifteado (P-18 / PM-2):\n  " + "\n  ".join(bad)


def test_p18_inbound_routing_has_no_hardcoded_route_prefixes() -> None:
    """El ruteo de inbounds de chats NO hardcodea el workflow-id de ninguna
    ruta de plugin — debe resolver TODO por el registry. (El reemplazo del
    parche P-18 transitorio: ya no atamos dos copias; prohibimos la segunda.)"""
    from src.platform.routing import _build_registry

    use_case = (
        BE_PLUGINS / "chats" / "agent" / "sales" / "use_cases" /
        "load_or_start_sales_session.py"
    )
    source = use_case.read_text(encoding="utf-8")
    bad: list[str] = []
    for route, target in _build_registry(None).items():
        prefix = target.workflow_id_template.split("{")[0]
        if prefix and f'"{prefix}' in source.replace(f'f"{prefix}', f'"{prefix}'):
            bad.append(
                f"ruta {route!r}: el prefijo {prefix!r} aparece hardcodeado en "
                f"{use_case.name} — usá resolve_route_workflow_id (P-18)"
            )
    assert not bad, "\n  ".join(bad)


# ----------------------------------------------------------------------------
# P-21 · P-SELFGATE — todo worker se auto-gatea con ensure_plugin_enabled
# ----------------------------------------------------------------------------


def _ensure_plugin_enabled_args(pyfile) -> list[str]:
    """Valores del primer arg constante de toda llamada a ensure_plugin_enabled."""
    tree = ast.parse(pyfile.read_text(encoding="utf-8"), filename=str(pyfile))
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else (
            fn.attr if isinstance(fn, ast.Attribute) else None
        )
        if name != "ensure_plugin_enabled" or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            out.append(first.value)
    return out


def test_p21_every_worker_self_gates_with_own_plugin() -> None:
    """Todo módulo de worker llama ``ensure_plugin_enabled("<su-plugin>")``.

    Defensa en profundidad de INV-2 (N-1): los containers de prod corren
    ``python -m <worker>`` directo (NO pasan por run_workers) — sin este
    self-gate, un container huérfano (PM-1) o un deployment mal configurado
    pollea su queue aunque el plugin esté apagado. El protocolo de plugin
    exige el gate como primera línea del ``main()``.
    """
    bad: list[str] = []
    for pdir in sorted(BE_PLUGINS.iterdir()):
        if not pdir.is_dir() or pdir.name.startswith((".", "_")):
            continue
        workers_dir = pdir / "workers"
        if not workers_dir.is_dir():
            continue
        for py in workers_dir.glob("*.py"):
            if py.name == "__init__.py":
                continue
            args = _ensure_plugin_enabled_args(py)
            if not args:
                bad.append(
                    f"{py.relative_to(HUBARA_ROOT).as_posix()}: no llama "
                    f"ensure_plugin_enabled(...) — agregá el self-gate al main()"
                )
            elif any(a != pdir.name for a in args):
                bad.append(
                    f"{py.relative_to(HUBARA_ROOT).as_posix()}: se gatea con "
                    f"{args!r} ≠ su plugin {pdir.name!r}"
                )
    assert not bad, "Worker sin self-gate (P-21 / N-1):\n  " + "\n  ".join(bad)


# ----------------------------------------------------------------------------
# P-17 · P-AGENTIC — agentic ⟺ dashboard workers (PM-3 / AP-11)
# ----------------------------------------------------------------------------


def test_p17_agentic_flag_matches_dashboard_workers() -> None:
    """``agentic: true`` ⟺ el plugin tiene ≥1 worker con bloque ``dashboard:``.

    El schema promete que ``agentic`` gatea ``GET /api/agents``; el código de
    ``agents_admin.discover_agents`` ahora LO HONRA (filtra por agentic +
    ENABLED_PLUGINS). Este test impide que flag y realidad driften en
    cualquiera de las dos direcciones (PM-3: el schema no puede mentir).
    """
    bad: list[str] = []
    for pid, manifest in all_manifests():
        has_dash = any(
            isinstance(w, dict) and w.get("dashboard")
            for w in (manifest.get("agent") or {}).get("workers") or []
        )
        agentic = bool(manifest.get("agentic", False))
        if has_dash != agentic:
            bad.append(
                f"{pid}: agentic={agentic} pero has_dashboard_worker={has_dash} — "
                + (
                    "agregá `agentic: true` al manifest"
                    if has_dash
                    else "sacá `agentic: true` o agregá el bloque dashboard del worker"
                )
            )
    assert not bad, "agentic ⊥ dashboard (P-17 / PM-3):\n  " + "\n  ".join(bad)
