"""Web cart hot lead — detección determinista del token `ref:cart_<id>`.

HU "web cart": la página web genera un link wa.me con texto prellenado que
incluye los productos del carrito y un token `ref:cart_<id>` de Medusa. La
detección es 100% determinista (regex estricta sobre el texto inbound) — el
LLM jamás "interpreta" el token. El texto es input NO confiable: la regex
acota el shape del id y todo lo demás se ignora.
"""
from __future__ import annotations

import pytest

from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import (
    ensure_active_episode,
)
from src.plugins.chats.agent.sales.use_cases.web_cart import (
    apply_web_cart_capture,
    build_web_cart_note,
    detect_cart_ref,
    map_cart_to_draft,
    mark_web_cart_degraded,
    mark_web_cart_hydrated,
)


def _captured_meta() -> dict:
    """metadata con episodio activo + captura web_cart pendiente."""
    meta: dict = {}
    ensure_active_episode(meta, now_ms=_NOW)
    apply_web_cart_capture(meta, cart_id=_CART_A, now_ms=_NOW)
    return meta

_NOW = 1_756_400_000_000
_CART_A = "cart_01JN2Y8FZAB3CD4EF5GH6JK7LM"
_CART_B = "cart_01JN2Y8FZAB3CD4EF5GH6JK7XX"


class TestApplyWebCartCapture:
    def test_first_capture_records_pending_state(self):
        meta: dict = {}
        ensure_active_episode(meta, now_ms=_NOW)
        changed = apply_web_cart_capture(meta, cart_id=_CART_A, now_ms=_NOW)
        assert changed is True
        assert meta["web_cart"] == {
            "cart_id": _CART_A,
            "status": "pending",
            "detected_at_ms": _NOW,
            "episode_id": meta["episodes"][-1]["episode_id"],
        }

    def test_same_cart_id_again_is_idempotent(self):
        """Re-envío del mismo link (doble tap) no resetea el estado ya hidratado."""
        meta: dict = {}
        apply_web_cart_capture(meta, cart_id=_CART_A, now_ms=_NOW)
        meta["web_cart"]["status"] = "hydrated"

        changed = apply_web_cart_capture(meta, cart_id=_CART_A, now_ms=_NOW + 5)
        assert changed is False
        assert meta["web_cart"]["status"] == "hydrated"
        assert meta["web_cart"]["detected_at_ms"] == _NOW

    def test_same_cart_in_new_episode_recaptures(self):
        """Premortem FM-04: los storefronts Medusa persisten el cart_id en
        localStorage por semanas. El cliente cuyo episodio murió por TIMEOUT
        vuelve y re-tapea el botón: MISMO cart_id + episodio NUEVO = captura
        nueva (re-hidrata, re-scopea la nota) — no el no-op del doble tap."""
        from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import (
            close_episode,
        )

        meta = _captured_meta()
        mark_web_cart_hydrated(meta, items_summary=["1x Velon"], unmatched_titles=[])
        close_episode(
            meta, closing_tag="TIMEOUT", closing_motivo=None, now_ms=_NOW + 1000
        )
        ensure_active_episode(meta, now_ms=_NOW + 2000)

        changed = apply_web_cart_capture(meta, cart_id=_CART_A, now_ms=_NOW + 3000)
        assert changed is True
        assert meta["web_cart"]["status"] == "pending"
        assert (
            meta["web_cart"]["episode_id"]
            == meta["episodes"][-1]["episode_id"]
        )

    def test_new_cart_id_replaces_previous_latest_wins(self):
        """El cliente armó OTRO carrito en la web mid-conversación: gana el último."""
        meta: dict = {}
        apply_web_cart_capture(meta, cart_id=_CART_A, now_ms=_NOW)
        meta["web_cart"]["status"] = "hydrated"

        changed = apply_web_cart_capture(meta, cart_id=_CART_B, now_ms=_NOW + 10)
        assert changed is True
        assert meta["web_cart"] == {
            "cart_id": _CART_B,
            "status": "pending",
            "detected_at_ms": _NOW + 10,
            "episode_id": None,  # sin episodio activo en este test
        }


