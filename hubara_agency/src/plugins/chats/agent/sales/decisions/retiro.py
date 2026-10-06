"""Piezas en retiro: lo que el motor de decisiones dejó sin uso (`retiro.yaml`).

El motor (#372) y su laboratorio dejaron código que producción ya no corre:
las capacidades escritas como clases, funciones con las que `main` decidía en
el lugar, el turno por perfil… No se borra a ciegas: cada pieza lleva un
testigo, y se borra cuando el testigo dice que nadie la usó en los
`observar_dias` del catálogo.

- `@en_retiro("id")` sobre una función o una clase. En la clase cuenta lo que
  decide (`rule`, `ask`, `decide`, `floor`, `same`), no construirla: las
  instancias globales de cada módulo (`PERSONA`, `RELEVO`…) nacen al importar.
  `usado("id")` marca un camino dentro de una función viva (`turn_of` con un
  perfil sin paquete).
- Cada uso deja UNA línea por pieza, proceso y día en
  `<vault>/_retiro/usos.jsonl`, con quién la llamó, y el evento
  `decisions.retiro_usado`. Nunca los argumentos: ni un dato del cliente.
- `observar()` (lo llama `registry.warm_up` en la API y los workers de
  ventas) anota en `<vault>/_retiro/observando.json` desde qué día se observa
  cada pieza; la que entra después cuenta desde el arranque que la ve.
- `informe()` y `python -m src.plugins.chats.agent.sales.decisions.retiro`:
  por pieza, sus usos y si ya se puede borrar.

Nunca falla: anotar no frena a quien llamó. Sin Temporal: lo importan tools,
use cases y activities.
"""
from __future__ import annotations

import functools
import inspect
import json
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, TypeVar

import structlog
import yaml

__all__ = ["CATALOGO", "Estado", "Pieza", "en_retiro", "informe", "main", "observar", "piezas", "reset", "usado"]

logger = structlog.get_logger()

CATALOGO = Path(__file__).with_name("retiro.yaml")
FOLDER = "_retiro"
USES = "usos.jsonl"
OBSERVED = "observando.json"
#: Lo que hace una capacidad de Python cuando decide.
_DECIDES = ("rule", "ask", "decide", "floor", "same")
_THIS = Path(__file__).resolve()

#: (pieza, detalle, día) ya anotados por este proceso.
_seen: set[tuple[str, str, str]] = set()

T = TypeVar("T")


@dataclass(frozen=True)
class Pieza:
    id: str
    grupo: str
    #: `módulo:atributo`.
    simbolo: str
    #: `decorador` (`@en_retiro`) o `en_linea` (`usado(...)` dentro del símbolo).
    marca: str = "decorador"


@dataclass(frozen=True)
class Estado:
    pieza: str
    grupo: str
    usos: int
    ultimo_uso: str | None
    #: Quién la llamó en su último uso (archivo:línea, de adentro hacia afuera).
    donde: tuple[str, ...]
    #: Día desde el que se observa; None = ningún proceso la ha observado.
    desde: str | None
    veredicto: str
    #: Los `detalle` con que se usó (p. ej. qué perfil viejo), sin repetir.
    detalles: tuple[str, ...] = ()


# ── El testigo ──────────────────────────────────────────────────────────────


def en_retiro(pieza: str) -> Callable[[T], T]:
    """Marca una función o una clase como pieza en retiro (ver el módulo)."""

    def mark(obj: Any) -> Any:
        if isinstance(obj, type):
            for name in _DECIDES:
                raw = obj.__dict__.get(name)
                if isinstance(raw, staticmethod | classmethod):
                    continue
                method = raw if raw is not None else getattr(obj, name, None)
                if callable(method):
                    setattr(obj, name, _traced(method, pieza))
        else:
            obj = _traced(obj, pieza)
        obj.__retiro__ = pieza
        return obj

    return mark


