"""La forma de un brazo del laboratorio (PAQUETES_DE_DECISION.md F6).

Un brazo es un bot (`A1`, `B`) y, si fija un paquete de decisión distinto al
de la tienda, `@<paquete>`: `B@ventas-2` corre el bot B con la inteligencia
de `ventas-2`. La plataforma solo sabe la forma (para validar antes de
reenviar o de escribir una ruta del almacén); qué bots existen lo decide
chats (`sales/decisions/bots.py`) y qué paquetes existen, su carpeta.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

#: El id de un paquete de decisión (su carpeta, como en Terraform), hasta 40 caracteres.
BUNDLE_ID = r"[a-z][a-z0-9-]{0,39}"


def split_arm(arm: str) -> tuple[str, str]:
    """`(bot, paquete)`; sin `@`, el paquete es "" (el de la tienda)."""
    bot, _, bundle = arm.partition("@")
    return bot, bundle


def _one(bots: Iterable[str]) -> str:
    return "(?:" + "|".join(re.escape(b) for b in bots) + f")(?:@{BUNDLE_ID})?"


def arm_pattern(bots: Iterable[str]) -> re.Pattern[str]:
    """Un brazo: uno de `bots`, con o sin `@<paquete>`."""
    return re.compile(f"^{_one(bots)}$")


def arms_pattern(bots: Iterable[str], *, max_arms: int) -> re.Pattern[str]:
    """Una lista de brazos separados por coma (como mucho `max_arms`)."""
    one = _one(tuple(bots))
    return re.compile(f"^{one}(?:,{one}){{0,{max_arms - 1}}}$")
