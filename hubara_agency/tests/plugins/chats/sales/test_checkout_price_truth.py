"""`verify_order_for_checkout` = la fuente de precios del cierre (incidente
run ebbc203d, 2026-09-16).

En ese run el LLM le dijo a la clienta "$45.000" (precio del anuncio), la
verificación comparó snapshot vs live (49.500 = 49.500) y dijo "OK", y el
resumen salió a $49.500 sin que nadie explicara el cambio. Ahora la tool:

  1. Devuelve `unit_price_cop` (int) por ítem y `subtotal_cop`: los precios
     EXACTOS que el LLM debe usar en `present_order_confirmation` /
     `register_order` (el live si Medusa cambió, si no el snapshot).
  2. Persiste `metadata.checkout_verification` (ledger por handle) — las
     tools de cierre aceptan solo esos precios.
  3. Cruza lo que el bot ESCRIBIÓ en el episodio (historial de la sesión)
     contra el catálogo: un monto en una oración de precio de producto que
     no es precio de catálogo → `quoted_price_mismatch=true` + instrucción
     de aclararlo ANTES de presentar la confirmación.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog import (
    CatalogManifestDTO,
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
    ProductNotFoundError,
    SearchResult,
)
from src.platform.catalog.checkout_port import (
    CheckoutItem,
    CheckoutVerification,
    VerifiedItem,
)
from src.platform.session_history import FilesystemMessageHistoryStore
from src.platform.state import FilesystemMetadataStore
from src.plugins.chats.agent.sales.tools.checkout import VerifyOrderForCheckoutTool

KEY = "wa_test_checkout_truth"
EPISODE_START_MS = 1_789_593_000_000  # 2026-09-16T21:10:00Z aprox


def _product(handle: str, title: str, price: str) -> CatalogProductDTO:
    return CatalogProductDTO(
        id=f"prod_{handle}", handle=handle, title=title, status="published",
        variants=[CatalogVariantDTO(
            id=f"variant_{handle}", title="Unico",
            prices=[CatalogPriceDTO(amount=price, currency_code="cop")],
        )],
    )


_PRODUCTS = {
    "trilogia-del-terror": _product("trilogia-del-terror", "Trilogía del Terror", "49500"),
    "calabaza": _product("calabaza", "Calabaza", "16000"),
    "momia": _product("momia", "Momia", "17500"),
    "fantasma": _product("fantasma", "Fantasma", "19000"),
}


class FakeCatalog:
    async def search(self, q: str, *, limit: int = 10, **_: object) -> SearchResult:
        products = list(_PRODUCTS.values())
        return SearchResult(
            query=q, count=len(products), truncated=False, stale=False,
            manifest=CatalogManifestDTO(version="v1", fetched_at="2026-09-16T00:00:00Z", product_count=len(products)),
            results=products,
        )

    async def get_by_handle(self, handle: str) -> CatalogProductDTO:
        try:
            return _PRODUCTS[handle]
        except KeyError:
            raise ProductNotFoundError(handle) from None


class FakeVerifier:
    def __init__(self, *, live: str | None = None, snapshot: str = "49500") -> None:
        self.live = snapshot if live is None else live
        self.snapshot = snapshot
        self.calls: list[list[CheckoutItem]] = []

    async def verify_items(self, items: list[CheckoutItem]) -> CheckoutVerification:
        self.calls.append(items)
        return CheckoutVerification(
            verified=True, catalog_available=True,
            items=[VerifiedItem(
                handle=it.handle, title=_PRODUCTS[it.handle].title,
                snapshot_price=self.snapshot, live_price=self.live, currency="cop",
                in_stock=True, discrepancy=self.snapshot != self.live,
            ) for it in items],
        )


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(session_key=KEY, channel="whatsapp", chat_id=KEY)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _seed(vault: Path, assistant_texts: list[str], *, before_episode: list[str] = ()) -> Path:
    meta = vault / KEY / "metadata.json"
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(json.dumps({
        "episodes": [{"episode_id": "ep_001", "started_at_ms": EPISODE_START_MS, "closed_at_ms": None,
                      "order_draft": {"slots": {"producto": "Trilogía del Terror", "cantidad": "1"},
                                      "updated_at_ms": EPISODE_START_MS + 1000,
                                      "confirmed_at_ms": EPISODE_START_MS + 2000, "confirmed_by": "text"}}],
    }, ensure_ascii=False), encoding="utf-8")
    hist = vault / KEY / "sessions" / f"{KEY}.jsonl"
    hist.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, text in enumerate(before_episode):
        lines.append({"role": "assistant", "content": text, "timestamp": _iso(EPISODE_START_MS - 100_000 + i)})
    lines.append({"role": "user", "content": "Tienes pago contra entrega", "timestamp": _iso(EPISODE_START_MS + 10_000)})
    for i, text in enumerate(assistant_texts):
        lines.append({"role": "assistant", "content": text, "timestamp": _iso(EPISODE_START_MS + 20_000 + i)})
    hist.write_text("".join(json.dumps(ln, ensure_ascii=False) + "\n" for ln in lines), encoding="utf-8")
    return meta


def _reader(vault: Path):
    return FilesystemMessageHistoryStore(vault).read_events


def _tool(vault: Path, verifier: FakeVerifier | None = None) -> VerifyOrderForCheckoutTool:
    return VerifyOrderForCheckoutTool(
        workspace=str(vault), verifier=verifier or FakeVerifier(), catalog=FakeCatalog(), metadata_store=FilesystemMetadataStore(vault),
        history_reader=_reader(vault),
    )


async def _verify(tool, ctx, handle: str = "trilogia-del-terror", qty: int = 1) -> dict:
    return json.loads(await tool.execute_with_context(ctx, items=[{"handle": handle, "quantity": qty}]))


_AD_PRICE_TEXT = (
    "El set de la Trilogía del Terror tiene un valor de *$45.000 COP*.\n\n"
    "Sobre el pago: sí manejamos contra entrega, y aplica justo desde $45.000 en productos, "
    "así que tu pedido califica. El valor del envío lo confirma la transportadora al despachar.\n\n"
    "¿Lo dejamos así?"
)


@pytest.mark.asyncio
async def test_envelope_carries_catalog_unit_prices_and_subtotal(ctx, _isolate_vault_dir: Path) -> None:
    _seed(_isolate_vault_dir, [])
    result = await _verify(_tool(_isolate_vault_dir), ctx, qty=2)
    assert result["verified"] is True
    assert result["items"][0]["unit_price_cop"] == 49500
    assert result["subtotal_cop"] == 99000
    assert "unit_price_cop" in result["message"]


@pytest.mark.asyncio
async def test_live_price_wins_when_medusa_changed(ctx, _isolate_vault_dir: Path) -> None:
    _seed(_isolate_vault_dir, [])
    result = await _verify(_tool(_isolate_vault_dir, FakeVerifier(live="50000")), ctx)
    assert result["discrepancy"] is True
    assert result["items"][0]["unit_price_cop"] == 50000
    assert result["subtotal_cop"] == 50000


@pytest.mark.asyncio
async def test_verification_ledger_is_persisted(ctx, _isolate_vault_dir: Path) -> None:
    meta = _seed(_isolate_vault_dir, [])
    await _verify(_tool(_isolate_vault_dir, FakeVerifier(live="50000")), ctx)
    ledger = json.loads(meta.read_text(encoding="utf-8"))["checkout_verification"]
    entry = ledger["items"]["trilogia-del-terror"]
    assert entry["snapshot_price_cop"] == 49500
    assert entry["live_price_cop"] == 50000
    assert entry["unit_price_cop"] == 50000
    assert entry["quantity"] == 1
    assert ledger["verified_at_ms"] > 0


@pytest.mark.asyncio
async def test_quoted_ad_price_is_detected(ctx, _isolate_vault_dir: Path) -> None:
    """El run real: "tiene un valor de $45.000" — 45.000 no es precio de catálogo."""
    _seed(_isolate_vault_dir, [_AD_PRICE_TEXT])
    result = await _verify(_tool(_isolate_vault_dir), ctx)
    assert result["verified"] is True
    assert result["quoted_price_mismatch"] is True
    assert result["quoted_amounts"] == [45000]
    msg = result["message"]
    assert "$45.000" in msg and "$49.500" in msg
    assert "ANTES" in msg


@pytest.mark.asyncio
async def test_quoted_mismatch_is_persisted_for_the_summary_tool(ctx, _isolate_vault_dir: Path) -> None:
    meta = _seed(_isolate_vault_dir, [_AD_PRICE_TEXT])
    await _verify(_tool(_isolate_vault_dir), ctx)
    ledger = json.loads(meta.read_text(encoding="utf-8"))["checkout_verification"]
    assert ledger["quoted_amounts_mismatch"] == [45000]


@pytest.mark.asyncio
async def test_policy_sentence_amounts_are_not_mismatches(ctx, _isolate_vault_dir: Path) -> None:
    _seed(_isolate_vault_dir, [
        "El contra entrega aplica desde $45.000 en productos; vas en $49.500 🤍",
        "El envío mínimo a nivel nacional es $16.940 y se confirma al despachar.",
    ])
    result = await _verify(_tool(_isolate_vault_dir), ctx)
    assert result["quoted_price_mismatch"] is False
    assert result["quoted_amounts"] == []


@pytest.mark.asyncio
async def test_other_catalog_product_price_is_not_a_mismatch(ctx, _isolate_vault_dir: Path) -> None:
    _seed(_isolate_vault_dir, ["La Calabaza sola vale $16.000 y la Momia $17.500."])
    result = await _verify(_tool(_isolate_vault_dir), ctx)
    assert result["quoted_price_mismatch"] is False


@pytest.mark.asyncio
async def test_texts_before_the_episode_are_ignored(ctx, _isolate_vault_dir: Path) -> None:
    _seed(_isolate_vault_dir, [], before_episode=["Ese cubo tenía un valor de $30.000 el mes pasado."])
    result = await _verify(_tool(_isolate_vault_dir), ctx)
    assert result["quoted_price_mismatch"] is False


@pytest.mark.asyncio
async def test_catalog_unavailable_still_reports_verification(ctx, _isolate_vault_dir: Path) -> None:
    """Sin catálogo para el allowlist, la verificación live sigue valiendo y
    la cruzada de montos se degrada (no bloquea)."""
    _seed(_isolate_vault_dir, [_AD_PRICE_TEXT])
    tool = VerifyOrderForCheckoutTool(
        workspace=str(_isolate_vault_dir), verifier=FakeVerifier(), catalog=None, metadata_store=FilesystemMetadataStore(_isolate_vault_dir),
        history_reader=_reader(_isolate_vault_dir),
    )
    result = await _verify(tool, ctx)
    assert result["verified"] is True
    assert result["items"][0]["unit_price_cop"] == 49500
    # con solo los precios verificados como referencia, 45.000 sigue sin explicación
    assert result["quoted_price_mismatch"] is True


@pytest.mark.asyncio
async def test_without_history_reader_prices_still_work(ctx, _isolate_vault_dir: Path) -> None:
    """Sin lector de historial (wiring incompleto) NO se cae: precios y ledger
    siguen; solo se salta la cruzada de montos citados."""
    _seed(_isolate_vault_dir, [_AD_PRICE_TEXT])
    tool = VerifyOrderForCheckoutTool(
        workspace=str(_isolate_vault_dir), verifier=FakeVerifier(), catalog=FakeCatalog(), metadata_store=FilesystemMetadataStore(_isolate_vault_dir),
    )
    result = await _verify(tool, ctx)
    assert result["items"][0]["unit_price_cop"] == 49500
    assert result["quoted_price_mismatch"] is False