def _traced(fn: Callable[..., Any], pieza: str) -> Callable[..., Any]:
    # Un método heredado de otra pieza se anota con el nombre de ESTA.
    while getattr(fn, "__retiro__", None) is not None and hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def traced_async(*args: Any, **kwargs: Any) -> Any:
            usado(pieza)
            return await fn(*args, **kwargs)

        traced: Any = traced_async
    else:

        @functools.wraps(fn)
        def traced_sync(*args: Any, **kwargs: Any) -> Any:
            usado(pieza)
            return fn(*args, **kwargs)

        traced = traced_sync
    traced.__retiro__ = pieza
    return traced


def usado(pieza: str, *, detalle: str = "") -> None:
    """Anota que corrió `pieza` (una vez por proceso, por día y por `detalle`).
    Nunca lanza."""
    try:
        day = _today().isoformat()
        key = (pieza, detalle, day)
        if key in _seen:
            return
        _seen.add(key)
        where = _callers()
        logger.warning("decisions.retiro_usado", pieza=pieza, detalle=detalle or None, donde=list(where))
        line = {
            "pieza": pieza,
            "detalle": detalle,
            "dia": day,
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "proceso": _process(),
            "donde": list(where),
        }
        folder = _vault(None) / FOLDER
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / USES).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 — el testigo nunca frena a quien llamó
        logger.debug("decisions.retiro_failed", pieza=pieza, error=repr(exc)[:200])


def reset() -> None:
    """Olvida lo anotado por este proceso (pruebas)."""
    _seen.clear()


def _today() -> date:
    return datetime.now(UTC).date()


def _vault(vault_dir: Path | str | None) -> Path:
    """El vault de este proceso: la misma variable y el mismo default que
    `src.sdk.runtime.WORKSPACE_VAULT_DIR`. No se importa de ahí porque
    `runtime` arrastra Temporal y a este módulo lo importan las tools
    (contrato `tools-no-temporal`). Las pruebas lo aíslan con la variable."""
    if vault_dir is not None:
        return Path(vault_dir)
    return Path(os.environ.get("WORKSPACE_VAULT_DIR", "./hubara_vault")).resolve()


def _callers(limit: int = 3) -> tuple[str, ...]:
    found: list[str] = []
    frame = sys._getframe(1)
    while frame is not None and len(found) < limit:
        if Path(frame.f_code.co_filename).resolve() != _THIS:
            found.append(f"{_short(frame.f_code.co_filename)}:{frame.f_lineno}")
        frame = frame.f_back
    return tuple(found)


def _short(path: str) -> str:
    cut = max(path.rfind("/src/"), path.rfind("/tests/"))
    return path[cut + 1 :] if cut >= 0 else Path(path).name


def _process() -> str:
    import socket

    program = _short(sys.argv[0]) if sys.argv and sys.argv[0] else "python"
    return f"{program}@{socket.gethostname()}"


# ── El catálogo y lo observado ──────────────────────────────────────────────


def _catalog(catalogo: Path | None) -> dict[str, Any]:
    return yaml.safe_load((catalogo or CATALOGO).read_text(encoding="utf-8")) or {}


def piezas(catalogo: Path | None = None) -> dict[str, Pieza]:
    """Las piezas del catálogo (`retiro.yaml`), por id."""
    found: dict[str, Pieza] = {}
    for grupo, data in (_catalog(catalogo).get("grupos") or {}).items():
        for pid, value in ((data or {}).get("piezas") or {}).items():
            if isinstance(value, str):
                found[pid] = Pieza(pid, grupo, value)
            else:
                found[pid] = Pieza(pid, grupo, str(value["simbolo"]), str(value.get("marca", "decorador")))
    return found


