"""Escalación a humano del agente Sales con precondiciones de dominio.

`ORDER_PENDING_SHIPPING_DETAILS` significa "el cliente confirmó el pedido pero
no mandó los datos de envío". Run 01a0a0f1 (2026-09-14): el LLM la usó sin
que el cliente hubiera dicho que sí → la sesión quedó en ruta humano (bot
mudo) por un pedido que no existía. `guarded_escalation_tool` envuelve la
tool de plataforma (que el worker, composition root, ya importa — P-28: el
plugin no importa `src.platform`) y rechaza esa razón cuando no hay
confirmación de compra registrada; el resto de razones pasa intacto.

Motor de decisiones (F5): la despedida la limpia `clean_llm_text` (la
muletilla del modelo, capacidad `preambulo`) y la filtra `safe_customer_text`
(la capacidad `persona`), con el proveedor del bot de la conversación, sobre
el punto de extensión `customer_farewell` de la tool de plataforma.

`BULK_ORDER` («>20 unidades») con lo que se cobra por caja (laboratorio,
2026-10-14: «me vendes 36 velitas?» se pasó a un colega; eran 3 cajas de las
velas de deseos): si el cliente dio una cifra que, contada en velas, cabe en
20 cajas o menos, decide la capacidad `por_mayor`. «velas» no escala y el
modelo recibe las cuentas; «duda» (sin Jev) lo devuelve UNA vez por mensaje
del cliente (`BULK_CHECK_KEY`: el segundo intento escala, nunca un bucle).
"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from exoclaw.agent.tools import ToolContext

from src.plugins.chats.agent.sales.decisions.guards import bulk_reading, clean_llm_text, safe_customer_text
from src.plugins.chats.agent.sales.state import FilesystemMetadataStore
from src.plugins.chats.agent.sales.use_cases.pack_quantity import BULK_UNITS, candle_counts, loose_candles_note
from src.plugins.chats.agent.sales.use_cases.product_options import boxed_products
from src.plugins.chats.shared.purchase_signals import has_purchase_confirmation
from src.plugins.chats.shared.store_pack import store_domain

GUARDED_REASON = "ORDER_PENDING_SHIPPING_DETAILS"
BULK_REASON = "BULK_ORDER"
#: El «por mayor» que pueden ser velas sueltas vuelve al modelo UNA vez por
#: mensaje del cliente: el segundo intento es el modelo que ya lo revisó.
BULK_CHECK_KEY = "por_mayor_revisado"
#: Lo último de la charla que lee Jev (`por_mayor`).
TALK_EVENTS = 6


def guarded_escalation_tool(base: type) -> type:
    """Subclase de `base` (la `EscalateToHumanTool` de plataforma) con la guarda."""

    class SalesEscalateToHumanTool(base):  # type: ignore[misc,valid-type]
        def __init__(self, *args: Any, history_reader: Callable[[str], list[dict[str, Any]]] | None = None,
                     **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            # Lo último de la charla (R-DIP: lo inyecta el worker).
            self._history_reader = history_reader

        async def execute_with_context(
            self,
            ctx: ToolContext,
            reason_category: str,
            summary: str,
            customer_message: str = "",
        ) -> str:
            if reason_category == GUARDED_REASON and not self._purchase_confirmed(ctx):
                return json.dumps(
                    {
                        "error": (
                            "precondition_failed: ORDER_PENDING_SHIPPING_DETAILS "
                            "exige que el cliente haya confirmado la compra (dijo "
                            "que sí a un producto con precio, o tocó Confirmar). En "
                            "este episodio no hay confirmación registrada: NO se "
                            "escaló. Etiqueta INTERESADO y deja que remarketing "
                            "retome; el bot sigue a cargo."
                        ),
                        "escalated": False,
                    },
                    ensure_ascii=False,
                )
            if reason_category == BULK_REASON:
                held = await self._loose_candles(ctx, summary)
                if held is not None:
                    return held
            return await super().execute_with_context(
                ctx,
                reason_category=reason_category,
                summary=summary,
                customer_message=customer_message,
            )

        async def _loose_candles(self, ctx: ToolContext, summary: str) -> str | None:
            """El error que devuelve el «por mayor» al modelo si eran velas
            sueltas de lo que se cobra por caja (`por_mayor`); None = escala."""
            boxes = boxed_products(store_domain().get("sold_by_box"))
            events = [
                e for e in self._events(ctx.session_key)[-TALK_EVENTS:]
                if e.get("role") in ("user", "assistant") and str(e.get("content") or "").strip()
            ]
            customer = "\n".join(str(e["content"]) for e in events if e["role"] == "user")
            cifras = candle_counts(customer, [units for _name, units in boxes])
            if not cifras:
                return None
            data = self._metadata(ctx)
            inbound = data.get("last_inbound_message_id")
            if inbound and data.get(BULK_CHECK_KEY) == inbound:
                return None
            talk = "\n".join(
                f"[{'cliente' if e['role'] == 'user' else 'asesor'}] {str(e['content']).strip()[:400]}" for e in events
            )
            reading = await bulk_reading(
                f"{talk}\n[asesor, nota para el colega] {summary.strip()[:400]}",
                cifras, [name for name, _u in boxes], [units for _n, units in boxes],
                session_id=ctx.session_key, vault_dir=self._vault_dir,
            )
            if reading is None or reading == "por_mayor":
                return None
            if inbound:
                FilesystemMetadataStore(self._vault_dir).update(
                    ctx.session_key, lambda d: {**d, BULK_CHECK_KEY: inbound}
                )
            lead = (
                "NO se pasó el caso a un colega: no es un pedido al por mayor."
                if reading == "velas"
                else "Todavía NO se pasó el caso a un colega."
            )
            return json.dumps(
                {
                    "error": (
                        f"{lead} {loose_candles_note(boxes, cifras)} Anota las cajas con set_order_slot y "
                        "sigue la venta (si no sabes de cuál producto, pregúntaselo). Si de verdad pide más de "
                        f"{BULK_UNITS} cajas u otro producto, vuelve a llamar escalate_to_human."
                    ),
                    "escalated": False,
                },
                ensure_ascii=False,
            )

        def _events(self, session_key: str) -> list[dict[str, Any]]:
            """Lo último de la conversación; vacío sin lector o si falla (es
            contexto del motor: nunca bloquea la escalación)."""
            if self._history_reader is None:
                return []
            try:
                return list(self._history_reader(session_key) or [])
            except Exception:  # noqa: BLE001
                return []

        def _metadata(self, ctx: ToolContext) -> dict[str, Any]:
            metadata_file = self._vault_dir / ctx.session_key / "metadata.json"
            if not metadata_file.exists():
                return {}
            try:
                data = json.loads(metadata_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {}
            return data if isinstance(data, dict) else {}

        async def customer_farewell(self, ctx: ToolContext, reason_category: str, customer_message: str) -> str:
            # Motor de decisiones (F5): la muletilla del modelo al principio la
            # decide `preambulo` y qué oraciones de la despedida se caen,
            # `persona`, con el proveedor del bot de la conversación (la regla
            # de hoy por defecto: idéntico a antes).
            safe = await safe_customer_text(
                await clean_llm_text(customer_message or "", session_id=ctx.session_key, vault_dir=self._vault_dir),
                session_id=ctx.session_key,
                vault_dir=self._vault_dir,
            )
            return self.farewell_from_safe_text(reason_category, safe)

        def _purchase_confirmed(self, ctx: ToolContext) -> bool:
            return has_purchase_confirmation(self._metadata(ctx))

    SalesEscalateToHumanTool.__name__ = "SalesEscalateToHumanTool"
    SalesEscalateToHumanTool.__qualname__ = "SalesEscalateToHumanTool"
    return SalesEscalateToHumanTool


__all__ = ["BULK_CHECK_KEY", "BULK_REASON", "GUARDED_REASON", "guarded_escalation_tool"]
