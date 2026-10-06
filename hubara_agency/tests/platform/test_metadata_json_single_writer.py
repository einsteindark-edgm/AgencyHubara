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
from dataclasses import dataclass, field
from functools import lru_cache
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

_METADATA_FILE = "metadata.json"
_META_NAME = re.compile(r"meta(?:data)?_?(?:file|path)$", re.IGNORECASE)
_META_WORD = re.compile(r"meta", re.IGNORECASE)
_WRITER_NAME = re.compile(r"write|dump|save|persist", re.IGNORECASE)
_STORE_RECEIVER = re.compile(r"store|meta", re.IGNORECASE)
_WRITE_MODE = re.compile(r"[wax+]")
_STORE_WRITE_KWARGS = {"session_id", "data"}


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


def _callee_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _module_name(rel: str) -> str:
    """`src/a/b.py` → `src.a.b` (`__init__.py` → el paquete)."""
    parts = rel.removesuffix(".py").split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


@dataclass
class _Module:
    """Lo que el escáner sabe de un módulo para no dejarse esquivar
    (revisión del PR #393, D4)."""

    rel: str
    tree: ast.Module
    docs: set[int]
    #: constantes de módulo con la ruta (`META = "metadata.json"`).
    consts: set[str] = field(default_factory=set)
    #: funciones que DEVUELVEN una ruta a metadata.json (`def _meta(...)`).
    path_helpers: set[str] = field(default_factory=set)
    #: funciones que escriben algún archivo (`def _save(path, data)`).
    writer_helpers: set[str] = field(default_factory=set)
    #: funciones conocidas (definidas acá o importadas de otro módulo de
    #: `src/`): de esas se sabe si escriben; el nombre (`_save_*`) solo cuenta
    #: para las desconocidas.
    known_functions: set[str] = field(default_factory=set)
    #: alias locales de `atomic_write_json` y de los módulos `json` / `os`.
    awj: set[str] = field(default_factory=lambda: {"atomic_write_json"})
    json_mods: set[str] = field(default_factory=lambda: {"json"})
    json_dumps: set[str] = field(default_factory=set)
    os_mods: set[str] = field(default_factory=lambda: {"os"})
    #: `from <módulo> import <nombre> [as alias]` → alias: (módulo, nombre).
    imported: dict[str, tuple[str, str]] = field(default_factory=dict)
    #: ¿el módulo nombra algo «meta»? (identificadores o textos)
    mentions_meta: bool = False


def _analyze(source: str, rel: str) -> _Module:
    tree = ast.parse(source, filename=rel)
    module = _Module(rel=rel, tree=tree, docs=_docstrings(tree))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name
                if alias.name == "json":
                    module.json_mods.add(local)
                elif alias.name == "os":
                    module.os_mods.add(local)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                local = alias.asname or alias.name
                if alias.name == "atomic_write_json":
                    module.awj.add(local)
                elif node.module == "json" and alias.name == "dump":
                    module.json_dumps.add(local)
                elif node.module:
                    module.imported[local] = (node.module, alias.name)
    for stmt in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(stmt, ast.Assign):
            targets, value = stmt.targets, stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            targets, value = [stmt.target], stmt.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str) and _METADATA_FILE in value.value:
            module.consts.update(t.id for t in targets if isinstance(t, ast.Name))
    module.mentions_meta = any(_names_meta(node, module.docs) for node in ast.walk(tree))
    functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    module.known_functions.update(fn.name for fn in functions)
    for fn in functions:
        if any(isinstance(n, ast.Call) and _write_primitive(n, module) for n in _own_nodes(fn)):
            module.writer_helpers.add(fn.name)
    changed = True
    while changed:
        changed = False
        for fn in functions:
            if fn.name in module.path_helpers:
                continue
            returns = [n for n in _own_nodes(fn) if isinstance(n, ast.Return) and n.value is not None]
            if any(_is_path_expression(r.value, module) for r in returns):
                module.path_helpers.add(fn.name)
                changed = True
    return module


