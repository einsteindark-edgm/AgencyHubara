"""Piezas en retiro (`decisions/retiro.yaml`): lo que el motor dejó sin uso.

Cada pieza lleva un testigo: si algo la corre de verdad, queda una línea en
`<vault>/_retiro/usos.jsonl` (una por pieza, proceso y día, con quién la
llamó) y el evento `decisions.retiro_usado`. El informe dice cuáles ya se
pueden borrar. Estas pruebas cuidan las dos mitades: el testigo anota bien, y
la clasificación no se desactualiza (cada pieza existe, lleva su testigo y
ningún código de producción la llama).
"""
from __future__ import annotations

import ast
import importlib
import inspect
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from structlog.testing import capture_logs

from src.plugins.chats.agent.sales.decisions import registry, retiro

SRC = Path(retiro.__file__).resolve().parents[5]


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SALES_DECISIONS_BUNDLE", raising=False)
    retiro.reset()
    registry.reset()
    yield
    retiro.reset()
    registry.reset()


def _uses(vault: Path) -> list[dict]:
    path = vault / "_retiro" / "usos.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def _today() -> date:
    return datetime.now(UTC).date()


# ── El testigo ──────────────────────────────────────────────────────────────


def test_a_piece_in_retirement_leaves_one_trace_per_day_with_who_called_it(_isolate_vault_dir: Path) -> None:
    @retiro.en_retiro("funcion:de_prueba")
    def vieja(x: int) -> int:
        return x + 1

    with capture_logs() as logs:
        assert vieja(1) == 2
        assert vieja(2) == 3

    [use] = _uses(_isolate_vault_dir)
    assert use["pieza"] == "funcion:de_prueba"
    assert use["dia"] == _today().isoformat()
    assert any("test_decisions_retiro.py" in where for where in use["donde"])
    assert [e["pieza"] for e in logs if e["event"] == "decisions.retiro_usado"] == ["funcion:de_prueba"]


def test_a_class_in_retirement_is_traced_when_it_decides_not_when_it_is_built(_isolate_vault_dir: Path) -> None:
    @retiro.en_retiro("clase:de_prueba")
    class Vieja:
        name = "de_prueba"

        def rule(self, inp: int) -> int:
            return inp

        @staticmethod
        def applies(inp: int) -> bool:
            return True

    vieja = Vieja()  # las instancias globales (`PERSONA = Persona()`) nacen al importar
    assert Vieja.applies(1) is True
    assert _uses(_isolate_vault_dir) == []

    assert vieja.rule(7) == 7
    assert [u["pieza"] for u in _uses(_isolate_vault_dir)] == ["clase:de_prueba"]
    assert type(vieja) is Vieja and Vieja.__retiro__ == "clase:de_prueba"


def test_a_subclass_in_retirement_is_traced_as_itself(_isolate_vault_dir: Path) -> None:
    @retiro.en_retiro("clase:madre")
    class Madre:
        def rule(self, inp: object) -> str:
            return "madre"

    @retiro.en_retiro("clase:hija")
    class Hija(Madre):
        pass

    assert Hija().rule(None) == "madre"
    assert [u["pieza"] for u in _uses(_isolate_vault_dir)] == ["clase:hija"]


