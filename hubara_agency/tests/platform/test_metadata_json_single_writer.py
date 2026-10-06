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

Lo mismo vale para las copias de la recuperación automática del store
(decisión del operador, 2026-10-06): `metadata.json.prev` (la última copia
buena) y `metadata.json.damaged-<ms>` (un dañado apartado) los escribe solo
`state.py`.

Qué ve (análisis estático, AST): `write_text`/`write_bytes`, `open('w')`,
`os.open` con banderas de escritura, `json.dump`, `atomic_write_json` (y sus
alias), `os.replace`/`os.rename`/`os.link`/`Path.replace`,
`.write(session_id, data)` de un store, y
helpers que escriben (también importados de otro módulo de `src/`, o llamados
como `modulo.funcion`), con la ruta armada a mano, en una constante (del mismo
módulo o importada), devuelta por otra función o guardada en `self.<attr>`.
No marca `zipfile`/`tarfile` (`.write` LEE el archivo) ni «Meta» la empresa.

Límites conocidos (no se detectan; hacerlos es más caro que su riesgo):
`shutil.move`/`copy`/`copyfile` hacia `metadata.json`; el nombre armado con
f-string, `+`, `%` o `format`; despacho dinámico (`getattr(store, "write")`,
`importlib`); `os.write` sobre un descriptor abierto en otro lado;
`subprocess` (`cp`, `mv`); escritores fuera de `src/` (scripts de operador).

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
_META_NAME = re.compile(r"meta(?:data)?_?(?:file|path)(?:_?name)?$", re.IGNORECASE)
#: «metadata», no «meta»: Meta (la empresa) aparece en medio `src/`.
_META_WORD = re.compile(r"metadata", re.IGNORECASE)
_WRITER_NAME = re.compile(r"write|dump|save|persist", re.IGNORECASE)
_STORE_RECEIVER = re.compile(r"store|metadata|(?:^|[._])meta$", re.IGNORECASE)
_WRITE_MODE = re.compile(r"[wax+]")
_STORE_WRITE_KWARGS = {"session_id", "data"}
_OS_WRITE_FLAGS = {"O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND"}
#: Archivos comprimidos: su `.write(ruta, nombre)` LEE la ruta.
_ARCHIVE_TYPE = re.compile(r"\b(?:ZipFile|TarFile)\b|\btarfile\.open\b")
_ARCHIVE_NAME = re.compile(r"^(?:zf|zipf|zip|zip_?file|tf|tar|tar_?file|archive)$", re.IGNORECASE)


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


def _scopes(tree: ast.Module) -> Iterator[tuple[str, ast.AST, str | None]]:
    """(`nombre calificado`, nodo, clase que la contiene) de cada función del
    módulo, y el módulo."""
    yield "<module>", tree, None

    def walk(node: ast.AST, prefix: str, cls: str | None) -> Iterator[tuple[str, ast.AST, str | None]]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                yield name, child, cls
                yield from walk(child, f"{name}.", cls)
            elif isinstance(child, ast.ClassDef):
                yield from walk(child, f"{prefix}{child.name}.", child.name)
            else:
                yield from walk(child, prefix, cls)

    yield from walk(tree, "", None)


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
    #: `import a.b as c` → c: "a.b".
    import_aliases: dict[str, str] = field(default_factory=dict)
    #: alias locales de otros módulos de `src/` (`from src.platform import fsutil`).
    module_aliases: dict[str, _Module] = field(default_factory=dict)
    #: clase → atributos de `self` que guardan una ruta a metadata.json.
    self_attr_paths: dict[str, set[str]] = field(default_factory=dict)
    #: (función, sus parámetros, sus llamadas) — para los envoltorios.
    function_calls: list[tuple[str, set[str], list[ast.Call]]] = field(default_factory=list)
    #: ¿el módulo nombra algo «metadata»? (identificadores o textos)
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
                elif alias.asname:
                    module.import_aliases[alias.asname] = alias.name
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
        calls = [n for n in _own_nodes(fn) if isinstance(n, ast.Call)]
        params = {a.arg for a in [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]}
        module.function_calls.append((fn.name, params, calls))
        if any(_write_primitive(call, module) for call in calls):
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
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        for node in ast.walk(cls):
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            else:
                continue
            if not _is_path_expression(value, module):
                continue
            for target in targets:
                if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
                    module.self_attr_paths.setdefault(cls.name, set()).add(target.attr)
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


def _owner(call: ast.Call) -> str | None:
    """`x` en `x.f(...)` (solo un nombre)."""
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return func.value.id
    return None