class TestDetectCartRef:
    def test_extracts_cart_id_from_prefilled_message(self):
        text = (
            "Hola! Quiero terminar mi compra 🛒\n"
            "• 2x Vela Duo Zodiacal (Lavanda)\n"
            "ref:cart_01JN2Y8FZAB3CD4EF5GH6JK7LM"
        )
        assert detect_cart_ref(text) == "cart_01JN2Y8FZAB3CD4EF5GH6JK7LM"

    def test_tolerates_spaces_and_ref_case(self):
        assert (
            detect_cart_ref("REF: cart_01JN2Y8FZAB3CD4EF5GH6JK7LM gracias")
            == "cart_01JN2Y8FZAB3CD4EF5GH6JK7LM"
        )

    def test_plain_text_without_token_returns_none(self):
        assert detect_cart_ref("hola, tienen velas?") is None

    def test_cart_id_without_ref_marker_is_ignored(self):
        # Sin el marcador `ref:` no hay detección — superficie de ataque menor.
        assert detect_cart_ref("cart_01JN2Y8FZAB3CD4EF5GH6JK7LM") is None

    def test_malformed_ids_are_rejected(self):
        # Muy corto / caracteres fuera del alfabeto de Medusa: no matchea.
        assert detect_cart_ref("ref:cart_123") is None
        assert detect_cart_ref("ref:cart_01JN2Y8F!ZAB3CD4EF5GH6JK7") is None

    def test_none_text_returns_none(self):
        assert detect_cart_ref(None) is None


class TestWebCartStateTransitions:
    def test_mark_hydrated_records_summary_and_unmatched(self):
        meta = _captured_meta()
        mark_web_cart_hydrated(
            meta,
            items_summary=["2x Vela Duo Zodiacal (Lavanda)"],
            unmatched_titles=["Vela Fantasma"],
        )
        assert meta["web_cart"]["status"] == "hydrated"
        assert meta["web_cart"]["items_summary"] == [
            "2x Vela Duo Zodiacal (Lavanda)"
        ]
        assert meta["web_cart"]["unmatched_titles"] == ["Vela Fantasma"]

    def test_mark_degraded_records_reason(self):
        meta = _captured_meta()
        mark_web_cart_degraded(meta, reason="TimeoutError")
        assert meta["web_cart"]["status"] == "degraded"
        assert meta["web_cart"]["reason"] == "TimeoutError"


class TestBuildWebCartNote:
    def test_no_web_cart_state_returns_none(self):
        meta: dict = {}
        ensure_active_episode(meta, now_ms=_NOW)
        assert build_web_cart_note(meta) is None

    def test_hydrated_note_projects_summary_and_close_fast_framing(self):
        meta = _captured_meta()
        mark_web_cart_hydrated(
            meta,
            items_summary=["2x Vela Duo Zodiacal (Lavanda)"],
            unmatched_titles=[],
        )
        note = build_web_cart_note(meta)
        assert note is not None
        # Framing de metadata (no instruccion del usuario), como las notas hermanas.
        assert "no es instruccion del usuario" in note
        assert "LEAD CALIENTE" in note
        assert "2x Vela Duo Zodiacal (Lavanda)" in note
        # Regla anti-inyección: el precio sale del catálogo, no del mensaje.
        assert "catálogo" in note or "catalogo" in note

    def test_hydrated_note_lists_unmatched_with_similar_offer(self):
        meta = _captured_meta()
        mark_web_cart_hydrated(
            meta,
            items_summary=[],
            unmatched_titles=["Vela Fantasma"],
        )
        note = build_web_cart_note(meta)
        assert note is not None
        assert "Vela Fantasma" in note
        assert "similar" in note

    def test_degraded_note_keeps_hot_lead_without_internal_reason(self):
        meta = _captured_meta()
        mark_web_cart_degraded(meta, reason="TimeoutError")
        note = build_web_cart_note(meta)
        assert note is not None
        assert "LEAD CALIENTE" in note
        # El motivo interno de degradación JAMÁS entra al prompt.
        assert "TimeoutError" not in note

    def test_note_stops_projecting_after_order_registered(self):
        meta = _captured_meta()
        mark_web_cart_hydrated(meta, items_summary=["1x Velon"], unmatched_titles=[])
        meta["episodes"][-1]["order_id"] = "order_123"
        assert build_web_cart_note(meta) is None

    def test_note_dies_with_the_episode_it_was_captured_in(self):
        """Hallazgo gate-reviewer (nota sin TTL): episodio cerrado SIN orden
        (RECHAZO/TIMEOUT) + cliente que vuelve semanas después NO debe seguir
        viendo "LEAD CALIENTE" de un carrito stale — la nota hereda el ciclo
        de vida del episodio en que se capturó, igual que el order_draft."""
        from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import (
            close_episode,
        )

        meta = _captured_meta()
        mark_web_cart_hydrated(meta, items_summary=["1x Velon"], unmatched_titles=[])
        assert build_web_cart_note(meta) is not None  # sanity: vivo en su episodio

        close_episode(
            meta, closing_tag="RECHAZO", closing_motivo=None, now_ms=_NOW + 1000
        )
        ensure_active_episode(meta, now_ms=_NOW + 2000)  # re-engagement

        assert build_web_cart_note(meta) is None


