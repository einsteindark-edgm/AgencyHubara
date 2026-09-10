"""Composición de las dependencias de las tools (canal 1: factories del SDK).

Cada port puede no estar configurado en este proceso (local sin Medusa, caja
sin snapshot): la dependencia queda en ``None`` y la tool responde un error
explícito que el agente sabe manejar, en vez de un 500 en un endpoint público.

``chats`` es el cast al contrato ``session-actions@v1`` (canal 3, D1.2b): las
tools de escritura delegan ahí. Import diferido para no cerrar el ciclo
``api → tools → api``.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Awaitable, Callable

from loguru import logger

from src.sdk.connectorkit import (
    get_catalog_client,
    get_checkout_verification_port,
    get_order_query_port,
)
from src.sdk.runtime import WORKSPACE_VAULT_DIR, FilesystemMetadataStore


@dataclass
class ToolDeps:
    catalog: Any | None
    checkout: Any | None
    order_query: Any | None
    metadata: Any  # FilesystemMetadataStore
    chats: Any | None = None  # SessionActionCall (cast mba→chats)
    #: D1.10 — ``(session_key, *, closing_tag, episode_id, order_id, order_reference)``: nota de
    #: frontera a MBA cuando una tool de escritura cerró el episodio. Best-effort.
    episode_boundary: Callable[..., Awaitable[Any]] | None = None


#: D1.10 — knob propio (además de la flag y la lista cerrada): la nota de
#: frontera es un ``agent_event`` que MBA podría convertir en un mensaje
#: proactivo al cliente; queda apagada hasta verificar en F0 que es silenciosa.
EPISODE_BOUNDARY_ENV = "MBA_EPISODE_BOUNDARY_EVENT"
_background: set[asyncio.Task[Any]] = set()


def episode_boundary_enabled() -> bool:
    return os.environ.get(EPISODE_BOUNDARY_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def make_episode_boundary(use_case_factory: Callable[[], Any]) -> Callable[..., Awaitable[asyncio.Task[Any] | None]]:
    """Devuelve el hook ``episode_boundary``: emite ``episode_closed`` en una
    task de fondo (la respuesta del connector a Meta no espera a Meta) y
    devuelve la task (``None`` con el knob apagado). Nunca levanta."""

    async def _emit(
        session_key: str, closing_tag: str, episode_id: str, order_id: str | None, order_reference: str | None
    ) -> Any:
        from src.plugins.mba.domain.agent_events import episode_closed_message

        try:
            use_case = use_case_factory()
            return await use_case.execute(
                session_key, "episode_closed", episode_id=episode_id, order_id=None,
                message=episode_closed_message(closing_tag, order_reference=order_reference or order_id),
                payload={"closing_tag": closing_tag, "order_id": order_id, "order_reference": order_reference},
                source="connector",
            )
        except Exception as exc:  # noqa: BLE001 — best-effort; el registro vive en agent_events[]
            logger.warning("[mba] nota de frontera episode_closed falló session={} ep={}: {}", session_key,
                           episode_id, exc)
            return None

    async def _boundary(
        session_key: str, *, closing_tag: str, episode_id: str, order_id: str | None, order_reference: str | None = None
    ) -> Any:
        if not episode_boundary_enabled():
            return None
        task = asyncio.get_running_loop().create_task(
            _emit(session_key, closing_tag, episode_id, order_id, order_reference)
        )
        _background.add(task)
        task.add_done_callback(_background.discard)
        return task

    return _boundary


def _default_emit_agent_event() -> Any:
    from src.plugins.mba.adapters.agent_event import MetaAgentEvent
    from src.plugins.mba.use_cases.emit_agent_event import EmitAgentEvent
    from src.sdk.runtime import mba_controls_thread, mba_customer_allowed, mba_standby_enabled

    return EmitAgentEvent(
        metadata_store=FilesystemMetadataStore(WORKSPACE_VAULT_DIR),
        port=MetaAgentEvent(),
        is_customer_allowed=mba_customer_allowed,
        is_enabled=mba_standby_enabled,
        controls_thread=mba_controls_thread,
        entity_id_fallback=lambda: os.environ.get("WHATSAPP_PHONE_NUMBER_ID", ""),
    )


def _try(name: str, factory: Callable[[], Any]) -> Any | None:
    try:
        return factory()
    except Exception as exc:  # noqa: BLE001 — sin config = tool degradada, no proceso caído
        logger.warning("[mba] {} no disponible en este proceso: {}", name, exc)
        return None


@lru_cache(maxsize=1)
def default_deps() -> ToolDeps:
    from src.plugins.mba.api.chats_cast import session_action

    return ToolDeps(
        catalog=_try("catalog", get_catalog_client),
        checkout=_try("checkout", get_checkout_verification_port),
        order_query=_try("order_query", get_order_query_port),
        metadata=FilesystemMetadataStore(WORKSPACE_VAULT_DIR),
        chats=session_action,
        episode_boundary=make_episode_boundary(_default_emit_agent_event),
    )
