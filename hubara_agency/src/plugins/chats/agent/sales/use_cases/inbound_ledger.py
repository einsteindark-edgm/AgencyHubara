"""Ledger durable de inbound del webhook de WhatsApp (auditoría 2026-09-18).

Responde "¿el mensaje llegó o no llegó?" sin depender de los logs del
container (se pierden en cada deploy). Caso: campaña halloween — Meta contaba
7 conversaciones, el vault 5, y no había forma de saber si los 2 webhooks
faltantes nunca entraron o entraron y se perdieron adentro.

Dos clases de registro (JSONL, una línea por registro):

* ``kind="request"`` — un POST al webhook: outcome + conteos por field.
* ``kind="message"`` — un mensaje del cliente, por etapa (``stage``):
  ``seen`` (estaba en el body crudo) → ``ingested`` | ``ingest_failed``.
  Un ``seen`` sin su ``ingested`` = lo perdimos nosotros.

Los registros ``seen`` salen del body CRUDO — no del parser — para que un
mensaje que el parser descarta quede igual a la vista.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


def webhook_ledger_records(body: Any, *, at_ms: int, outcome: str = "accepted") -> list[dict[str, Any]]:
    """Registros del ledger para UN POST al webhook (puro, tolerante a shapes
    rotas: lo que no se entiende no se cuenta, nunca lanza)."""
    request: dict[str, Any] = {
        "kind": "request",
        "at_ms": at_ms,
        "outcome": outcome,
        "fields": [],
        "n_messages": 0,
        "n_statuses": 0,
    }
    records: list[dict[str, Any]] = [request]
    for field_name, value in _iter_changes(body):
        if field_name not in request["fields"]:
            request["fields"].append(field_name)
        holder = _holder(field_name, value)
        statuses = holder.get("statuses")
        request["n_statuses"] += len(statuses) if isinstance(statuses, list) else 0
        for msg in _inbound_messages(field_name, value):
            request["n_messages"] += 1
            records.append(
                {
                    "kind": "message",
                    "stage": "seen",
                    "at_ms": at_ms,
                    "field": field_name,
                    "wa_message_id": msg.get("id"),
                    "session_id": _session_id(msg.get("from")),
                    "msg_type": msg.get("type"),
                    "wa_timestamp": msg.get("timestamp"),
                    "referral": _referral_summary(msg.get("referral")),
                }
            )
    return records


def message_stage_record(
    *,
    stage: str,
    at_ms: int,
    field: str,
    wa_message_id: str,
    from_number: str,
    error: str | None = None,
) -> dict[str, Any]:
    """Registro de una etapa POSTERIOR a ``seen`` (``ingested`` /
    ``ingest_failed``) — se une al ``seen`` por ``wa_message_id``."""
    record: dict[str, Any] = {
        "kind": "message",
        "stage": stage,
        "at_ms": at_ms,
        "field": field,
        "wa_message_id": wa_message_id,
        "session_id": _session_id(from_number),
    }
    if error is not None:
        record["error"] = error[:300]
    return record


NO_REFERRAL_BUCKET = "sin_referral"
_BOGOTA = ZoneInfo("America/Bogota")


def summarize_ledger(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Resumen para reconciliar contra Meta, por día de BOGOTÁ (la zona de la
    cuenta publicitaria) y por anuncio (``referral.source_id``).

    Por bucket: ``sessions`` = personas distintas que escribieron (lo comparable
    con "conversaciones iniciadas" de Meta); ``ingested`` / ``failed`` / ``lost``
    cuentan mensajes únicos (un reintento de Meta repite el wamid y no suma).
    ``lost`` = visto en el body y sin desenlace: lo descartó el parser o el
    proceso murió antes de ingerirlo.
    """
    requests: dict[str, int] = {}
    seen: dict[str, dict[str, Any]] = {}
    outcome: dict[str, dict[str, Any]] = {}
    for record in records:
        if record.get("kind") == "request":
            key = str(record.get("outcome"))
            requests[key] = requests.get(key, 0) + 1
            continue
        wamid = record.get("wa_message_id")
        if record.get("kind") != "message" or not isinstance(wamid, str):
            continue
        if record.get("stage") == "seen":
            seen.setdefault(wamid, record)
        elif record.get("stage") == "ingested" or wamid not in outcome:
            outcome[wamid] = record  # un `ingested` (reintento exitoso) gana sobre el fallo

    by_day_ad: dict[str, dict[str, dict[str, Any]]] = {}
    lost: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for wamid, record in sorted(seen.items(), key=lambda kv: kv[1]["at_ms"]):
        source_id = (record.get("referral") or {}).get("source_id")
        day = datetime.fromtimestamp(record["at_ms"] / 1000, _BOGOTA).strftime("%Y-%m-%d")
        bucket = by_day_ad.setdefault(day, {}).setdefault(
            source_id or NO_REFERRAL_BUCKET,
            {"sessions": set(), "ingested": 0, "failed": 0, "lost": 0},
        )
        bucket["sessions"].add(record.get("session_id"))
        row = {
            "wa_message_id": wamid,
            "session_id": record.get("session_id"),
            "at_ms": record["at_ms"],
            "source_id": source_id,
        }
        final = outcome.get(wamid)
        if final is None:
            bucket["lost"] += 1
            lost.append(row)
        elif final.get("stage") == "ingested":
            bucket["ingested"] += 1
        else:
            bucket["failed"] += 1
            failed.append(row | {"error": final.get("error")})

    for ads in by_day_ad.values():
        for bucket in ads.values():
            bucket["sessions"] = len(bucket["sessions"])
    return {
        "requests": requests,
        "by_day_ad": dict(sorted(by_day_ad.items())),
        "lost": lost,
        "failed": failed,
    }


def _iter_changes(body: Any):
    if not isinstance(body, dict) or not isinstance(body.get("entry"), list):
        return
    for entry in body["entry"]:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            if not isinstance(change, dict) or not isinstance(change.get("value"), dict):
                continue
            field_name = change.get("field") if isinstance(change.get("field"), str) else "messages"
            yield field_name, change["value"]


def _holder(field_name: str, value: dict[str, Any]) -> dict[str, Any]:
    """Dónde viven ``messages``/``statuses`` del change: en ``standby`` van
    bajo ``value.standby`` (mismo esquema que el inbound regular)."""
    holder = value.get("standby") if field_name == "standby" else value
    return holder if isinstance(holder, dict) else {}


def _inbound_messages(field_name: str, value: dict[str, Any]) -> list[dict[str, Any]]:
    """Mensajes DEL CLIENTE de un change."""
    messages = _holder(field_name, value).get("messages")
    return [m for m in messages if isinstance(m, dict)] if isinstance(messages, list) else []


def _session_id(from_number: Any) -> str | None:
    return f"wa_{from_number}" if isinstance(from_number, str) and from_number.isdigit() else None


def _referral_summary(referral: Any) -> dict[str, Any] | None:
    """Lo que hace falta para reconciliar contra Meta. El ``ctwa_clid`` NO se
    copia (solo si vino): el valor ya vive en la sesión y acá sería ruido."""
    if not isinstance(referral, dict):
        return None
    return {
        "source_type": referral.get("source_type"),
        "source_id": referral.get("source_id"),
        "headline": referral.get("headline"),
        "has_clid": bool(referral.get("ctwa_clid")),
    }
