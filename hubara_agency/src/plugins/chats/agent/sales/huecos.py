"""Testigo de huecos (2026-10-07): lo que hoy nadie frena, contado después de
enviar, para decidir con datos si vale la pena arreglarlo.

Análisis: `docs/calidad-llm/cobertura-motor.html`. La mayoría de los huecos ya
los registra el scorecard (cada regla que falló, turno por turno, en
`_evals/scorecards/`); este testigo cuenta lo que el scorecard no ve:

* ``texto_suelto_*``: el turno cerró con texto suelto (sin `send_reply`), el
  camino que se salta las revisiones de `send_reply` (plan del motor §6.6):
  la lista de opciones que la guarda tuvo que cambiar por el selector, la
  negación de un producto que la foto ya reconoció y la promesa de revisar
  y responder después.
* ``afirmacion_sin_consultar``: la capacidad `afirmacion` (en sombra, solo
  donde decide Jev) leyó una afirmación de stock, entrega o estado del pedido
  sin la herramienta que la consulta.

Un turno del cliente = una línea en ``<vault>/_huecos/<día UTC>.jsonl``, también
sin huecos (el denominador). Sin textos: solo qué pasó. El informe lo arma
`sales_eval/huecos.py`. Nunca lanza: el testigo no puede afectar al cliente.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FOLDER = "_huecos"
#: Los turnos que no son del cliente no cuentan (ni como denominador).
_NOT_CUSTOMER = frozenset({"ghost", "complement"})


def _via_send_reply(record: dict[str, Any]) -> bool:
    return any(
        isinstance(t, dict) and t.get("name") == "send_reply" and t.get("ok") is not False
        for t in record.get("tools") or []
    )


def witness_line(record: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any] | None:
    """La línea del testigo para un turno ya enriquecido, o None si no es un
    turno del cliente."""
    from src.plugins.chats.agent.sales.use_cases.photo_product import (
        denies_availability,
        promises_to_follow_up,
        verified_photo_products,
    )

    if str(record.get("trigger") or "customer") in _NOT_CUSTOMER:
        return None
    text = str(record.get("llm_text") or "")
    loose = bool(text.strip()) and not _via_send_reply(record)
    sent = text in (record.get("sent_texts") or [])
    holes: list[str] = []
    if loose and "variant_enumeration_guard" in (record.get("guards") or []):
        holes.append("texto_suelto_lista")
    if loose and sent and denies_availability(text) and verified_photo_products(metadata or {}):
        holes.append("texto_suelto_niega_foto")
    if loose and sent and promises_to_follow_up(text):
        holes.append("texto_suelto_promete_volver")
    claims = record.get("claims")
    if isinstance(claims, dict) and claims.get("value") is True:
        holes.append("afirmacion_sin_consultar")
    at_ms = record.get("recorded_at_ms")
    return {
        "at_ms": at_ms,
        "session": record.get("session_id"),
        "episode": record.get("episode_id"),
        "turn": record.get("turn"),
        "workflow": record.get("workflow"),
        "suelto": loose,
        "huecos": holes,
    }


def day_of(at_ms: Any) -> str:
    """El día UTC del turno (el archivo donde va su línea)."""
    if isinstance(at_ms, (int, float)) and not isinstance(at_ms, bool):
        return datetime.fromtimestamp(at_ms / 1000, UTC).date().isoformat()
    return datetime.now(UTC).date().isoformat()


def append_line(vault_dir: Path, line: dict[str, Any]) -> None:
    """Agrega la línea al archivo del día. Solo agrega (un proceso por worker)."""
    folder = Path(vault_dir) / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / f"{day_of(line.get('at_ms'))}.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")
