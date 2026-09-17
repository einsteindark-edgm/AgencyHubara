"""Precio = CATÁLOGO — helpers compartidos por las tools de cierre.

Incidente run ebbc203d (2026-09-16) + defensa contra inyección de precios:
`request_shipping_details`, `verify_order_for_checkout`,
`present_order_confirmation` y `register_order` comparten estas tres reglas:

  * El precio unitario de un producto sale del catálogo (snapshot), o del
    precio LIVE que `verify_order_for_checkout` dejó en el ledger
    `metadata.checkout_verification` (Medusa cambió y el snapshot aún no
    refrescó). Nunca de un monto que mande el LLM, el cliente o el anuncio.
  * Sin referencia (catálogo caído) no hay contra qué comparar: las tools
    degradan a sus otras guardas (SEC-07, gate humano de pago) en vez de
    bloquear la venta.
  * Formato es-CO para el LLM y el cliente: "$49.500".

Sin Temporal, sin red: lectura del vault por el store de metadata del plugin
(`sales.state`, shim del store de platform — R-DIP #7: tools inertes).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from src.plugins.chats.agent.sales.state import FilesystemMetadataStore


def catalog_unit_price(product: Any) -> int | None:
    """Precio unitario (int COP) de la primera variante; COP gana sobre otras
    monedas (caso wa_573125671604: "$35.000 usd" por tomar prices[0] ciego)."""
    variants = getattr(product, "variants", None) or []
    if not variants:
        return None
    prices = getattr(variants[0], "prices", None) or []
    if not prices:
        return None
    chosen = next(
        (p for p in prices if str(getattr(p, "currency_code", "")).lower() == "cop"),
        prices[0],
    )
    try:
        value = int(round(float(chosen.amount)))
    except (TypeError, ValueError, AttributeError):
        return None
    return value if value > 0 else None


def format_cop(amount: int) -> str:
    """$49.500 — miles con punto, sin decimales."""
    return "$" + f"{int(amount):,}".replace(",", ".")


def read_checkout_ledger(vault_dir: Path, session_key: str) -> dict[str, Any]:
    """`metadata.checkout_verification` o `{}` (sin ledger / vault ilegible)."""
    try:
        data = FilesystemMetadataStore(Path(vault_dir)).read(session_key) or {}
    except Exception:  # noqa: BLE001 — sin metadata legible: sin ledger
        return {}
    ledger = data.get("checkout_verification")
    return ledger if isinstance(ledger, dict) else {}


def accepted_prices(
    vault_dir: Path, session_key: str, handle: str, *, snapshot_price_cop: int | None
) -> set[int]:
    """Precios válidos para `handle` en esta sesión: snapshot + live verificado.
    Vacío = sin referencia."""
    accepted: set[int] = set()
    if snapshot_price_cop is not None and int(snapshot_price_cop) > 0:
        accepted.add(int(snapshot_price_cop))
    entry = ((read_checkout_ledger(vault_dir, session_key).get("items") or {}).get(handle)) or {}
    live = entry.get("live_price_cop")
    try:
        if live is not None and int(live) > 0:
            accepted.add(int(live))
    except (TypeError, ValueError):
        pass
    return accepted


def quoted_amounts_mismatch(vault_dir: Path, session_key: str) -> list[int]:
    """Montos que el bot le escribió al cliente y no son del catálogo, según
    la última verificación (`verify_order_for_checkout`)."""
    raw = read_checkout_ledger(vault_dir, session_key).get("quoted_amounts_mismatch") or []
    out: list[int] = []
    for value in raw:
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            continue
    return out