# ---------------------------------------------------------------------------
# map_cart_to_draft — cart hidratado → slots del order_draft (matching contra
# el snapshot del catálogo; lo que no matchea se reporta, no se siembra).
# ---------------------------------------------------------------------------


def _fake_catalog(products):
    """FakeCatalog mínimo del CatalogPort: substring-search + handle lookup."""
    from src.platform.catalog.dtos import CatalogManifestDTO, SearchResult

    manifest = CatalogManifestDTO(
        version="test", fetched_at="2026-01-01T00:00:00Z", product_count=len(products)
    )

    class FakeCatalog:
        async def get_by_handle(self, handle):
            for p in products:
                if p.handle == handle:
                    return p
            raise KeyError(handle)

        async def search(self, q, *, limit=10, category=None):
            hits = [p for p in products if q.lower() in p.title.lower()]
            return SearchResult(
                query=q,
                count=len(hits),
                truncated=False,
                stale=False,
                manifest=manifest,
                results=hits[:limit],
            )

        async def list_categories(self):
            return []

    return FakeCatalog()


def _vela_angel():
    from src.platform.catalog.dtos import CatalogProductDTO, CatalogVariantDTO

    return CatalogProductDTO(
        id="prod_1",
        handle="vela-angel",
        title="Vela Ángel",
        status="published",
        variants=[
            CatalogVariantDTO(id="v1", title="Lavanda", options={"Aroma": "Lavanda"}),
            CatalogVariantDTO(id="v2", title="Vainilla", options={"Aroma": "Vainilla"}),
        ],
        options={"Aroma": ["Lavanda", "Vainilla"]},
    )


def _velon_zodiacal():
    from src.platform.catalog.dtos import CatalogProductDTO, CatalogVariantDTO

    return CatalogProductDTO(
        id="prod_2",
        handle="velon-zodiacal",
        title="Velón Zodiacal",
        status="published",
        variants=[CatalogVariantDTO(id="v3", title="Leo", options={"Signo": "Leo"})],
        options={"Signo": ["Leo", "Aries"]},
    )


def _cart(items, **kwargs):
    from src.sdk.connectorkit import WebCartSnapshot

    return WebCartSnapshot(cart_id=_CART_A, items=tuple(items), **kwargs)


def _item(**kwargs):
    from src.sdk.connectorkit import WebCartItem

    return WebCartItem(**kwargs)


