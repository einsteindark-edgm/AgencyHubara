"""Ratchet — `metadata.json` se escribe SOLO por `FilesystemMetadataStore`.

Incidente 2026-10-06 (conversación de prueba): el turno 4 mandó la foto del
producto; el flush la sacó de `pending_ui_intents` y anotó su entrega en
`outbound_media_index`. Otro escritor que había leído ANTES escribió su copia
entera DESPUÉS: la foto volvió a la cola (el turno 5 la mandó otra vez sin que
el bot la pidiera) y la entrega del turno 4 desapareció del índice.

La causa no era un escritor: eran ~30 caminos distintos de escribir el mismo
archivo (`write_text` sin candado, `.write()` del store con una lectura vieja,
temp+rename propios). La regla desde entonces:

  * Una sola forma de escribir `metadata.json`: `src/platform/state.py`
    (`FilesystemMetadataStore`), con candado y atómica.
  * Cada escritor toca SOLO lo suyo: ``update(session_id, mutator)`` sobre la
    lectura fresca, o ``write_merged(session_id, base=..., ours=...)`` (merge
    de tres vías) si acumula cambios entre esperas.
  * ``.write()`` del store (reemplazo entero) tampoco va fuera de `state.py`.

Este gate falla si un módulo de `src/` escribe `metadata.json` por otro camino.
La lista permitida lleva SIEMPRE su razón y solo puede achicarse.

Vive en `tests/platform/` y no en `tests/architecture/` porque esa carpeta es
`protected: true` en `.hubara/spinal-files.yaml` (sumarle un archivo exige un
ADR y el label `architecture-change`). Lleva la marca `architecture`: corre
con `pytest -m architecture` y en el paso propio del CI de arquitectura.
"""
from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

HUB_ROOT: Path = Path(__file__).resolve().parents[2]
SRC_ROOT: Path = HUB_ROOT / "src"
STATE_MODULE = "src/platform/state.py"

#: `ruta::función` → por qué puede escribir `metadata.json` sin el store.
ALLOWED: dict[str, str] = {
    "src/plugins/chats/agent/sales_lab/sandbox/readings.py::_write_metadata": (
        "Laboratorio: el ingest simulado escribe el metadata del vault de PRUEBA "
        "del sandbox antes de arrancar el workflow del caso (único escritor en "
        "ese momento); nunca corre sobre el vault de producción."
    ),
    "src/plugins/chats/agent/sales_lab/sandbox/materialize.py::materialize_case": (
        "Laboratorio: materializa el vault de prueba de un caso en un directorio "
        "recién creado (nadie más lo lee ni lo escribe) y cambia el número real "
        "por el ficticio en el texto antes de escribirlo."
    ),
}

#: Ratchet: la lista permitida nunca crece (bajar el número al drenar una entrada).
MAX_ALLOWED = 2

_META_NAME = re.compile(r"meta(?:data)?_?(?:file|path)$", re.IGNORECASE)
_STORE_RECEIVER = re.compile(r"store", re.IGNORECASE)
_WRITE_MODE = re.compile(r"[wax+]")