def test_the_trace_lands_in_the_vault_of_the_process(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Sin importar `src.sdk.runtime` (arrastra Temporal hasta las tools):
    misma variable y mismo default."""
    from src.platform import config

    assert retiro._vault(None) == Path(config.WORKSPACE_VAULT_DIR)
    monkeypatch.delenv("WORKSPACE_VAULT_DIR")
    monkeypatch.chdir(tmp_path)
    assert retiro._vault(None) == (tmp_path / "hubara_vault").resolve()
    assert 'getenv("WORKSPACE_VAULT_DIR", "./hubara_vault")' in Path(config.__file__).read_text(encoding="utf-8")


def test_the_trace_never_breaks_the_caller(_isolate_vault_dir: Path) -> None:
    (_isolate_vault_dir / "_retiro").write_text("no es una carpeta", encoding="utf-8")

    @retiro.en_retiro("funcion:de_prueba")
    def vieja() -> str:
        return "sigue"

    assert vieja() == "sigue"


# ── El informe ──────────────────────────────────────────────────────────────


def _catalog(tmp_path: Path, *ids: str) -> Path:
    path = tmp_path / f"retiro-{len(ids)}.yaml"
    pieces = {pid: f"modulo:{pid.split(':')[1]}" for pid in ids}
    path.write_text(yaml.safe_dump({"observar_dias": 30, "grupos": {"g": {"que_es": "prueba", "piezas": pieces}}}))
    return path


def test_the_report_says_which_pieces_can_go(_isolate_vault_dir: Path, tmp_path: Path) -> None:
    vault, today = _isolate_vault_dir, _today()
    first = _catalog(tmp_path, "funcion:quieta", "funcion:viva")
    later = _catalog(tmp_path, "funcion:quieta", "funcion:viva", "funcion:nueva")
    retiro.observar(vault, hoy=today - timedelta(days=31), catalogo=first)
    # Una pieza que entra después cuenta sus días desde el arranque que la ve.
    retiro.observar(vault, hoy=today - timedelta(days=10), catalogo=later)
    retiro.usado("funcion:viva", detalle="jev-v4")

    rows = {r.pieza: r for r in retiro.informe(vault, hoy=today, catalogo=later)}
    assert rows["funcion:viva"].detalles == ("jev-v4",)

    assert rows["funcion:quieta"].veredicto == "se puede borrar"
    assert rows["funcion:quieta"].desde == (today - timedelta(days=31)).isoformat()
    assert rows["funcion:nueva"].veredicto == "observando: faltan 20 días"
    assert rows["funcion:viva"].veredicto == "en uso"
    assert (rows["funcion:viva"].usos, rows["funcion:viva"].ultimo_uso) == (1, today.isoformat())
    assert any("test_decisions_retiro.py" in where for where in rows["funcion:viva"].donde)

    empty = retiro.informe(tmp_path / "otro_vault", hoy=today, catalogo=later)
    assert {r.veredicto for r in empty} == {"sin observar"}


def test_the_command_prints_the_report(_isolate_vault_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    retiro.observar(_isolate_vault_dir, hoy=_today() - timedelta(days=40))
    retiro.usado("turno:perfil-sin-paquete", detalle="jev-v4")

    assert retiro.main(["--vault", str(_isolate_vault_dir)]) == 0

    out = capsys.readouterr().out
    assert "clase:compra" in out and "se puede borrar" in out
    assert "1 uso (jev-v4)" in out


def test_the_sales_processes_start_observing_when_they_warm_up(_isolate_vault_dir: Path) -> None:
    registry.warm_up()

    observed = json.loads((_isolate_vault_dir / "_retiro" / "observando.json").read_text(encoding="utf-8"))
    assert set(observed) == set(retiro.piezas())
    assert set(observed.values()) == {_today().isoformat()}


# ── La clasificación no se desactualiza ─────────────────────────────────────


def test_every_piece_in_retirement_exists_and_carries_its_trace() -> None:
    problems = []
    for piece in retiro.piezas().values():
        module, attr = piece.simbolo.split(":")
        found = getattr(importlib.import_module(module), attr, None)
        if found is None:
            problems.append(f"{piece.id}: no existe {piece.simbolo}")
        elif piece.marca == "decorador" and getattr(found, "__retiro__", None) != piece.id:
            problems.append(f"{piece.id}: {piece.simbolo} sin @en_retiro({piece.id!r})")
        elif piece.marca == "en_linea" and f'usado("{piece.id}"' not in inspect.getsource(found):
            problems.append(f'{piece.id}: {piece.simbolo} no llama retiro.usado("{piece.id}")')
    assert problems == []


def test_every_python_capability_class_is_classified() -> None:
    classified = {piece.simbolo for piece in retiro.piezas().values()}
    classes = {f"{type(c).__module__}:{type(c).__name__}" for c in registry.class_capabilities().values()}
    assert sorted(classes - classified) == []


def test_no_production_code_calls_a_piece_in_retirement() -> None:
    """La mitad estática del testigo. Si esto falla, una pieza volvió a
    usarse: o no la llames, o sácala de `retiro.yaml` junto con su testigo."""
    pieces = {p.simbolo.split(":")[1]: p for p in retiro.piezas().values() if p.marca == "decorador"}
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        module = ".".join(path.relative_to(SRC.parent).with_suffix("").parts).removesuffix(".__init__")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.Name):
                names = [node.id]
            elif isinstance(node, ast.Attribute):
                names = [node.attr]
            else:
                continue
            for name in names:
                piece = pieces.get(name)
                if piece is not None and module != piece.simbolo.split(":")[0]:
                    offenders.append(f"{path.relative_to(SRC)}:{node.lineno}: {piece.id}")
    assert offenders == []


def test_an_old_profile_turn_is_traced_and_the_bundle_turn_is_not(_isolate_vault_dir: Path) -> None:
    from src.plugins.chats.agent.sales.decisions.profiles import get_engine_profile
    from src.plugins.chats.agent.sales.decisions.turn import turn_of

    turn_of(get_engine_profile("jev-v5"))
    assert _uses(_isolate_vault_dir) == []

    turn_of(get_engine_profile("jev-v4"))
    [use] = _uses(_isolate_vault_dir)
    assert (use["pieza"], use["detalle"]) == ("turno:perfil-sin-paquete", "jev-v4")
