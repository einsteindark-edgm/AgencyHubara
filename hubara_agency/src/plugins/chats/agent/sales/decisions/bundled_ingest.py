"""Builtins de las lecturas del ingest (baja, retoma) — PAQUETES_DE_DECISION.md.

Viven aparte porque usan `messagingkit`, que también re-exporta una
activity de Temporal: el resolutor los carga SOLO cuando una capacidad del
paquete los pide (las del ingest). Así las tools, que llegan al resolutor
por `guards`, no arrastran Temporal (contrato `tools-no-temporal`).
"""
from __future__ import annotations

from typing import Any

from src.plugins.chats.agent.sales.decisions.bundled import customer_text
from src.sdk.messagingkit import is_courtesy_text, is_opt_out_text, parse_reengagement_deferral


def opt_out_text(inp: Any) -> bool:
    text = customer_text(inp)
    return bool(text) and is_opt_out_text(text)


def reengagement(inp: Any) -> dict[str, Any]:
    text = customer_text(inp)
    parsed = parse_reengagement_deferral(text, inp.now_ms, inp.tz) if text else None
    return {
        "deferral": {"kind": parsed.kind, "until_ms": parsed.until_ms} if parsed else None,
        "courtesy": bool(text) and is_courtesy_text(text),
    }
