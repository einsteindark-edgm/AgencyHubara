"""Las rutas que el gateway pide y las que el backend le autoriza son LA MISMA lista.

Viven en dos paquetes que no se importan entre sí: el catálogo del gateway
(`hubara_gateway/catalog.py`) y la regla M2M del backend
(`hubara_agency/src/platform/auth.py`, `_M2M_SCOPE_RULES`). Si una tool nueva no entra a la
regla, el backend le responde 403; si la regla autoriza de más (otra ruta, otro método,
otro scope), el M2M alcanza lo que el gateway no usa.

El test lee la regla del backend con `ast` (no la importa: el backend no está en este
entorno) y exige igualdad exacta: un solo scope, solo `GET`, y los mismos patrones.
Si la regla cambia de forma, falla con un mensaje claro en vez de pasar en falso.
"""

import ast
import re
from pathlib import Path

from hubara_gateway.catalog import TOOLS

AUTH_PY = Path(__file__).resolve().parents[2] / "hubara_agency/src/platform/auth.py"
SCOPE = "hubara-api/ads.read"
SEGMENT = "[^/]+"


def _module_constant(tree: ast.Module, name: str) -> ast.expr:
    for node in tree.body:
        target = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        elif isinstance(node, ast.AnnAssign):
            target = node.target
        if isinstance(target, ast.Name) and target.id == name and node.value is not None:
            return node.value
    raise AssertionError(f"no encuentro `{name}` a nivel de módulo en {AUTH_PY}")


def _pattern_text(node: ast.expr, segment: str) -> str:
    """Texto de un patrón: literal `r"..."` o `rf"...{_SEGMENT}..."`."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for piece in node.values:
            if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                parts.append(piece.value)
            elif isinstance(piece, ast.FormattedValue) and isinstance(piece.value, ast.Name) and piece.value.id == "_SEGMENT":
                parts.append(segment)
            else:
                raise AssertionError(f"patrón con una interpolación que este test no entiende: {ast.dump(piece)}")
        return "".join(parts)
    raise AssertionError(f"patrón que no es un literal de texto: {ast.dump(node)}")


def backend_rules(source: str) -> dict[str, set[tuple[str, str]]]:
    """`{scope: {(método, patrón)}}` tal como lo declara `_M2M_SCOPE_RULES`."""
    tree = ast.parse(source)
    segment = ast.literal_eval(_module_constant(tree, "_SEGMENT"))
    rules = _module_constant(tree, "_M2M_SCOPE_RULES")
    assert isinstance(rules, ast.Dict), "`_M2M_SCOPE_RULES` ya no es un dict literal: actualizá este test"

    out: dict[str, set[tuple[str, str]]] = {}
    for key, value in zip(rules.keys, rules.values, strict=True):
        scope = ast.literal_eval(key)  # type: ignore[arg-type]
        # Forma esperada: tuple(("GET", re.compile(pattern)) for pattern in (<literales>))
        shape_ok = (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "tuple"
            and len(value.args) == 1
            and isinstance(value.args[0], ast.GeneratorExp)
            and len(value.args[0].generators) == 1
            and isinstance(value.args[0].elt, ast.Tuple)
            and len(value.args[0].elt.elts) == 2
            and isinstance(value.args[0].generators[0].iter, ast.Tuple | ast.List)
            and not value.args[0].generators[0].ifs
        )
        assert shape_ok, f"la regla de `{scope}` cambió de forma: actualizá este test para leerla"
        generator = value.args[0]
        method = ast.literal_eval(generator.elt.elts[0])
        out[scope] = {(method, _pattern_text(p, segment)) for p in generator.generators[0].iter.elts}
    return out


def _gateway_routes() -> set[tuple[str, str]]:
    return {("GET", re.sub(r"\{[a-z_]+\}", SEGMENT, spec.path)) for spec in TOOLS.values()}


def test_backend_m2m_rule_authorizes_exactly_the_gateway_catalog() -> None:
    rules = backend_rules(AUTH_PY.read_text(encoding="utf-8"))
    gateway = _gateway_routes()

    assert len(gateway) == len(TOOLS) == 11
    assert set(rules) == {SCOPE}, f"el backend declara otros scopes M2M: {sorted(set(rules) - {SCOPE})}"
    backend = rules[SCOPE]
    assert backend == gateway, (
        f"solo en el backend: {sorted(backend - gateway)} · solo en el gateway: {sorted(gateway - backend)} — "
        "una tool nueva lleva su ruta en hubara_agency/src/platform/auth.py (y su test en test_api_auth_m2m.py)"
    )


# ── El lector caza las formas de «autoriza de más» (caso negativo primero) ──

_SAMPLE = '''
_SEGMENT = r"[^/]+"
_M2M_SCOPE_RULES: dict = {
    "hubara-api/ads.read": tuple(
        ("GET", re.compile(pattern))
        for pattern in (
            r"/api/ads/campaigns",
            rf"/api/ads/campaigns/{_SEGMENT}/adsets",
        )
    ),
}
'''


def test_reader_sees_extra_patterns_whatever_their_spelling() -> None:
    base = backend_rules(_SAMPLE)[SCOPE]
    assert base == {("GET", "/api/ads/campaigns"), ("GET", "/api/ads/campaigns/[^/]+/adsets")}

    for extra in ('r"/api/orders/.*"', "r'/api/ads/meta/insights'", '"/api/ads/events"', 'r"/api/.*"'):
        mutated = _SAMPLE.replace('r"/api/ads/campaigns",', f'r"/api/ads/campaigns",\n            {extra},')
        assert len(backend_rules(mutated)[SCOPE]) == 3, extra


def test_reader_sees_other_methods_and_other_scopes() -> None:
    post = backend_rules(_SAMPLE.replace('("GET", re.compile(pattern))', '("POST", re.compile(pattern))'))
    assert {method for method, _ in post[SCOPE]} == {"POST"}

    two = _SAMPLE.replace("\n}\n", '\n    "hubara-api/ads.write": tuple(("POST", re.compile(p)) for p in (r"/api/ads/x",)),\n}\n')
    assert set(backend_rules(two)) == {SCOPE, "hubara-api/ads.write"}
