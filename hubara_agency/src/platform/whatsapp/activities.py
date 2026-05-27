"""Activities Temporal para enviar mensajes a WhatsApp.

Contiene tanto la activity legacy `send_whatsapp_message_activity` (texto
plano) como `send_whatsapp_template_activity` (HU-WA24H-001 F1.7), usado
por el watchdog y por el agente LLM cuando decide enviar un template.

El `name=` del decorator se preserva en todas las activities para no
invalidar history events de workflows en vuelo.

`send_message_to_session` es la version pura (sin decorators de Temporal)
que los handlers HTTP (dashboard handoff) reutilizan para mandar mensajes
del humano al cliente sin pasar por el worker. La activity la envuelve.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import asdict
from typing import Any

import structlog
from temporalio import activity
from temporalio.exceptions import ApplicationError

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.constants import WHATSAPP_SESSION_PREFIX
from src.platform.temporal.heartbeat import with_heartbeat
from src.platform.whatsapp import client as whatsapp_client
from src.platform.whatsapp.composition import get_template_registry
from src.platform.whatsapp.cost import (
    OutboundLogEntry,
    add_outbound_to_summary,
    empty_episode_cost_summary,
)
from src.platform.whatsapp.dtos import OutboundResult

log = structlog.get_logger()


# =============================================================================
# Constantes — template send (F1.7)
# =============================================================================


#: Códigos de error Meta que NO deben ser retried. El send no tiene chance
#: de funcionar en un retry inmediato (template paused, no aprobado, user
#: saturado por marketing cap). Aborta el workflow con `non_retryable=True`.
NON_RETRYABLE_META_ERROR_CODES: frozenset[str] = frozenset(
    {
        "131008",  # Template name does not exist OR not approved in this language
        "132012",  # Template has been paused by Meta (low quality)
        "131049",  # Per-user marketing template cap reached
        "131047",  # Re-engagement message — fuera de ventana (defensivo para template)
    }
)


def _now_ms() -> int:
    return int(time.time() * 1000)


async def send_message_to_session(session_id: str, message: str) -> None:
    """Envia `message` al cliente cuyo `session_id` mapea a un numero de WhatsApp.

    Pura (no toca Temporal). Resuelve `phone_number_id` desde `metadata.json` o
    desde `WHATSAPP_PHONE_NUMBER_ID` env var (fallback). Fragmenta el mensaje en
    burbujas separadas por `\\n\\n` con una pausa de 1.5s entre chunks (igual que
    la activity, para preservar UX en WhatsApp).

    Reutilizada por:
      * `send_whatsapp_message_activity` (worker, dentro de workflows).
      * `dashboard/handoff.py` (HTTP, mensajes del humano operador).
    """
    from_number = session_id.replace(WHATSAPP_SESSION_PREFIX, "")

    phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    if not phone_number_id:
        raise RuntimeError("WHATSAPP_PHONE_NUMBER_ID not configured")

    try:
        metadata_file = WORKSPACE_VAULT_DIR / session_id / "metadata.json"
        if metadata_file.exists():
            data = json.loads(metadata_file.read_text(encoding="utf-8"))
            phone_number_id = data.get("phone_number_id", phone_number_id)
    except (OSError, json.JSONDecodeError):
        pass

    chunks = [chunk.strip() for chunk in message.split("\n\n") if chunk.strip()]
    for chunk in chunks:
        await whatsapp_client.send_message(phone_number_id, from_number, chunk)
        await asyncio.sleep(1.5)


@activity.defn(name="send_whatsapp_message_activity")
@with_heartbeat(every=10)
async def send_whatsapp_message_activity(session_id: str, message: str) -> None:
    await send_message_to_session(session_id, message)


async def _read_metadata_for_typing(session_id: str) -> tuple[str | None, str | None]:
    """Devuelve `(phone_number_id, last_inbound_message_id)` desde metadata.

    Fallback de phone_number_id: env var. message_id NO tiene fallback — sin
    referencia a un mensaje del cliente la API de WhatsApp rechaza la request.
    """
    phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    message_id: str | None = None
    try:
        metadata_file = WORKSPACE_VAULT_DIR / session_id / "metadata.json"
        if metadata_file.exists():
            data = json.loads(metadata_file.read_text(encoding="utf-8"))
            phone_number_id = data.get("phone_number_id", phone_number_id)
            message_id = data.get("last_inbound_message_id")
    except (OSError, json.JSONDecodeError):
        pass
    return phone_number_id, message_id


@activity.defn(name="send_typing_indicator_activity")
async def send_typing_indicator_activity(session_id: str) -> None:
    """Dispara el "escribiendo..." outbound al cliente.

    Lee `last_inbound_message_id` desde `metadata.json` (escrito por
    `IngestInboundMessage` al recibir cada webhook). Si no hay message_id
    — caso turno proactivo / handoff / ghost trigger — la activity noopea
    silenciosamente: la API de WhatsApp requiere referenciar un mensaje del
    cliente, y un typing indicator sin contexto fresco no aporta UX.

    Best-effort: errores HTTP los absorbe `client.send_typing_indicator`.
    Sin heartbeat porque es una request corta (timeout 4s) y el retry seria
    contraproducente: si fallo, el LLM ya esta procesando y el typing
    seria stale.
    """
    phone_number_id, message_id = await _read_metadata_for_typing(session_id)
    if not phone_number_id or not message_id:
        return
    await whatsapp_client.send_typing_indicator(phone_number_id, message_id)


# =============================================================================
# Template send (HU-WA24H-001 F1.7)
# =============================================================================


def _parse_meta_error_code(error: str | None) -> str | None:
    """Extrae el código Meta del string de error de `OutboundResult.error`.

    `_post_json` retorna error como `http_<status>: <body>` donde el body tiene
    shape `{"error": {"code": 131008, "message": "...", ...}}`. Hacemos best-
    effort parse — si no encuentra, retorna None y el caller deja retryable.
    """
    if not error:
        return None
    try:
        # Buscar "code": N en el body
        import re

        match = re.search(r'"code"\s*:\s*(\d+)', error)
        if match:
            return match.group(1)
    except Exception:  # noqa: BLE001 — best-effort
        return None
    return None


def _read_metadata(session_id: str) -> dict[str, Any]:
    """Lee metadata.json. Devuelve {} si no existe o está corrupto."""
    metadata_file = WORKSPACE_VAULT_DIR / session_id / "metadata.json"
    if not metadata_file.exists():
        return {}
    try:
        return json.loads(metadata_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_metadata(session_id: str, data: dict[str, Any]) -> None:
    """Escribe metadata.json atómicamente (write tmp + rename)."""
    metadata_file = WORKSPACE_VAULT_DIR / session_id / "metadata.json"
    metadata_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = metadata_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(metadata_file)


def _resolve_phone_number_id(metadata: dict[str, Any]) -> str:
    """Resuelve phone_number_id desde metadata o fallback a env."""
    phone_number_id = metadata.get("phone_number_id") or os.getenv(
        "WHATSAPP_PHONE_NUMBER_ID"
    )
    if not phone_number_id:
        raise ApplicationError(
            "WHATSAPP_PHONE_NUMBER_ID not configured (neither in metadata "
            "nor env var)",
            non_retryable=True,
            type="WhatsAppConfigError",
        )
    return phone_number_id


def _append_outbound_to_active_episode(
    metadata: dict[str, Any],
    log_entry: OutboundLogEntry,
) -> None:
    """Mutates: agrega `log_entry` al `outbound_messages[]` del episodio
    activo y actualiza su `cost_summary`. Si NO hay episodio activo (caso
    raro: outbound sin haber recibido inbound), retorna sin tocar — el
    OutboundLogEntry se persiste en `metadata.last_outbound` solamente.

    Detalle de cost: el `log_entry` viene con `cost_usd_micros=None` y
    `pricing=None` porque el send activity NO conoce el pricing todavía
    (viene por webhook delivery status). `IngestDeliveryStatus` lo
    materializa después usando `materialize_pending_in_summary`.
    """
    episodes = metadata.get("episodes", [])
    if not episodes:
        return
    last = episodes[-1]
    if last.get("closed_at_ms") is not None:
        return  # episodio cerrado — no contaminar

    # Asegurar list + summary inicial
    outbound_list = last.setdefault("outbound_messages", [])
    summary_raw = last.get("cost_summary")
    if not isinstance(summary_raw, dict):
        summary = empty_episode_cost_summary()
    else:
        # Reconstruir EpisodeCostSummary desde dict (sin importar
        # CategoryCostBreakdown para no hacer la reconstrucción compleja).
        # Mejor approach pragmático: invocar add_outbound_to_summary que
        # solo necesita los campos planos del summary; reconstruimos lo
        # mínimo. Si la estructura está corrupta, empezamos de cero.
        from src.platform.whatsapp.cost import (
            CategoryCostBreakdown,
            EpisodeCostSummary,
        )

        def _rebuild_breakdown(raw: Any) -> dict[str, CategoryCostBreakdown]:
            if not isinstance(raw, dict):
                return {}
            out: dict[str, CategoryCostBreakdown] = {}
            for k, v in raw.items():
                if isinstance(v, dict) and "count" in v and "usd_micros" in v:
                    out[k] = CategoryCostBreakdown(
                        count=int(v["count"]), usd_micros=int(v["usd_micros"])
                    )
            return out

        try:
            summary = EpisodeCostSummary(
                total_usd_micros=int(summary_raw.get("total_usd_micros", 0)),
                messages_count=int(summary_raw.get("messages_count", 0)),
                messages_billable_count=int(
                    summary_raw.get("messages_billable_count", 0)
                ),
                messages_free_count=int(summary_raw.get("messages_free_count", 0)),
                messages_pending_count=int(
                    summary_raw.get("messages_pending_count", 0)
                ),
                by_category=_rebuild_breakdown(summary_raw.get("by_category", {})),
                by_pricing_type=_rebuild_breakdown(
                    summary_raw.get("by_pricing_type", {})
                ),
            )
        except (ValueError, TypeError):
            log.warning(
                "cost_summary_corrupted_resetting", session_id=metadata.get("session_id")
            )
            summary = empty_episode_cost_summary()

    new_summary = add_outbound_to_summary(summary, log_entry)
    outbound_list.append(asdict(log_entry))
    last["cost_summary"] = asdict(new_summary)


async def send_template_to_session(
    session_id: str,
    template_name: str,
    variables: dict[str, str],
) -> OutboundResult:
    """Envía un template aprobado y persiste el OutboundLogEntry.

    Versión pura (sin decorators Temporal) — testeable sin worker. El activity
    `send_whatsapp_template_activity` es solo el wrapper con heartbeat.

    Flujo:
      1. Resolver `TemplateSpec` desde el registry (composition cached).
      2. Leer metadata.json — phone_number_id + episodio activo.
      3. POST a Cloud API vía `whatsapp_client.send_template`.
      4. Si falla con código non-retryable → ApplicationError(non_retryable=True).
      5. Si falla con código retryable → ApplicationError normal (Temporal
         retry policy decide).
      6. Si OK → persistir OutboundLogEntry (pricing=None, cost=None) en
         episodes[active].outbound_messages[] + actualizar cost_summary.
      7. Retornar OutboundResult.

    El cost se materializa después cuando llega el webhook `message_status`
    con el objeto `pricing` (use case `IngestDeliveryStatus` — F1.10-F1.12).
    """
    # 1. Resolver spec
    registry = get_template_registry()
    if template_name not in registry:
        raise ApplicationError(
            f"Template {template_name!r} not in registry. "
            f"Available: {sorted(registry.keys())}",
            non_retryable=True,
            type="TemplateNotFound",
        )
    spec = registry[template_name]

    # 2. Read metadata + resolver destino
    metadata = _read_metadata(session_id)
    phone_number_id = _resolve_phone_number_id(metadata)
    to_number = session_id.replace(WHATSAPP_SESSION_PREFIX, "")

    # 3. Send
    result = await whatsapp_client.send_template(
        phone_number_id, to_number, spec, variables
    )

    # 4. Parse error y decidir retry policy
    if not result.ok:
        error_code = _parse_meta_error_code(result.error)
        if error_code in NON_RETRYABLE_META_ERROR_CODES:
            raise ApplicationError(
                f"WhatsApp template send failed (non-retryable, code={error_code}): "
                f"{result.error}",
                non_retryable=True,
                type=f"TemplateMetaError{error_code}",
            )
        raise ApplicationError(
            f"WhatsApp template send failed (retryable): {result.error}",
            type="TemplateSendRetryable",
        )

    # 6. Persistir OutboundLogEntry — pricing y cost vienen después (webhook)
    log_entry = OutboundLogEntry(
        sent_at_ms=_now_ms(),
        wa_message_id=result.wa_message_id or "",
        kind="template",
        template_name=template_name,
        pricing=None,
        cost_usd_micros=None,
        rate_card_version=None,
    )
    _append_outbound_to_active_episode(metadata, log_entry)
    metadata["last_outbound"] = asdict(log_entry)
    _write_metadata(session_id, metadata)

    # HU-WA24H-001 pre-mortem F2.2: el dashboard del operador lee el JSONL
    # del session_history para mostrar el chat. Sin esto, los templates
    # enviados quedan invisibles al operador humano que tome el caso.
    # Persistimos un marker semántico — no el body literal del template
    # (vive en Meta Business Manager, no en código), pero suficiente para
    # que el operador entienda qué se mandó.
    _append_template_to_session_history(
        session_id, template_name, variables, spec.waba_template_name
    )

    log.info(
        "template_send_success",
        session_id=session_id,
        template_name=template_name,
        wa_message_id=result.wa_message_id,
    )
    return result


def _append_template_to_session_history(
    session_id: str,
    template_name: str,
    variables: dict[str, str],
    waba_template_name: str,
) -> None:
    """Persiste un marker del template enviado al JSONL del session_history.

    Shape del evento (compatible con `append_assistant_event` shape):
        {"role": "assistant", "kind": "template", "template_name": "...",
         "waba_template_name": "...", "variables": {...},
         "content": "[Template: ...] var1=val1, var2=val2",
         "timestamp": "<ISO>"}

    El `content` es un string human-readable que el dashboard puede pintar
    si no quiere parsear los campos estructurados.

    Best-effort: si el write falla, log warning y continúa — el send ya
    ocurrió, el log JSONL es secondary observability.
    """
    history_path = WORKSPACE_VAULT_DIR / session_id / "sessions" / f"{session_id}.jsonl"
    try:
        history_path.parent.mkdir(parents=True, exist_ok=True)
        var_summary = ", ".join(f"{k}={v}" for k, v in variables.items())
        content = f"[Template: {template_name}] {var_summary}".strip()
        event = {
            "role": "assistant",
            "kind": "template",
            "template_name": template_name,
            "waba_template_name": waba_template_name,
            "variables": variables,
            "content": content,
            "timestamp": _now_iso_utc(),
        }
        with history_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError as e:
        log.warning(
            "template_session_history_persist_failed",
            session_id=session_id,
            template_name=template_name,
            error=str(e),
        )


def _now_iso_utc() -> str:
    """ISO 8601 UTC string para timestamps del JSONL (simetría con
    assistant_event y human_event existentes)."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


@activity.defn(name="send_whatsapp_template_activity")
@with_heartbeat(every=10)
async def send_whatsapp_template_activity(
    session_id: str,
    template_name: str,
    variables: dict[str, str],
) -> OutboundResult:
    """Activity wrapper que delega a `send_template_to_session`."""
    return await send_template_to_session(session_id, template_name, variables)
