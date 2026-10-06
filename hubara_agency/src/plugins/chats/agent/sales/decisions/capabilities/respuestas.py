"""Cómo leen las políticas de las capacidades las respuestas de Jev (noul y
choice) y cómo arman las opciones de un choice sobre una lista cerrada.

Puro y sin Temporal: lo usan las capacidades del ingest, del prompt y de las
tools (vía `guards`).
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from src.plugins.chats.agent.sales.decisions.plan import answer_of, plain

YES_NO: dict[str, str] = {"true": "sí", "false": "no"}

#: Opciones que se suman a toda lista cerrada (diseño v2 §07, mapeos).
AMBIGUO = "ambiguo"
NINGUNO = "ninguno"


def noul_p(result: Any, qid: str) -> float | None:
    """P(sí) de una pregunta noul, o None si Jev no la respondió."""
    p = getattr(answer_of(result, qid), "p", None)
    return float(p) if isinstance(p, (int, float)) and not isinstance(p, bool) else None


def choice_of(result: Any, qid: str) -> tuple[str | None, float]:
    """(opción elegida, su probabilidad) de una pregunta choice; (None, 0) sin respuesta."""
    answer = answer_of(result, qid)
    choice = getattr(answer, "choice", None)
    if not choice:
        return None, 0.0
    probs = dict(getattr(answer, "probs", ()) or ())
    p = probs.get(choice, getattr(answer, "confidence", None))
    return str(choice), float(p) if isinstance(p, (int, float)) and not isinstance(p, bool) else 0.0


def option_keys(values: Iterable[str], *, reserved: Iterable[str] = (AMBIGUO, NINGUNO)) -> dict[str, str]:
    """`{clave: valor}` para un choice sobre una lista cerrada: claves cortas
    sin tildes ni espacios (`"Azul marino"` → `azul_marino`), únicas y que no
    choquen con las opciones reservadas. Jev contesta la clave y el código la
    lleva de vuelta al valor real de la lista."""
    taken = set(reserved)
    keys: dict[str, str] = {}
    for value in values:
        base = re.sub(r"[^a-z0-9]+", "_", plain(str(value))).strip("_") or "opcion"
        key, n = base, 2
        while key in taken:
            key, n = f"{base}_{n}", n + 1
        taken.add(key)
        keys[key] = str(value)
    return keys


def closed_criteria(keys: Mapping[str, str], *, ambiguous: str, none: str) -> dict[str, str]:
    """Las opciones de la lista cerrada más `ambiguo` y `ninguno`."""
    return {**keys, AMBIGUO: ambiguous, NINGUNO: none}