def _names_meta(node: ast.AST, docs: set[int]) -> bool:
    if isinstance(node, ast.Name):
        return bool(_META_WORD.search(node.id))
    if isinstance(node, ast.Attribute):
        return bool(_META_WORD.search(node.attr))
    if isinstance(node, ast.arg):
        return bool(_META_WORD.search(node.arg))
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return bool(_META_WORD.search(node.name))
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
        return "metadata" in node.value.lower()
    return False


def _calls_path_helper(call: ast.Call, module: _Module) -> bool:
    return _callee_name(call) in module.path_helpers


def _names_metadata_path(node: ast.AST, module: _Module) -> bool:
    """Un nodo que nombra una ruta a metadata.json: el literal (fuera de
    docstrings), la constante de módulo, un nombre `metadata_file`/`meta_path`
    o la llamada a un helper que la devuelve."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return _METADATA_FILE in node.value and id(node) not in module.docs
    if isinstance(node, ast.Name):
        return node.id in module.consts or bool(_META_NAME.search(node.id))
    if isinstance(node, ast.arg):
        return bool(_META_NAME.search(node.arg))
    if isinstance(node, ast.Call):
        return _calls_path_helper(node, module)
    return False


def _is_metadata_path(expr: ast.expr, module: _Module) -> bool:
    """¿`expr` arma una ruta a metadata.json?"""
    return any(_names_metadata_path(node, module) for node in ast.walk(expr))


def _is_path_expression(expr: ast.expr, module: _Module) -> bool:
    """¿`expr` ES una ruta a metadata.json (no un dato leído de ella)? Para
    reconocer los helpers que la devuelven (`return vault / sid / META`)."""
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Div):
        return _is_metadata_path(expr, module)
    if isinstance(expr, ast.Name):
        return expr.id in module.consts or bool(_META_NAME.search(expr.id))
    if isinstance(expr, ast.IfExp):
        return _is_path_expression(expr.body, module) or _is_path_expression(expr.orelse, module)
    if isinstance(expr, ast.Call):
        if _calls_path_helper(expr, module):
            return True
        if _callee_name(expr) == "Path":
            return any(_is_metadata_path(arg, module) for arg in expr.args)
    return False


def _write_primitive(call: ast.Call, module: _Module) -> str | None:
    """¿La llamada escribe un archivo? (`write_text`, `open('w')`, `json.dump`,
    `atomic_write_json` y sus alias, `os.replace`, `Path.replace(destino)`,
    `os.fdopen(.., 'w')`)."""
    func = call.func
    if isinstance(func, ast.Attribute):
        owner = func.value.id if isinstance(func.value, ast.Name) else None
        if func.attr in ("write_text", "write_bytes"):
            return func.attr
        if func.attr == "dump" and owner in module.json_mods:
            return "json.dump"
        if func.attr in ("replace", "rename") and owner in module.os_mods:
            return f"os.{func.attr}"
        if func.attr == "replace" and len(call.args) == 1 and not call.keywords and owner not in module.os_mods:
            return "Path.replace"
        if func.attr == "fdopen" and _WRITE_MODE.search(_mode(call, 1) or ""):
            return "os.fdopen(w)"
        if func.attr == "open" and _WRITE_MODE.search(_mode(call, 0) or ""):
            return "open(w)"
        if func.attr in module.awj:
            return "atomic_write_json"
    if isinstance(func, ast.Name):
        if func.id == "open" and _WRITE_MODE.search(_mode(call, 1) or ""):
            return "open(w)"
        if func.id in module.awj:
            return "atomic_write_json"
        if func.id in module.json_dumps:
            return "json.dump"
    return None


def _is_store_like_write(call: ast.Call) -> bool:
    """`<algo>.write(session_id, data)` (2 posicionales o esas llaves): el
    reemplazo entero de un documento de sesión, se llame como se llame el
    receptor (`self._meta`, `metadata_port`, `store`...)."""
    func = call.func
    if not (isinstance(func, ast.Attribute) and func.attr == "write"):
        return False
    positional = [a for a in call.args if not isinstance(a, ast.Starred)]
    keywords = {k.arg for k in call.keywords}
    return len(positional) == 2 or bool(keywords & _STORE_WRITE_KWARGS)


def _resolve_imports(module: _Module, helpers: dict[str, _Module]) -> None:
    """Helpers importados de otros módulos de `src/` (`from x import _meta`)."""
    for local, (source_module, name) in module.imported.items():
        other = helpers.get(source_module)
        if other is None:
            continue
        if name in other.known_functions:
            module.known_functions.add(local)
        if name in other.path_helpers:
            module.path_helpers.add(local)
        if name in other.writer_helpers:
            module.writer_helpers.add(local)


def _scan_module(module: _Module) -> set[str]:
    found: set[str] = set()
    rel = module.rel
    for name, scope in _scopes(module.tree):
        nodes = _own_nodes(scope)
        mentions = "metadata" in name.lower() or any(_names_metadata_path(n, module) for n in nodes)
        for node in nodes:
            if not isinstance(node, ast.Call):
                continue
            if _is_store_like_write(node) and (
                module.mentions_meta or _STORE_RECEIVER.search(ast.unparse(node.func.value))
            ):
                found.add(f"{rel}::{name} (.write de un documento de sesión)")
            primitive = _write_primitive(node, module)
            if primitive and mentions:
                found.add(f"{rel}::{name} ({primitive})")
            callee = _callee_name(node)
            writes = (
                primitive is not None
                or callee in module.writer_helpers
                or (callee not in module.known_functions and bool(_WRITER_NAME.search(callee)))
            )
            if writes and any(
                _is_metadata_path(arg, module) for arg in [*node.args, *(k.value for k in node.keywords)]
            ):
                found.add(f"{rel}::{name} ({callee} sobre metadata.json)")
    return found


def scan_source(source: str, rel: str) -> set[str]:
    """`ruta::función (cómo)` de cada escritura de `metadata.json` fuera del store."""
    return _scan_module(_analyze(source, rel))


@lru_cache(maxsize=1)
def scan_tree() -> frozenset[str]:
    modules: dict[str, _Module] = {}
    for path in sorted(SRC_ROOT.rglob("*.py")):
        rel = path.relative_to(HUB_ROOT).as_posix()
        modules[_module_name(rel)] = _analyze(path.read_text(encoding="utf-8"), rel)
    found: set[str] = set()
    for module in modules.values():
        if module.rel == STATE_MODULE:
            continue
        _resolve_imports(module, modules)
        found |= _scan_module(module)
    return frozenset(found)


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
    # Revisión del PR #393 (D4): variantes que el gate no veía.
    "receiver_sin_store": '''
class Tool:
    def run(self, sid, data):
        self._meta.write(sid, data)
''',
    "store_write_kwargs": '''
class Tool:
    def run(self, sid, data):
        self._store.write(session_id=sid, data=data)
''',
    "helper_generico": '''
def _save(path, data):
    path.write_text(json.dumps(data))

def persist(vault, sid, data):
    _save(vault / sid / "metadata.json", data)
''',
    "constante_modulo": '''
META = "metadata.json"

def persist(vault, sid, data):
    (vault / sid / META).write_text(json.dumps(data))
''',
    "path_desde_funcion": '''
def _meta(vault, sid):
    return vault / sid / "metadata.json"

def persist(vault, sid, data):
    _meta(vault, sid).write_text(json.dumps(data))
''',
    "port_write": '''
async def handler(metadata_port, sid, data):
    metadata_port.write(sid, data)
''',
    "atomic_alias": '''
from src.platform.state import atomic_write_json as _awj

def persist(session_dir, data):
    _awj(session_dir / "metadata.json", data)
''',
    "tmp_replace_otro_nombre": '''
def persist(session_dir, data):
    target = session_dir / "metadata.json"
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(target)
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


def _save_record(metadata_file, record):
    FilesystemMetadataStore(metadata_file.parent.parent).update(
        metadata_file.parent.name, lambda d: {**d, "r": record}
    )


def retry(vault, sid, record):
    _save_record(vault / sid / "metadata.json", record)
'''
    assert scan_source(source, "src/sample_ok.py") == set()


def test_unrelated_two_argument_writes_elsewhere_are_not_flagged() -> None:
    source = '''
def archive(zf, path, arcname):
    zf.write(path, arcname)
'''
    assert scan_source(source, "src/sample_zip.py") == set()


def test_the_gate_scans_the_real_tree() -> None:
    assert len(list(SRC_ROOT.rglob("*.py"))) > 100