def _calls_path_helper(call: ast.Call, module: _Module) -> bool:
    other = module.module_aliases.get(_owner(call) or "")
    if other is not None:
        return _callee_name(call) in other.path_helpers
    return _callee_name(call) in module.path_helpers


def _names_metadata_path(node: ast.AST, module: _Module, self_attrs: set[str] = frozenset()) -> bool:
    """Un nodo que nombra una ruta a metadata.json: el literal (fuera de
    docstrings), la constante de módulo (o importada), un nombre
    `metadata_file`/`meta_path`/`METADATA_FILENAME`, la llamada a un helper
    que la devuelve o un `self.<attr>` que la guardó."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return _METADATA_FILE in node.value and id(node) not in module.docs
    if isinstance(node, ast.Name):
        return node.id in module.consts or bool(_META_NAME.search(node.id))
    if isinstance(node, ast.arg):
        return bool(_META_NAME.search(node.arg))
    if isinstance(node, ast.Call):
        return _calls_path_helper(node, module)
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
        return node.attr in self_attrs
    return False


def _is_metadata_path(expr: ast.expr, module: _Module, self_attrs: set[str] = frozenset()) -> bool:
    """¿`expr` arma una ruta a metadata.json?"""
    return any(_names_metadata_path(node, module, self_attrs) for node in ast.walk(expr))


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
        if func.attr in ("replace", "rename", "link", "symlink") and owner in module.os_mods:
            return f"os.{func.attr}"
        if func.attr == "open" and owner in module.os_mods:
            flags = {
                node.attr if isinstance(node, ast.Attribute) else node.id
                for arg in [*call.args[1:], *(k.value for k in call.keywords if k.arg == "flags")]
                for node in ast.walk(arg)
                if isinstance(node, (ast.Attribute, ast.Name))
            }
            return "os.open(w)" if flags & _OS_WRITE_FLAGS else None
        if (
            func.attr == "replace"
            and len(call.args) == 1
            and not call.keywords
            and owner not in module.os_mods
            # `df.replace({...})`, `s.replace("x")`: no es mover un archivo.
            and not isinstance(call.args[0], (ast.Dict, ast.List, ast.Set, ast.Tuple, ast.Constant))
        ):
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


def _is_store_like_write(call: ast.Call, module: _Module) -> bool:
    """`<algo>.write(session_id, data)` (2 posicionales o esas llaves): el
    reemplazo entero de un documento de sesión, se llame como se llame el
    receptor (`self._meta`, `metadata_port`, `store`...). No lo son
    `os.write(fd, bytes)`, un `.write("texto", ...)` de log/stream ni
    `.write(<ruta>, ...)` (eso es escribir A una ruta: lo ve la otra regla)."""
    func = call.func
    if not (isinstance(func, ast.Attribute) and func.attr == "write"):
        return False
    if _owner(call) in module.os_mods:
        return False
    positional = [a for a in call.args if not isinstance(a, ast.Starred)]
    if positional and isinstance(positional[0], ast.Constant):
        return False
    if positional and _is_metadata_path(positional[0], module):
        return False
    keywords = {k.arg for k in call.keywords}
    return len(positional) == 2 or bool(keywords & _STORE_WRITE_KWARGS)


def _archives(scope: ast.AST, nodes: list[ast.AST]) -> set[str]:
    """Nombres que en esta función son un zip/tar (anotados, abiertos en un
    `with` o asignados desde `ZipFile(...)`/`tarfile.open(...)`): su `.write`
    LEE el archivo para comprimirlo."""
    names: set[str] = set()
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for arg in [*scope.args.posonlyargs, *scope.args.args, *scope.args.kwonlyargs]:
            if arg.annotation is not None and _ARCHIVE_TYPE.search(ast.unparse(arg.annotation)):
                names.add(arg.arg)
    for node in nodes:
        if isinstance(node, ast.withitem) and isinstance(node.optional_vars, ast.Name):
            if _ARCHIVE_TYPE.search(ast.unparse(node.context_expr)):
                names.add(node.optional_vars.id)
        elif isinstance(node, ast.Assign) and _ARCHIVE_TYPE.search(ast.unparse(node.value)):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.Name) and _ARCHIVE_NAME.match(node.id):
            names.add(node.id)
    return names


def _resolve_imports(module: _Module, helpers: dict[str, _Module]) -> None:
    """Lo importado de otros módulos de `src/`: helpers y constantes (`from x
    import _meta`) y módulos enteros (`from src.platform import fsutil`,
    `import a.b as c`)."""
    for local, (source_module, name) in module.imported.items():
        submodule = helpers.get(f"{source_module}.{name}")
        if submodule is not None:
            module.module_aliases[local] = submodule
            continue
        other = helpers.get(source_module)
        if other is None:
            continue
        if name in other.known_functions:
            module.known_functions.add(local)
        if name in other.path_helpers:
            module.path_helpers.add(local)
        if name in other.writer_helpers:
            module.writer_helpers.add(local)
        if name in other.consts:
            module.consts.add(local)
    for local, dotted in module.import_aliases.items():
        if dotted in helpers:
            module.module_aliases[local] = helpers[dotted]


def _calls_writer(call: ast.Call, module: _Module) -> bool:
    """¿La llamada es a un helper que escribe? (de este módulo, importado o
    `modulo.funcion` de otro módulo de `src/`)."""
    other = module.module_aliases.get(_owner(call) or "")
    if other is not None:
        return _callee_name(call) in other.writer_helpers
    return _callee_name(call) in module.writer_helpers


def _is_known(call: ast.Call, module: _Module) -> bool:
    other = module.module_aliases.get(_owner(call) or "")
    if other is not None:
        return _callee_name(call) in other.known_functions
    return _callee_name(call) in module.known_functions


def _propagate_writers(modules: dict[str, _Module]) -> None:
    """Envoltorios: una función que le pasa SU parámetro como ruta a un helper
    que escribe también escribe (`def _keep(path, d): put_json(path, d)`),
    aunque el helper venga de otro módulo. Punto fijo sobre todo `src/`."""
    changed = True
    while changed:
        changed = False
        for module in modules.values():
            _resolve_imports(module, modules)
            for name, params, calls in module.function_calls:
                if name in module.writer_helpers:
                    continue
                for call in calls:
                    first = call.args[0] if call.args else None
                    if isinstance(first, ast.Name) and first.id in params and _calls_writer(call, module):
                        module.writer_helpers.add(name)
                        changed = True
                        break


def _scan_module(module: _Module) -> set[str]:
    found: set[str] = set()
    rel = module.rel
    for name, scope, cls in _scopes(module.tree):
        nodes = _own_nodes(scope)
        self_attrs = module.self_attr_paths.get(cls, set()) if cls else set()
        archives = _archives(scope, nodes)
        mentions = "metadata" in name.lower() or any(_names_metadata_path(n, module, self_attrs) for n in nodes)
        for node in nodes:
            if not isinstance(node, ast.Call) or _owner(node) in archives:
                continue
            if _is_store_like_write(node, module) and (
                module.mentions_meta or _STORE_RECEIVER.search(ast.unparse(node.func.value))
            ):
                found.add(f"{rel}::{name} (.write de un documento de sesión)")
            primitive = _write_primitive(node, module)
            if primitive and mentions:
                found.add(f"{rel}::{name} ({primitive})")
            callee = _callee_name(node)
            writes = (
                primitive is not None
                or _calls_writer(node, module)
                or (not _is_known(node, module) and bool(_WRITER_NAME.search(callee)))
            )
            if writes and any(
                _is_metadata_path(arg, module, self_attrs) for arg in [*node.args, *(k.value for k in node.keywords)]
            ):
                found.add(f"{rel}::{name} ({callee} sobre metadata.json)")
    return found


def scan_source(source: str, rel: str) -> set[str]:
    """`ruta::función (cómo)` de cada escritura de `metadata.json` fuera del store."""
    return scan_sources({rel: source})


def scan_sources(sources: dict[str, str]) -> set[str]:
    """Lo mismo para varios módulos a la vez (`ruta relativa → código`): los
    imports entre ellos se resuelven (helpers, constantes)."""
    modules = {_module_name(rel): _analyze(source, rel) for rel, source in sources.items()}
    _propagate_writers(modules)
    found: set[str] = set()
    for module in modules.values():
        if module.rel == STATE_MODULE:
            continue
        found |= _scan_module(module)
    return found


@lru_cache(maxsize=1)
def scan_tree() -> frozenset[str]:
    sources = {
        path.relative_to(HUB_ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(SRC_ROOT.rglob("*.py"))
    }
    return frozenset(scan_sources(sources))


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


# --- segunda revisión del PR #393 (D4) -----------------------------------------
# Más variantes que el gate tiene que ver (las baratas de detectar), y falsos
# positivos que no puede marcar.

_MORE_SAMPLES = {
    "constante_importada": '''
from src.platform.constants import METADATA_FILENAME

def persist(session_dir, data):
    (session_dir / METADATA_FILENAME).write_text(json.dumps(data))
''',
    "ruta_en_self_desde_init": '''
class Tool:
    def __init__(self, session_dir):
        self.target = session_dir / "metadata.json"

    def run(self, data):
        self.target.write_text(json.dumps(data))
''',
    "os_open_para_escribir": '''
import os

def persist(session_dir, data):
    fd = os.open(session_dir / "metadata.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    os.write(fd, json.dumps(data).encode())
''',
}

#: Varios módulos: lo que solo se ve resolviendo los imports entre ellos.
_MULTI_MODULE_SAMPLES = {
    "constante_de_otro_modulo": {
        "src/platform/names.py": 'SESSION_DOC = "metadata.json"\n',
        "src/plugins/x/persist.py": '''
from src.platform.names import SESSION_DOC

def persist(session_dir, data):
    (session_dir / SESSION_DOC).write_text(json.dumps(data))
''',
    },
    "envoltorio_local_de_un_escritor_importado": {
        "src/platform/fsutil.py": '''
def put_json(path, data):
    path.write_text(json.dumps(data))
''',
        "src/plugins/x/persist.py": '''
from src.platform.fsutil import put_json

def _keep(path, data):
    put_json(path, data)

def persist(session_dir, data):
    _keep(session_dir / "metadata.json", data)
''',
    },
    "funcion_de_un_modulo_importado": {
        "src/platform/fsutil.py": '''
def put(path, data):
    path.write_text(json.dumps(data))
''',
        "src/plugins/x/persist.py": '''
from src.platform import fsutil

def persist(session_dir, data):
    fsutil.put(session_dir / "metadata.json", data)
''',
    },
}


def test_the_scanner_catches_the_variants_found_in_the_second_review() -> None:
    for label, source in _MORE_SAMPLES.items():
        assert scan_source(source, f"src/sample_{label}.py"), f"el gate no ve el patrón {label!r}"
    for label, sources in _MULTI_MODULE_SAMPLES.items():
        found = scan_sources(sources)
        assert any(f.startswith("src/plugins/x/persist.py::persist") for f in found), (
            f"el gate no ve el patrón {label!r}: {sorted(found)}"
        )


_NOT_WRITES = {
    # zipfile LEE el archivo para meterlo al zip.
    "zip_anotado": '''
import zipfile

def export(session_dir, zf: zipfile.ZipFile):
    zf.write(session_dir / "metadata.json", "metadata.json")
''',
    "zip_abierto_en_with": '''
import zipfile

def export(meta_path, dest, arcname):
    with zipfile.ZipFile(dest, "w") as bundle:
        bundle.write(meta_path, arcname)
''',
    # «Meta» la empresa no es «metadata».
    "meta_la_empresa": '''
import os

META_KEY = "x"

def ping(fd, payload):
    os.write(fd, payload)
''',
    "un_log_con_dos_argumentos": '''
def report(writer, metadata):
    writer.write("resumen", metadata)
''',
    "replace_de_pandas": '''
def clean(df, metadata_file):
    return df.replace({"a": 1})
''',
}


def test_the_scanner_does_not_flag_what_only_reads_or_is_not_metadata() -> None:
    for label, source in _NOT_WRITES.items():
        assert scan_source(source, f"src/ok_{label}.py") == set(), f"falso positivo: {label!r}"


#: La recuperación automática (decisión del operador, 2026-10-06) guarda la
#: última copia buena en `metadata.json.prev` y aparta un dañado como
#: `metadata.json.damaged-<ms>`: esas copias también las escribe SOLO el store.
_COPY_SAMPLES = {
    "prev_a_mano": '''
def keep_last_good(session_dir, data):
    (session_dir / "metadata.json.prev").write_text(json.dumps(data))
''',
    "damaged_a_mano": '''
import os

def set_aside(session_dir):
    os.replace(session_dir / "metadata.json", session_dir / "metadata.json.damaged-1")
''',
    "enlace_a_prev": '''
import os

def keep_last_good(session_dir):
    os.link(session_dir / "metadata.json", session_dir / "metadata.json.prev")
''',
}


def test_the_copies_of_metadata_are_written_only_by_the_store() -> None:
    for label, source in _COPY_SAMPLES.items():
        assert scan_source(source, f"src/sample_{label}.py"), f"el gate no ve el patrón {label!r}"


def test_the_gate_scans_the_real_tree() -> None:
    assert len(list(SRC_ROOT.rglob("*.py"))) > 100