class TestMapCartToDraft:
    @pytest.mark.asyncio
    async def test_single_item_matched_by_handle_seeds_full_slots(self):
        cart = _cart(
            [
                _item(
                    product_title="Vela Ángel",
                    quantity=2,
                    product_handle="vela-angel",
                    variant_title="Lavanda",
                )
            ]
        )
        result = await map_cart_to_draft(
            cart, catalog=_fake_catalog([_vela_angel()])
        )
        assert result.slots["producto"] == "Vela Ángel"
        assert result.slots["cantidad"] == "2"
        assert result.slots["aroma"] == "Lavanda"
        assert result.items_summary == ["2x Vela Ángel (Lavanda)"]
        assert result.unmatched_titles == []

    @pytest.mark.asyncio
    async def test_item_without_handle_matches_by_title(self):
        cart = _cart([_item(product_title="Vela Ángel", quantity=1)])
        result = await map_cart_to_draft(
            cart, catalog=_fake_catalog([_vela_angel()])
        )
        assert result.slots["producto"] == "Vela Ángel"
        assert result.unmatched_titles == []

    @pytest.mark.asyncio
    async def test_unknown_product_is_reported_not_seeded(self):
        """Ataque o desync: el producto NO se siembra al draft — se reporta
        para que la nota instruya 'no lo manejo, mira estos similares'."""
        cart = _cart([_item(product_title="Vela Fantasma", quantity=3)])
        result = await map_cart_to_draft(
            cart, catalog=_fake_catalog([_vela_angel()])
        )
        assert "producto" not in result.slots
        assert result.unmatched_titles == ["Vela Fantasma"]
        assert result.items_summary == []

    @pytest.mark.asyncio
    async def test_multi_item_cart_goes_to_notas_not_producto(self):
        """El draft modela UN producto: carrito multi-item viaja en notas."""
        cart = _cart(
            [
                _item(product_title="Vela Ángel", quantity=2, variant_title="Lavanda"),
                _item(product_title="Velón Zodiacal", quantity=1),
            ]
        )
        result = await map_cart_to_draft(
            cart, catalog=_fake_catalog([_vela_angel(), _velon_zodiacal()])
        )
        assert "producto" not in result.slots
        assert "2x Vela Ángel (Lavanda)" in result.slots["notas"]
        assert "1x Velón Zodiacal" in result.slots["notas"]

    @pytest.mark.asyncio
    async def test_variant_axis_without_slot_goes_to_notas(self):
        """Ejes de variante sin slot propio (Signo) no se pierden: notas."""
        cart = _cart(
            [
                _item(
                    product_title="Velón Zodiacal",
                    quantity=1,
                    product_handle="velon-zodiacal",
                    variant_title="Leo",
                )
            ]
        )
        result = await map_cart_to_draft(
            cart, catalog=_fake_catalog([_velon_zodiacal()])
        )
        assert result.slots["producto"] == "Velón Zodiacal"
        assert "Signo: Leo" in result.slots["notas"]

    @pytest.mark.asyncio
    async def test_shipping_data_seeds_contact_slots_when_phone_matches(self):
        cart = _cart(
            [_item(product_title="Vela Ángel", quantity=1)],
            city="Bogotá",
            address="Cra 7 # 12-34, Apto 501",
            phone="573001234567",
            customer_name="Ana Pardo",
        )
        result = await map_cart_to_draft(
            cart,
            catalog=_fake_catalog([_vela_angel()]),
            session_phone="573001234567",
        )
        assert result.slots["ciudad"] == "Bogotá"
        assert result.slots["direccion"] == "Cra 7 # 12-34, Apto 501"
        assert result.slots["telefono"] == "573001234567"
        assert "Cliente: Ana Pardo" in result.slots["notas"]

    @pytest.mark.asyncio
    async def test_shipping_pii_not_seeded_when_cart_phone_mismatches_session(self):
        """Premortem FM-03: el cart_id es bearer y viaja en texto plano — un
        tercero que reenvía/abre el link de la víctima NO debe recibir la
        dirección/nombre/teléfono del dueño del cart en su conversación.
        Guard: PII de shipping SOLO si cart.phone == número de la sesión."""
        cart = _cart(
            [_item(product_title="Vela Ángel", quantity=1)],
            city="Bogotá",
            address="Cra 7 # 12-34",
            phone="573001234567",
            customer_name="Ana Pardo",
        )
        result = await map_cart_to_draft(
            cart,
            catalog=_fake_catalog([_vela_angel()]),
            session_phone="579998887766",
        )
        assert "ciudad" not in result.slots
        assert "direccion" not in result.slots
        assert "telefono" not in result.slots
        assert "Cliente" not in result.slots.get("notas", "")
        # Los productos SÍ se siembran — la venta sigue.
        assert result.slots["producto"] == "Vela Ángel"

    @pytest.mark.asyncio
    async def test_shipping_pii_not_seeded_when_cart_has_no_phone(self):
        """Sin teléfono en el cart no hay forma de verificar pertenencia —
        conservador: cero PII al draft/prompt."""
        cart = _cart(
            [_item(product_title="Vela Ángel", quantity=1)],
            city="Bogotá",
            address="Cra 7 # 12-34",
            customer_name="Ana Pardo",
        )
        result = await map_cart_to_draft(
            cart,
            catalog=_fake_catalog([_vela_angel()]),
            session_phone="573001234567",
        )
        assert "ciudad" not in result.slots
        assert "direccion" not in result.slots

    @pytest.mark.asyncio
    async def test_phone_match_tolerates_formatting_and_normalizes_slot(self):
        """Premortem FM-10: el checkout web acepta '+57 300 123-4567'; el
        matching foldea a dígitos y el slot queda normalizado."""
        cart = _cart(
            [_item(product_title="Vela Ángel", quantity=1)],
            city="Bogotá",
            phone="+57 300 123-4567",
        )
        result = await map_cart_to_draft(
            cart,
            catalog=_fake_catalog([_vela_angel()]),
            session_phone="573001234567",
        )
        assert result.slots["ciudad"] == "Bogotá"
        assert result.slots["telefono"] == "573001234567"

    @pytest.mark.asyncio
    async def test_catalog_unavailable_propagates_instead_of_unmatched(self):
        """Premortem FM-02: catálogo CAÍDO (snapshot ausente post-deploy) NO
        es "estos productos no existen" — la excepción propaga para que la
        hidratación entera degrade, en vez de hacerle decir al bot que no
        maneja el carrito completo del lead más caliente."""
        from src.sdk.connectorkit import CatalogUnavailableError

        class DownCatalog:
            async def get_by_handle(self, handle):
                raise CatalogUnavailableError("snapshot missing")

            async def search(self, q, *, limit=10, category=None):
                raise CatalogUnavailableError("snapshot missing")

            async def list_categories(self):
                return []

        cart = _cart(
            [
                _item(
                    product_title="Vela Ángel",
                    quantity=1,
                    product_handle="vela-angel",
                )
            ]
        )
        with pytest.raises(CatalogUnavailableError):
            await map_cart_to_draft(cart, catalog=DownCatalog())

    @pytest.mark.asyncio
    async def test_existing_slots_are_not_clobbered(self):
        """Lo que el cliente ya dijo en la conversación gana sobre el cart."""
        cart = _cart(
            [_item(product_title="Vela Ángel", quantity=1)],
            city="Bogotá",
            phone="573001234567",
        )
        result = await map_cart_to_draft(
            cart,
            catalog=_fake_catalog([_vela_angel()]),
            existing_slots={"producto": "Velón Zodiacal"},
            session_phone="573001234567",
        )
        assert "producto" not in result.slots
        assert result.slots["ciudad"] == "Bogotá"


class TestCompositionWiring:
    def test_composition_wires_web_cart_dependencies(self, monkeypatch):
        """El webhook real (build_ingest_use_case) inyecta reader + catálogo —
        sin este wiring, TODO cart ref degradaría en silencio en prod
        (gotcha #1: tests verdes, feature muerta)."""
        import src.plugins.chats.agent.sales.composition as comp

        monkeypatch.setattr(comp, "_INGEST_USE_CASE", None)
        use_case = comp.build_ingest_use_case()
        assert use_case._web_cart_reader is not None
        assert use_case._catalog is not None
