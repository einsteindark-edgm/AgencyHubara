"""El gancho de remarketing solo afirma datos de producto que existen.

Incidente 2026-09-23/25 (sesión de un lead CTWA «Velas Artesanales»): el
cliente mandó una foto (Cubo Love, Cilindro Love, Cubo de corazón y una vela
de dragón que NO vendemos) y escribió «Estás y en vaso también». Ventas nunca
aclaró que no hay velas en vaso ni de dragón. El agente de remarketing no tiene
catálogo (su única tool es transferir a Ventas) y la escalera le pedía «un
detalle concreto del producto que miró» — así que lo inventó, toque tras toque:
  * «las de vaso son las más pedidas para aromatizar espacios grandes»
  * «las de cubo con corazones y la del dragón son de las más lindas»
  * «el Cubo Love también viene en vaso» (Cubo Love tiene UNA presentación)
Además de la popularidad inventada («los más pedidos»), sin datos de ventas.

Fix: el contexto del gancho trae la ficha REAL del catálogo (snapshot que ya
lee Ventas) de los productos que se nombraron en la charla, y el trigger la
declara como la única fuente de datos de producto.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment

from src.plugins.chats.agent.remarketing.activities import context as context_mod
from src.plugins.chats.agent.remarketing.activities.context import (
    read_remarketing_context_activity,
)
from src.plugins.chats.agent.remarketing.contracts import (
    RemarketingContext,
    RemarketingSessionInput,
)
from src.plugins.chats.agent.remarketing.prompts import build_remarketing_trigger
from src.plugins.chats.agent.remarketing.use_cases.context import catalog_facts_for
from src.sdk.catalogkit import (
    CatalogProductDTO,
    CatalogUnavailableError,
    CatalogVariantDTO,
)

_TAGS = ["Aroma: Café", "Aroma: Lavanda", "Color: Azul", "Color: Rosado"]


def _product(title: str, handle: str, description: str = "", **extra) -> CatalogProductDTO:
    return CatalogProductDTO(
        id=f"prod_{handle}",
        handle=handle,
        title=title,
        status="published",
        description=description,
        variants=[CatalogVariantDTO(id=f"var_{handle}", title="Unico")],
        tags=list(_TAGS),
        options={"Unico": ["Unico"]},
        **extra,
    )


CUBO_LOVE = _product(
    "Cubo Love",
    "cubo-love",
    "Una propuesta moderna y tipográfica de la clásica vela cúbica. Cada una de "
    "sus caras presenta la palabra \"LOVE\" grabada en bajorrelieve.",
)
CUBO_CORAZON = _product("Cubo de corazón", "cubo-de-corazon", "Vela de corazones entrelazados.")
KOALA = _product("Velón Koala", "velon-koala", "Velón con forma de koala.")
DUO = CatalogProductDTO(
    id="prod_duo",
    handle="duo-zodiacal",
    title="Duo Zodiacal",
    status="published",
    description="Dos velas de tu signo.",
    variants=[CatalogVariantDTO(id="v1", title="Aries"), CatalogVariantDTO(id="v2", title="Leo")],
    options={"Signo": ["Aries", "Leo"]},
)
CATALOG = [CUBO_LOVE, CUBO_CORAZON, KOALA, DUO]

TRANSCRIPT = (
    "Cliente: [el cliente envió una foto: vela azul en forma de cubo con texto 'Love', "
    "vela rosa con diseño de dragón]\n"
    "Cliente: Estás y en vaso también\n"
    "Asesor: *Cubo Love*: $21.000\n*Cubo de corazon*: $22.000"
)


class TestCatalogFacts:
    def test_names_every_product_that_exists(self) -> None:
        facts = catalog_facts_for(CATALOG, mentioned=TRANSCRIPT)
        for title in ("Cubo Love", "Cubo de corazón", "Velón Koala", "Duo Zodiacal"):
            assert title in facts

    def test_mentioned_product_gets_its_real_ficha(self) -> None:
        facts = catalog_facts_for(CATALOG, mentioned=TRANSCRIPT)
        assert "bajorrelieve" in facts  # descripción real del Cubo Love
        assert "Aromas: Café, Lavanda" in facts
        assert "Colores: Azul, Rosado" in facts

    def test_single_variant_product_declares_a_single_presentation(self) -> None:
        facts = catalog_facts_for([CUBO_LOVE], mentioned="Cubo Love")
        assert "presentación única" in facts.lower()

    def test_real_options_are_listed(self) -> None:
        facts = catalog_facts_for([DUO], mentioned="el duo zodiacal")
        assert "Signo: Aries, Leo" in facts
        assert "presentación única" not in facts.lower()

    def test_match_ignores_accents_and_case(self) -> None:
        # «Cubo de corazon» (sin tilde) en el chat → ficha de «Cubo de corazón».
        facts = catalog_facts_for([CUBO_CORAZON], mentioned="me gustó el CUBO DE CORAZON")
        assert "corazones entrelazados" in facts

    def test_unmentioned_product_is_named_but_has_no_ficha(self) -> None:
        facts = catalog_facts_for(CATALOG, mentioned=TRANSCRIPT)
        assert "Velón Koala" in facts
        assert "forma de koala" not in facts

    def test_empty_catalog_is_empty_facts(self) -> None:
        assert catalog_facts_for([], mentioned=TRANSCRIPT) == ""


# ---------------------------------------------------------------- activity


class _Catalog:
    def __init__(self, products: list[CatalogProductDTO]) -> None:
        self._products = products

    async def search(self, q: str = "", *, limit: int = 10, category: str | None = None):
        from types import SimpleNamespace

        return SimpleNamespace(results=self._products[:limit])


class _Unavailable:
    async def search(self, q: str = "", *, limit: int = 10, category: str | None = None):
        raise CatalogUnavailableError("snapshot not found")


def _seed_session(vault: Path, sid: str) -> None:
    d = vault / sid
    (d / "sessions").mkdir(parents=True)
    (d / "metadata.json").write_text(
        json.dumps({"tag": "INTERESADO", "motivo": "se le cotizaron Cubo Love y Cubo de corazón"}),
        encoding="utf-8",
    )
    lines = [
        {"role": "user", "content": "Estás y en vaso también"},
        {"role": "assistant", "content": "*Cubo Love*: $21.000"},
    ]
    (d / "sessions" / f"{sid}.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in lines), encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_activity_attaches_real_catalog_facts(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(context_mod, "get_catalog_client", lambda: _Catalog(CATALOG))
    sid = "wa_catalog_grounding"
    _seed_session(_isolate_vault_dir, sid)

    ctx = await ActivityEnvironment().run(read_remarketing_context_activity, sid)

    assert "bajorrelieve" in ctx.catalog_facts  # ficha del Cubo Love (transcript)
    assert "corazones entrelazados" in ctx.catalog_facts  # nombrado en el motivo
    assert "forma de koala" not in ctx.catalog_facts


@pytest.mark.asyncio
async def test_activity_tolerates_catalog_unavailable(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(context_mod, "get_catalog_client", lambda: _Unavailable())
    sid = "wa_catalog_down"
    _seed_session(_isolate_vault_dir, sid)

    ctx = await ActivityEnvironment().run(read_remarketing_context_activity, sid)

    assert ctx.catalog_facts == ""
    assert "Cubo Love" in ctx.transcript  # el resto del contexto sigue intacto


# ---------------------------------------------------------------- trigger

_LADDER = dict(has_order_draft=False, transcript=TRANSCRIPT, touch_number=3, silence_minutes=150)


def test_trigger_carries_the_catalog_as_the_only_product_source() -> None:
    facts = catalog_facts_for(CATALOG, mentioned=TRANSCRIPT)
    text = build_remarketing_trigger("cotizó Cubo Love", catalog_facts=facts, **_LADDER)
    assert facts in text
    assert "CATÁLOGO REAL" in text
    assert "única fuente" in text


def test_trigger_forbids_inventing_presentations_and_popularity() -> None:
    text = build_remarketing_trigger("cotizó Cubo Love", catalog_facts="x", **_LADDER)
    low = text.lower()
    # Lo que el cliente pidió y NO existe (vaso, dragón) no se confirma.
    assert "no lo confirmes" in low
    # Tus propios ganchos anteriores no son fuente de datos.
    assert "ganchos anteriores" in low and "no son fuente" in low
    # Popularidad inventada.
    assert "más pedid" in low


def test_ladder_angle_is_anchored_to_the_catalog() -> None:
    text = build_remarketing_trigger("cotizó Cubo Love", catalog_facts="x", **_LADDER)
    assert "un detalle concreto del producto que miró" not in text
    assert "un detalle concreto que esté en el CATÁLOGO REAL" in text


def test_without_catalog_the_hook_affirms_no_product_attributes() -> None:
    text = build_remarketing_trigger("cotizó Cubo Love", **_LADDER)
    assert "CATÁLOGO REAL" not in text.split("REGLAS DEL GANCHO")[0]
    assert "no tienes el catálogo" in text.lower()


# ------------------------------------------------ lo que NO existe (guarda)

from src.plugins.chats.shared.product_truth import (  # noqa: E402
    invented_product_claim,
    unavailable_terms,
)

#: Lo que el cliente escribió en el episodio del incidente (+ el motivo).
CUSTOMER_TEXT = (
    "[el cliente vino desde un anuncio de Facebook/Instagram, titulado 'Velas "
    "Artesanales', (¿Te ha pasado que enciendes una vela para relajarte y a los "
    "10 minutos tienes dolor de cabeza?)]\n¡Hola! Quiero más información sobre "
    "las velas aromaticas\nLas dos\nSí para aromatizar la casa\nNo tengo el "
    "catálogo\nOk\n[el cliente envió una foto: vela azul en forma de cubo con "
    "texto 'Love', vela rosa en forma de cilindro, vela roja en forma de cubo de "
    "corazones, vela rosa con diseño de dragón]\nEstás y en vaso también\n"
    "Cuáles son\nSí cuánto vale\n"
    "Cliente pidió velas aromáticas para casa y regalo, vio el catálogo, "
    "preguntó por las de vaso y por precios; se le cotizaron Cubo Love, "
    "Cilindro Love y Cubo de corazón y no respondió."
)
_HAYSTACK_PRODUCTS = [
    _product("Cubo Love", "cubo-love", "Vela cúbica en forma de cubo con la palabra LOVE."),
    _product("Cubo de corazón", "cubo-de-corazon", "Pequeños corazones entrelazados."),
    _product("Cilindro Love", "cilindro-love", "Vela en forma de cilindro con diseño tipográfico."),
]


class TestUnavailableTerms:
    def test_incident_container_and_design_are_detected(self) -> None:
        terms = unavailable_terms(CUSTOMER_TEXT, _HAYSTACK_PRODUCTS)
        assert "vaso" in terms
        assert "dragón" in terms

    def test_catalog_words_and_generic_shop_words_are_not_flagged(self) -> None:
        terms = unavailable_terms(CUSTOMER_TEXT, _HAYSTACK_PRODUCTS)
        for ok in ("cubo", "corazones", "cilindro", "catálogo", "precios", "velas", "casa"):
            assert ok not in terms

    def test_ad_annotation_is_not_something_the_customer_asked_for(self) -> None:
        # «dolor de cabeza» sale del anuncio, no de un pedido del cliente: un
        # gancho que lo retoma es legítimo.
        assert "cabeza" not in unavailable_terms(CUSTOMER_TEXT, _HAYSTACK_PRODUCTS)

    def test_without_catalog_nothing_is_flagged(self) -> None:
        # Sin catálogo no se puede saber qué no existe: la guarda no adivina.
        assert unavailable_terms(CUSTOMER_TEXT, []) == []


class TestInventedProductClaim:
    def test_incident_hook_is_caught(self) -> None:
        hook = "Te recordé y quería contarte que el Cubo Love también viene en vaso 🤍"
        assert invented_product_claim(hook, ["vaso", "dragón"]) == "vaso"

    def test_plural_and_accents_are_tolerated(self) -> None:
        assert invented_product_claim("las VASOS de la sala", ["vaso"]) == "vaso"
        assert invented_product_claim("y la del dragon", ["dragón"]) == "dragón"

    def test_word_inside_another_word_is_not_a_claim(self) -> None:
        assert invented_product_claim("te envaso el pedido", ["vaso"]) is None

    def test_invented_popularity_is_caught_without_terms(self) -> None:
        assert invented_product_claim("las de cubo son las más pedidas 🕯️", [])
        assert invented_product_claim("son de las más lindas para regalar", [])
        assert invented_product_claim("lavanda y verde menta son los favoritos", [])

    def test_a_clean_hook_passes(self) -> None:
        for hook in (
            "Hola de nuevo 🌿 Quedó pendiente elegir el aroma para tu Cubo Love. ¿Te ayudo? 🤍",
            "¿Cuál es tu favorito de los que viste? ✨",
            "Te recordé y quería saber si pudiste mirar el catálogo 🌿",
        ):
            assert invented_product_claim(hook, ["vaso", "dragón"]) is None, hook


@pytest.mark.asyncio
async def test_activity_detects_what_the_customer_asked_that_does_not_exist(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(context_mod, "get_catalog_client", lambda: _Catalog(_HAYSTACK_PRODUCTS))
    sid = "wa_catalog_terms"
    _seed_session(_isolate_vault_dir, sid)

    ctx = await ActivityEnvironment().run(read_remarketing_context_activity, sid)

    assert ctx.unavailable_terms == ["vaso"]


def test_trigger_names_what_does_not_exist() -> None:
    text = build_remarketing_trigger(
        "cotizó Cubo Love", catalog_facts="x", unavailable_terms=["vaso", "dragón"], **_LADDER
    )
    assert "«vaso», «dragón»" in text
    assert "NO existe en el catálogo" in text
    # …y la regla 8 lo repite al final, donde pesa más: el A/B con el modelo
    # de prod bajó de 7/10 ganchos bloqueados a 0/10 con esta línea.
    rules = text.split("REGLAS DEL GANCHO")[1]
    assert "En esta charla eso es «vaso», «dragón»: si tu gancho lo nombra, NO se envía." in rules


def test_without_catalog_nothing_is_named_as_missing() -> None:
    text = build_remarketing_trigger("cotizó Cubo Love", unavailable_terms=["vaso"], **_LADDER)
    assert "«vaso»" not in text


# ---------------------------------------------------------------- workflow


async def _run_guarded_workflow(tmp_path: Path, llm_content: str, terms: list[str]):
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from src.plugins.chats.agent.remarketing.workflows.remarketing import (
        RemarketingSessionWorkflow,
    )
    from tests.plugins.chats import test_remarketing_abstention_workflow as harness

    @harness.activity.defn(name="read_remarketing_context_activity")
    async def fake_context(session_id: str) -> RemarketingContext:
        return RemarketingContext(catalog_facts="x", unavailable_terms=terms)

    tracker = harness.Tracker()
    tracker.recorded_turns = []

    @harness.activity.defn(name="record_turn")
    async def fake_record_turn(input) -> None:
        tracker.recorded_turns.append(input)

    overridden = {"read_remarketing_context_activity", "record_turn"}
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    activities = [
        a for a in harness._make_fake_activities(tracker, llm_content=llm_content, workspace_path=str(workspace))
        if a.__temporal_activity_definition.name not in overridden
    ] + [fake_context, fake_record_turn]
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue=harness.REMARKETING_QUEUE,
            workflows=[RemarketingSessionWorkflow], activities=activities,
        ):
            handle = await env.client.start_workflow(
                RemarketingSessionWorkflow.run,
                RemarketingSessionInput(session_id="wa_truth_test", motivo="cliente interesado"),
                id="remarketing-wa_truth_test", task_queue=harness.REMARKETING_QUEUE,
            )
            await handle.result()
    return tracker


@pytest.mark.asyncio
async def test_hook_that_invents_a_presentation_is_not_sent(tmp_path: Path) -> None:
    tracker = await _run_guarded_workflow(
        tmp_path,
        "Te recordé y quería contarte que el Cubo Love también viene en vaso, por si "
        "lo prefieres así para la casa 🤍 ¿Te muestro cómo se ve? ✨",
        ["vaso", "dragón"],
    )
    assert tracker.send_whatsapp_calls == [], "gancho con invento → NINGÚN mensaje al cliente"
    assert tracker.persist_calls == []
    assert tracker.claim_calls == ["remarketing", "ventas"], "bloqueado ≈ abstención"


@pytest.mark.asyncio
async def test_truthful_hook_still_sends(tmp_path: Path) -> None:
    hook = "Hola de nuevo 🌿 Quedó pendiente elegir el aroma para tu Cubo Love. ¿Te ayudo? 🤍"
    tracker = await _run_guarded_workflow(tmp_path, hook, ["vaso"])
    assert tracker.send_whatsapp_calls == [hook]


@pytest.mark.asyncio
async def test_blocked_hook_does_not_enter_the_llm_history(tmp_path: Path) -> None:
    """Si el gancho bloqueado quedara grabado, el siguiente toque lo copiaría
    (así se propagó «las de vaso» toque tras toque, incidente 2026-09-25)."""
    tracker = await _run_guarded_workflow(
        tmp_path, "el Cubo Love también viene en vaso 🤍", ["vaso"]
    )
    assert "vaso" not in json.dumps(tracker.recorded_turns, default=str, ensure_ascii=False)


@pytest.mark.asyncio
async def test_truthful_hook_is_recorded(tmp_path: Path) -> None:
    hook = "Hola de nuevo 🌿 ¿Te ayudo a elegir el aroma de tu Cubo Love? 🤍"
    tracker = await _run_guarded_workflow(tmp_path, hook, ["vaso"])
    assert "Cubo Love" in json.dumps(tracker.recorded_turns, default=str, ensure_ascii=False)
