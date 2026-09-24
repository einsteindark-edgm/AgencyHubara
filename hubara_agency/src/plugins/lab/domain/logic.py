"""Dominio de lab — lógica PURA (sin I/O, sin vendors, sin fastapi).

El plugin lab es la vista del laboratorio de conversaciones (plan
LABORATORIO_CONVERSACIONES_PLAN.md §3.7 y §11). Sus datos son de chats: los
consume por el contrato `lab@v1` (`/api/chats/lab/*`) a través del cast
`api/lab.py`. Acá vive lo único que el cast decide: la ruta del provider y
los parámetros que viajan, con cada segmento validado ANTES de reenviar (una
corrida, una conversación, un episodio o un brazo con otra forma nunca llega
a chats).
"""
from __future__ import annotations

import re
from typing import Any

from src.sdk.labkit import RUN_ID_RE

PROVIDER_PREFIX = "/api/chats/lab"

_SID_RE = re.compile(r"^wa_[A-Za-z0-9_+]{3,40}$")
_EPISODE_RE = re.compile(r"^ep_\d{1,6}$")
_ARM_RE = re.compile(r"^(A0|A1|B|C)$")
_ARMS_RE = re.compile(r"^(A0|A1|B|C)(,(A0|A1|B|C)){0,3}$")
_BENCH_RE = re.compile(r"^(new|bench-[a-z0-9-]{6,64})$")
_TURN_KEY_RE = re.compile(r"^[A-Za-z0-9_:/+.-]{1,200}$")
_REPS = (1, 3)
_REP_MAX = 2

_QUERY_CHECKS: dict[str, Any] = {
    "arm": _ARM_RE,
    "base": _ARM_RE,
    "cand": _ARM_RE,
    "arms": _ARMS_RE,
    "bench": _BENCH_RE,
    "episode": _EPISODE_RE,
    "turn_key": _TURN_KEY_RE,
}


class LabPathError(ValueError):
    """Un segmento o parámetro que no tiene la forma del contrato `lab@v1`."""


def _check(value: str, pattern: re.Pattern[str], what: str) -> str:
    if not isinstance(value, str) or not pattern.match(value):
        raise LabPathError(f"{what} inválido")
    return value


def run_path(run: str, *rest: str) -> str:
    """`/api/chats/lab/runs/<corrida>[/…]` con la corrida validada."""
    return "/".join((f"{PROVIDER_PREFIX}/runs", _check(run, RUN_ID_RE, "corrida"), *rest))


def conversation_path(run: str, sid: str, *rest: str) -> str:
    """`…/runs/<corrida>/conversations/<sid>[/…]` con ambos validados."""
    return run_path(run, "conversations", _check(sid, _SID_RE, "conversación"), *rest)


def clean_query(**params: Any) -> dict[str, Any]:
    """Los parámetros que viajan al provider: sin los ausentes y con forma válida."""
    out: dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            continue
        if key == "rep":
            if not isinstance(value, int) or not 0 <= value <= _REP_MAX:
                raise LabPathError("repetición inválida")
        elif key == "reps":
            if value not in _REPS:
                raise LabPathError("repeticiones inválidas")
        else:
            _check(value, _QUERY_CHECKS[key], key)
        out[key] = value
    return out