def _docstrings(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _own_nodes(scope: ast.AST) -> list[ast.AST]:
    """Nodos de `scope` sin entrar a funciones/clases anidadas (cada una se
    revisa por separado, con su propio nombre)."""
    out: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        out.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return out


def _scopes(tree: ast.Module) -> Iterator[tuple[str, ast.AST]]:
    """(`nombre calificado`, nodo) de cada función del módulo, y el módulo."""
    yield "<module>", tree

    def walk(node: ast.AST, prefix: str) -> Iterator[tuple[str, ast.AST]]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                yield name, child
                yield from walk(child, f"{name}.")
            elif isinstance(child, ast.ClassDef):
                yield from walk(child, f"{prefix}{child.name}.")
            else:
                yield from walk(child, prefix)

    yield from walk(tree, "")


def _mode(call: ast.Call, position: int) -> str | None:
    mode: object = None
    if len(call.args) > position and isinstance(call.args[position], ast.Constant):
        mode = call.args[position].value
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            mode = kw.value.value
    return mode if isinstance(mode, str) else None


def _write_primitive(call: ast.Call) -> str | None:
    """¿La llamada escribe un archivo? (`write_text`, `open('w')`, `json.dump`,
    `atomic_write_json`, `os.replace`, `os.fdopen(.., 'w')`)."""
    func = call.func
    if isinstance(func, ast.Attribute):
        owner = func.value.id if isinstance(func.value, ast.Name) else None
        if func.attr in ("write_text", "write_bytes"):
            return func.attr
        if func.attr == "dump" and owner == "json":
            return "json.dump"
        if func.attr in ("replace", "rename") and owner == "os":
            return f"os.{func.attr}"
        if func.attr == "fdopen" and _WRITE_MODE.search(_mode(call, 1) or ""):
            return "os.fdopen(w)"
        if func.attr == "open" and _WRITE_MODE.search(_mode(call, 0) or ""):
            return "open(w)"
        if func.attr == "atomic_write_json":
            return "atomic_write_json"
    if isinstance(func, ast.Name):
        if func.id == "open" and _WRITE_MODE.search(_mode(call, 1) or ""):
            return "open(w)"
        if func.id == "atomic_write_json":
            return "atomic_write_json"
    return None


def _callee_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _is_store_write(call: ast.Call) -> bool:
    """`<algo>store<algo>.write(session_id, data)`: reemplazo entero del documento."""
    func = call.func
    if not (isinstance(func, ast.Attribute) and func.attr == "write" and len(call.args) == 2):
        return False
    receiver = ast.unparse(func.value)
    return bool(_STORE_RECEIVER.search(receiver))


def scan_source(source: str, rel: str) -> set[str]:
    """`ruta::función (cómo)` de cada escritura de `metadata.json` fuera del store."""
    tree = ast.parse(source, filename=rel)
    docs = _docstrings(tree)
    found: set[str] = set()
    for name, scope in _scopes(tree):
        nodes = _own_nodes(scope)
        mentions = "metadata" in name.lower() or any(
            (
                isinstance(n, ast.Constant)
                and isinstance(n.value, str)
                and "metadata.json" in n.value
                and id(n) not in docs
            )
            or (isinstance(n, ast.Name) and _META_NAME.search(n.id))
            or (isinstance(n, ast.arg) and _META_NAME.search(n.arg))
            for n in nodes
        )
        for node in nodes:
            if not isinstance(node, ast.Call):
                continue
            if _is_store_write(node):
                found.add(f"{rel}::{name} (.write del store)")
            primitive = _write_primitive(node)
            if primitive and mentions:
                found.add(f"{rel}::{name} ({primitive})")
            callee = _callee_name(node).lower()
            if ("write" in callee or "dump" in callee) and any(
                "metadata.json" in ast.unparse(arg) for arg in [*node.args, *(k.value for k in node.keywords)]
            ):
                found.add(f"{rel}::{name} ({_callee_name(node)} sobre metadata.json)")
    return found


def scan_tree() -> set[str]:
    found: set[str] = set()
    for path in sorted(SRC_ROOT.rglob("*.py")):
        rel = path.relative_to(HUB_ROOT).as_posix()
        if rel == STATE_MODULE:
            continue
        found |= scan_source(path.read_text(encoding="utf-8"), rel)
    return found


def _location(finding: str) -> str:
    return finding.split(" (", 1)[0]


# --- el gate ------------------------------------------------------------------


def test_metadata_json_is_written_only_through_the_store() -> None:
    offenders = sorted(f for f in scan_tree() if _location(f) not in ALLOWED)
    assert not offenders, (
        "Estos caminos escriben metadata.json por fuera de FilesystemMetadataStore "
        "(incidente 2026-10-06: una copia vieja devolvió una foto ya entregada a la "
        "cola). Usa `store.update(session_id, mutator)` con SOLO tus llaves, o "
        "`store.write_merged(session_id, base=<copia de lo que leíste>, ours=<tu dict>)` "
        "si acumulas cambios entre esperas:\n  " + "\n  ".join(offenders)
    )


def test_every_allowed_entry_still_writes() -> None:
    """Ratchet: al drenar una entrada, se borra de la lista (y se baja el tope)."""
    locations = {_location(f) for f in scan_tree()}
    stale = sorted(set(ALLOWED) - locations)
    assert not stale, f"Ya no escriben metadata.json por fuera del store; bórralas de ALLOWED: {stale}"


def test_the_allowlist_never_grows_and_every_entry_says_why() -> None:
    assert len(ALLOWED) <= MAX_ALLOWED, "La lista permitida no crece: usa el store."
    for location, reason in ALLOWED.items():
        assert "::" in location, location
        assert len(reason.split()) >= 8, f"{location}: la razón tiene que explicar por qué"


# --- el gate no pasa en vacío -------------------------------------------------

_SAMPLES = {
    "write_text": '''
def save(vault, sid, data):
    path = vault / sid / "metadata.json"
    path.write_text(json.dumps(data))
''',
    "store_write": '''
class Tool:
    def run(self, sid, data):
        self._store.write(sid, data)
''',
    "atomic": '''
def persist(metadata_file, data):
    atomic_write_json(metadata_file, data)
''',
    "open_w": '''
def save(metadata_path, data):
    with open(metadata_path, "w") as fh:
        json.dump(data, fh)
''',
    "temp_rename": '''
def _write_metadata(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)
''',
    "helper_call": '''
def build(box, value):
    _write_json(box / "metadata.json", value)
''',
}


def test_the_scanner_catches_every_known_way_of_writing_metadata() -> None:
    for label, source in _SAMPLES.items():
        assert scan_source(source, f"src/sample_{label}.py"), f"el gate no ve el patrón {label!r}"


def test_the_scanner_ignores_other_files_and_reads() -> None:
    source = '''
def save_index(vault, data):
    """Escribe el índice (no metadata.json)."""
    (vault / "_index.json").write_text(json.dumps(data))


def read(vault, sid):
    return json.loads((vault / sid / "metadata.json").read_text())


def update(store, sid):
    store.update(sid, lambda d: d)
'''
    assert scan_source(source, "src/sample_ok.py") == set()


def test_the_gate_scans_the_real_tree() -> None:
    assert len(list(SRC_ROOT.rglob("*.py"))) > 100
