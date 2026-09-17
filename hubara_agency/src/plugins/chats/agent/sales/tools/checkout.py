"""Tool: VerifyOrderForCheckoutTool.

DEHA-compliant tool que verifica precio y disponibilidad LIVE contra Medusa
JUSTO antes de cerrar la venta. Es el unico momento de la conversacion donde
la fuente de la verdad NO es el snapshot — el snapshot puede tener dias o
semanas (es valido durante la charla), pero al cobrar verificamos contra la
API real para detectar cambios de precio que hubieran ocurrido.

Desde el incidente run ebbc203d (2026-09-16) la tool es además LA FUENTE DE
PRECIOS DEL CIERRE:

  1. Devuelve `unit_price_cop` (int) por ítem y `subtotal_cop` — los precios
     EXACTOS que el LLM debe pasar a `present_order_confirmation` y
     `register_order` (el live si Medusa cambió; si no, el snapshot).
  2. Persiste `metadata.checkout_verification` (ledger por handle): las tools
     de cierre aceptan solo esos precios (o el del snapshot) — un precio
     inventado, "negociado" por el cliente o sacado del anuncio se rechaza.
  3. Cruza lo que el bot ESCRIBIÓ en el episodio (historial de la sesión)
     contra el catálogo (`price_quotes.find_unexplained_amounts`): en ese run
     el LLM dijo "tiene un valor de $45.000" (precio del anuncio) con el set a
     $49.500, la verificación snapshot-vs-live dio OK y el resumen salió a
     $49.500 sin explicación. Ahora `quoted_price_mismatch=true` + la
     instrucción de aclararlo ANTES de presentar la confirmación.

El envelope que devuelve es leido por el LLM, no parseado al workflow (no es
un "decision tool" como escalation/transfer). Las acciones derivadas
(escalar si verify falla, avisar al cliente si hay discrepancia) las dicta
el prompt `TOOLS.md`.

Inerte respecto a Temporal (ADR-001). I/O real via `CheckoutVerificationPort`
+ `CatalogPort` (allowlist de precios) + `metadata_store` (ledger y episodio,
`composition.build_session_metadata_store`) + `history_reader` (eventos del
historial, `composition.build_session_history_reader`) inyectados por el
composition root del worker (R-DIP #7: la tool no importa platform ni
temporalio). Sin `metadata_store` no hay ledger ni recorte por episodio; sin
`history_reader` no hay cruzada de montos citados — los precios del envelope
siguen funcionando (degradado, nunca bloquea).
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from exoclaw.agent.tools import ToolBase, ToolContext
from loguru import logger

from src.platform.catalog.checkout_port import (
    CheckoutItem,
    CheckoutVerificationPort,
    VerifiedItem,
)
from src.sdk.catalogkit import CatalogPort
from src.plugins.chats.agent.sales.config.shipping import (
    CASH_ON_DELIVERY_MIN_PRODUCTS_COP,
    SHIPPING_RATE_BOGOTA_COP,
    SHIPPING_RATE_NATIONAL_COP,
)
from src.plugins.chats.agent.sales.price_quotes import (
    UnexplainedAmount,
    find_unexplained_amounts,
)
from src.plugins.chats.agent.sales.pricing import catalog_unit_price, format_cop
from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import get_active_episode

# Montos de política que el bot puede citar legítimamente en su contexto
# (umbral de contra entrega, tarifas mínimas de envío).
_POLICY_AMOUNTS = frozenset({
    CASH_ON_DELIVERY_MIN_PRODUCTS_COP,
    SHIPPING_RATE_BOGOTA_COP,
    SHIPPING_RATE_NATIONAL_COP,
})
_CATALOG_ALLOWLIST_LIMIT = 100

# `session_key -> eventos del historial` (dicts con role/content/timestamp/sender).
HistoryReader = Callable[[str], list[dict[str, Any]]]


class MetadataStore(Protocol):
    """Lo que la tool necesita del store de metadata de sesión (duck-typed)."""

    def read(self, session_id: str) -> dict[str, Any]: ...

    def write(self, session_id: str, data: dict[str, Any]) -> None: ...


def _price_to_int(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        value = int(round(float(raw)))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _iso_to_ms(value: Any) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


class VerifyOrderForCheckoutTool(ToolBase):
    """Verifica items contra la API live de Medusa antes de cerrar venta.

    Llamada OBLIGATORIA antes de confirmar pedido al cliente (ver
    `sales_whatsapp/workspace/TOOLS.md` -> "Verificación en checkout").
    """

    name = "verify_order_for_checkout"
    description = (
        "Verifica precio y disponibilidad EN VIVO contra Medusa para los "
        "items del pedido ANTES de cerrar la venta. Úsala una sola vez, "
        "justo después de tener producto + cantidad + datos del cliente y "
        "ANTES de confirmar el pedido. Devuelve `unit_price_cop` por ítem y "
        "`subtotal_cop`: son los ÚNICOS precios válidos para "
        "present_order_confirmation y register_order. Si hay discrepancia "
        "de precio contra el snapshot que ya le mostraste, avísale al "
        "cliente honestamente y pídele confirmación con el precio "
        "actualizado. Si `quoted_price_mismatch=true`, le escribiste al "
        "cliente un precio que no es el del catálogo: acláraselo ANTES de "
        "presentar la confirmación. Si la API no responde (error: "
        "catalog_unavailable), escala a humano con "
        "reason_category=CHECKOUT_VERIFY_FAILED."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "minItems": 1,
                "description": (
                    "Lista de items a verificar. Cada item: {handle, "
                    "quantity}. Los handles deben ser EXACTAMENTE los "
                    "vistos previamente en search_products / "
                    "get_product_by_handle — NO los inventes."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "handle": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 200,
                        },
                        "quantity": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 999,
                        },
                    },
                    "required": ["handle", "quantity"],
                },
            },
        },
        "required": ["items"],
    }

    def __init__(
        self,
        workspace: str | Path,
        verifier: CheckoutVerificationPort,
        catalog: CatalogPort | None = None,
        metadata_store: MetadataStore | None = None,
        history_reader: HistoryReader | None = None,
    ) -> None:
        self._workspace = Path(workspace)
        self._verifier = verifier
        # Allowlist de precios legítimos para la cruzada de montos citados.
        # Sin catálogo (dev/tests) la referencia son solo los ítems verificados.
        self._catalog = catalog
        # Sin store no hay ledger ni recorte por episodio (degradado).
        self._metadata_store = metadata_store
        # Sin lector de historial no hay cruzada de montos citados (degradado).
        self._history_reader = history_reader

    # ------------------------------------------------------------------
    # Ledger + cruzada de montos citados
    # ------------------------------------------------------------------

    def _episode_assistant_texts(self, session_key: str) -> list[str]:
        """Textos que el BOT le escribió al cliente en el episodio activo.

        Excluye al humano del dashboard (`sender="human"`) y los mensajes
        anteriores al episodio (pedidos viejos). Sin episodio → toda la sesión.
        Sin `history_reader` → [] (la cruzada se salta).
        """
        if self._history_reader is None:
            return []
        metadata: dict[str, Any] = {}
        if self._metadata_store is not None:
            try:
                metadata = self._metadata_store.read(session_key) or {}
            except Exception:  # noqa: BLE001 — sin metadata legible: sesión completa
                metadata = {}
        episode = get_active_episode(metadata) or {}
        started_at_ms = episode.get("started_at_ms")
        texts: list[str] = []
        try:
            events = list(self._history_reader(session_key) or [])
        except Exception as exc:  # noqa: BLE001 — historial ilegible: sin cruzada
            logger.warning("🧾 [TOOL verify_order_for_checkout] historial ilegible: {}", exc)
            return []
        for event in events:
            if event.get("role") != "assistant" or event.get("sender") == "human":
                continue
            content = event.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            if started_at_ms is not None:
                at_ms = _iso_to_ms(event.get("timestamp"))
                if at_ms is not None and at_ms < int(started_at_ms):
                    continue
            texts.append(content)
        return texts

    async def _catalog_price_allowlist(self, unit_prices: dict[str, int]) -> set[int]:
        allowed = set(unit_prices.values())
        if self._catalog is None:
            return allowed
        try:
            result = await self._catalog.search(q="", limit=_CATALOG_ALLOWLIST_LIMIT)
            for product in list(result.results):
                price = catalog_unit_price(product)
                if price is not None:
                    allowed.add(price)
        except Exception as exc:  # noqa: BLE001 — catálogo caído: solo los verificados
            logger.warning(
                "🧾 [TOOL verify_order_for_checkout] allowlist sin catálogo ({}) — "
                "uso solo los precios verificados",
                exc,
            )
        return allowed

    async def _quoted_mismatches(
        self, session_key: str, unit_prices: dict[str, int]
    ) -> list[UnexplainedAmount]:
        texts = self._episode_assistant_texts(session_key)
        if not texts:
            return []
        allowed = await self._catalog_price_allowlist(unit_prices)
        hits: list[UnexplainedAmount] = []
        for text in texts:
            hits.extend(
                find_unexplained_amounts(
                    text, catalog_prices=allowed, policy_amounts=_POLICY_AMOUNTS
                )
            )
        return hits

    def _persist_ledger(
        self,
        session_key: str,
        verified: list[VerifiedItem],
        qty_by_handle: dict[str, int],
        unit_prices: dict[str, int],
        quoted_amounts: list[int],
    ) -> None:
        """`metadata.checkout_verification`: precios verificados por handle.
        Lo consumen `present_order_confirmation` y `register_order` (aceptan
        solo estos precios o el del snapshot)."""
        if self._metadata_store is None:
            logger.warning(
                "🧾 [TOOL verify_order_for_checkout] sin metadata_store: ledger no persistido"
            )
            return
        try:
            store = self._metadata_store
            data = store.read(session_key) or {}
            data["checkout_verification"] = {
                "verified_at_ms": int(time.time() * 1000),
                "items": {
                    vi.handle: {
                        "snapshot_price_cop": _price_to_int(vi.snapshot_price),
                        "live_price_cop": _price_to_int(vi.live_price),
                        "unit_price_cop": unit_prices.get(vi.handle),
                        "quantity": qty_by_handle.get(vi.handle, 1),
                        "in_stock": vi.in_stock,
                    }
                    for vi in verified
                },
                "quoted_amounts_mismatch": quoted_amounts,
            }
            store.write(session_key, data)
        except Exception as exc:  # noqa: BLE001 — el ledger es best-effort
            logger.warning(
                "🧾 [TOOL verify_order_for_checkout] no pude persistir el ledger: {}", exc
            )

    # ------------------------------------------------------------------
    # Tool
    # ------------------------------------------------------------------

    async def execute_with_context(
        self,
        ctx: ToolContext,
        items: list[dict[str, Any]],
    ) -> str:
        parsed = [
            CheckoutItem(handle=str(it["handle"]), quantity=int(it["quantity"]))
            for it in items
        ]
        logger.info(
            "🧾 [TOOL verify_order_for_checkout] session={} items={}",
            ctx.session_key,
            [(p.handle, p.quantity) for p in parsed],
        )

        result = await self._verifier.verify_items(parsed)

        if not result.catalog_available:
            logger.warning(
                "🧾 [TOOL verify_order_for_checkout] catalog_unavailable: {}",
                result.error_detail,
            )
            return json.dumps(
                {
                    "error": "catalog_unavailable",
                    "detail": result.error_detail,
                    "message": (
                        "No se pudo verificar el precio en vivo. Si esto se "
                        "repite, escala con escalate_to_human "
                        "(reason_category='CHECKOUT_VERIFY_FAILED')."
                    ),
                },
                ensure_ascii=False,
            )

        items_payload = [asdict(vi) for vi in result.items]
        any_discrepancy = any(vi.discrepancy for vi in result.items)

        # Precio EXACTO por ítem: live (Medusa hoy) > snapshot.
        qty_by_handle: dict[str, int] = {}
        for p in parsed:
            qty_by_handle[p.handle] = qty_by_handle.get(p.handle, 0) + p.quantity
        unit_prices: dict[str, int] = {}
        for vi, payload in zip(result.items, items_payload, strict=True):
            unit = _price_to_int(vi.live_price) or _price_to_int(vi.snapshot_price)
            payload["unit_price_cop"] = unit
            if unit is not None:
                unit_prices[vi.handle] = unit
        all_priced = bool(result.items) and all(
            vi.handle in unit_prices for vi in result.items
        )
        subtotal_cop = (
            sum(unit_prices[h] * qty_by_handle.get(h, 1) for h in unit_prices)
            if all_priced else None
        )

        quoted = await self._quoted_mismatches(ctx.session_key, unit_prices)
        quoted_amounts: list[int] = []
        for hit in quoted:
            if hit.amount not in quoted_amounts:
                quoted_amounts.append(hit.amount)
        self._persist_ledger(ctx.session_key, result.items, qty_by_handle, unit_prices, quoted_amounts)

        logger.info(
            "🧾 [TOOL verify_order_for_checkout] verified={} discrepancy={} subtotal={} quoted_mismatch={}",
            result.verified, any_discrepancy, subtotal_cop, quoted_amounts,
        )

        envelope: dict[str, Any] = {
            "verified": result.verified,
            "discrepancy": any_discrepancy,
            "items": items_payload,
            "subtotal_cop": subtotal_cop,
            "quoted_price_mismatch": bool(quoted_amounts),
            "quoted_amounts": quoted_amounts,
        }
        prices_hint = (
            " Usa EXACTAMENTE estos precios (unit_price_cop por ítem, "
            "subtotal_cop) en present_order_confirmation y register_order — "
            "cualquier otro monto se rechaza."
        )
        if any_discrepancy:
            message = (
                "Hay diferencias de precio o disponibilidad entre el snapshot "
                "y la fuente live. Comunícale al cliente con honestidad cada "
                "discrepancia (producto + precio anterior → precio actualizado) "
                "y pídele que confirme con el precio nuevo antes de cerrar el "
                "pedido. Si no acepta, registra tag RECHAZO." + prices_hint
            )
        else:
            message = (
                "Verificación OK: snapshot y live coinciden. Procede a "
                "confirmar el pedido normalmente." + prices_hint
            )
        if quoted_amounts:
            catalog_label = ", ".join(format_cop(unit_prices[h]) for h in unit_prices) or "el del catálogo"
            quoted_label = ", ".join(format_cop(a) for a in quoted_amounts)
            message += (
                f" ⚠️ Le escribiste al cliente {quoted_label} y el precio "
                f"vigente del catálogo es {catalog_label}: acláraselo con "
                "honestidad en UNA línea (\"el precio vigente del set es "
                f"{catalog_label}\") ANTES de presentar la confirmación; nunca "
                "cambies el número sin explicar de dónde sale la diferencia."
            )
        envelope["message"] = message

        return json.dumps(envelope, ensure_ascii=False)