def observar(vault_dir: Path | str | None = None, *, hoy: date | None = None, catalogo: Path | None = None) -> None:
    """Anota desde qué día se observa cada pieza; la que ya tenía día lo
    conserva. La llaman la API y los workers de ventas al arrancar. Nunca
    lanza."""
    try:
        day = (hoy or _today()).isoformat()
        path = _vault(vault_dir) / FOLDER / OBSERVED
        known = _read_json(path)
        news = {pid: day for pid in piezas(catalogo) if pid not in known}
        if not news:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{OBSERVED}.{os.getpid()}")
        tmp.write_text(json.dumps({**known, **news}, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
        logger.info("decisions.retiro_observing", nuevas=len(news), desde=day)
    except Exception as exc:  # noqa: BLE001 — observar nunca tumba el arranque
        logger.warning("decisions.retiro_failed", error=repr(exc)[:200])


def informe(vault_dir: Path | str | None = None, *, hoy: date | None = None, catalogo: Path | None = None) -> list[Estado]:
    """Por pieza: cuántas veces se usó, dónde la última, desde cuándo se
    observa y el veredicto (`se puede borrar`, `observando: faltan N días`,
    `en uso` o `sin observar`)."""
    today = hoy or _today()
    need = int(_catalog(catalogo).get("observar_dias", 30))
    folder = _vault(vault_dir) / FOLDER
    since = _read_json(folder / OBSERVED)
    uses: dict[str, list[dict[str, Any]]] = {}
    for line in _read_lines(folder / USES):
        uses.setdefault(str(line.get("pieza")), []).append(line)
    rows = []
    for piece in piezas(catalogo).values():
        seen = sorted(uses.get(piece.id, []), key=lambda u: str(u.get("at", "")))
        last = seen[-1] if seen else {}
        start = since.get(piece.id)
        rows.append(Estado(
            pieza=piece.id,
            grupo=piece.grupo,
            usos=len(seen),
            ultimo_uso=last.get("dia"),
            donde=tuple(last.get("donde") or ()),
            desde=start,
            veredicto=_verdict(len(seen), start, today, need),
            detalles=tuple(dict.fromkeys(str(u["detalle"]) for u in seen if u.get("detalle"))),
        ))
    return rows


def _verdict(uses: int, start: str | None, today: date, need: int) -> str:
    if uses:
        return "en uso"
    if start is None:
        return "sin observar"
    left = need - (today - date.fromisoformat(start)).days
    if left <= 0:
        return "se puede borrar"
    return f"observando: falta {left} día" if left == 1 else f"observando: faltan {left} días"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_lines(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    lines = []
    for raw in text.splitlines():
        try:
            line = json.loads(raw)
        except ValueError:
            continue
        if isinstance(line, dict):
            lines.append(line)
    return lines


# ── El informe en la terminal ───────────────────────────────────────────────


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="retiro", description="Piezas en retiro del motor de decisiones: cuáles ya se pueden borrar."
    )
    parser.add_argument("--vault", type=Path, default=None, help="carpeta del vault (por defecto, la del proceso)")
    args = parser.parse_args(argv)
    catalog = _catalog(None)
    rows = informe(args.vault)
    print(
        f"Piezas en retiro ({_vault(args.vault) / FOLDER}). Se borran tras "
        f"{catalog.get('observar_dias', 30)} días observadas sin usos."
    )
    groups: dict[str, list[Estado]] = {}
    for row in rows:
        groups.setdefault(row.grupo, []).append(row)
    for group, items in groups.items():
        ready = sum(row.veredicto == "se puede borrar" for row in items)
        print(f"\n{group}: {ready} de {len(items)} se pueden borrar")
        print(f"  borrar cuando: {(catalog['grupos'][group] or {}).get('borrar_cuando', '')}")
        width = max(len(row.pieza) for row in items)
        for row in items:
            if row.usos:
                count = f"{row.usos} uso{'' if row.usos == 1 else 's'}"
                if row.detalles:
                    count += f" ({', '.join(row.detalles)})"
                detail = f"{count}; el último el {row.ultimo_uso} desde {', '.join(row.donde) or '?'}"
            elif row.desde:
                detail = f"observada desde el {row.desde}"
            else:
                detail = "ningún proceso la ha observado"
            print(f"  {row.pieza:<{width}}  {row.veredicto:<28}  {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
