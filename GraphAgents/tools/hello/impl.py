"""Lógica PURA de `hello` — G-AGNOSTIC: NO importa ningún runtime. Determinista."""
from __future__ import annotations


def run(*, name: str) -> dict:
    return {"greeting": f"hola, {name}"}
