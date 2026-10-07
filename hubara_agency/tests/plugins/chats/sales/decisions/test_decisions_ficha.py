"""El producto desde el que escribe el cliente, en el turno del bot nuevo.

Conversación de prueba del 2026-10-07 (···1604, run 95f3f563, turno 3): el bot
mandó la lista de 4 productos de Halloween, el cliente abrió la Calabaza y
desde su ficha tocó «Enviar mensaje a la empresa»: «Me gusta esta». La nota
del ingest llegó al modelo («…desde la ficha de Calabaza… no le preguntes qué
producto busca»), pero el motor (ventas-3, `turno-v3`) calculó la etapa solo
con el borrador, vacío, y cerró el bloque con «[ETAPA] Descubrimiento.
Siguiente paso: ayúdale a escoger un producto (catálogo o ficha).». El modelo
obedeció la orden: «Buena elección 🎃 ¿Cuál de las cuatro te gustó?».

El motor ahora sabe de la ficha (la misma que proyecta la nota del ingest:
resuelta, del episodio, sin pedido) y la política `turno-v4` (paquete
`ventas-4`) le da al LLM el paso con ese producto en vez de «escoge uno».
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.plugins.chats.agent.sales.decisions.context import TurnContext, ViewedProduct, Window
from src.plugins.chats.agent.sales.decisions.questionnaire import load_questionnaire
from src.plugins.chats.agent.sales.use_cases.web_product_ref import CATALOG_ORIGIN
from src.sdk.connectorkit import PerceptionResult, TypedAnswer

SID = "wa_573001234567"
NOW = 1_791_389_181_000
LIST_SENT = "🛍️ El bot envió el catálogo con 4 productos"
RAFAGA = load_questionnaire("rafaga-v3")


def _card(**over: object) -> dict:
    return {
        "sku": "HUB-CALABAZA", "source": None, "origin": CATALOG_ORIGIN, "status": "resolved",
        "detected_at_ms": NOW, "episode_id": "ep_012", "handle": "calabaza", "title": "Calabaza", **over,
    }


def _metadata(card: dict | None, **episode: object) -> dict:
    metadata: dict = {
        "last_inbound_message_id": "wamid.ME_GUSTA",
        "episodes": [{"episode_id": "ep_012", "started_at_ms": NOW - 60_000, "closed_at_ms": None, **episode}],
    }
    if card is not None:
        metadata["web_product_ref"] = card
    return metadata


def _vault_session(vault: Path, metadata: dict) -> None:
    session = vault / SID
    (session / "sessions").mkdir(parents=True, exist_ok=True)
    (session / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (session / "sessions" / f"{SID}.jsonl").write_text(
        json.dumps({"role": "assistant", "content": LIST_SENT}) + "\n", encoding="utf-8"
    )


def _turn_context(vault: Path, metadata: dict):
    from src.plugins.chats.agent.sales.decisions.activities import _turn_context

    _vault_session(vault, metadata)
    return _turn_context(SID, [{"text": "Me gusta esta", "wamid": "wamid.ME_GUSTA", "ts_ms": NOW}])


def test_the_engine_knows_the_product_card_the_customer_wrote_from(_isolate_vault_dir: Path) -> None:
    context = _turn_context(_isolate_vault_dir, _metadata(_card()))

    viewed = getattr(context, "viewed_product", None)
    assert viewed is not None, "el motor no sabe desde qué ficha escribe el cliente"
    assert (viewed.title, viewed.handle, viewed.from_catalog) == ("Calabaza", "calabaza", True)


@pytest.mark.parametrize(
    ("card", "episode"),
    [
        pytest.param(_card(status="unresolved", reason="not_in_catalog"), {}, id="no-esta-en-el-catalogo"),
        pytest.param(_card(episode_id="ep_011"), {}, id="de-otro-episodio"),
        pytest.param(_card(), {"order_id": "order_01X"}, id="ya-hay-pedido"),
        pytest.param(None, {}, id="sin-ficha"),
    ],
)
def test_a_card_the_note_would_not_show_is_not_in_the_turn_either(
    _isolate_vault_dir: Path, card: dict | None, episode: dict
) -> None:
    context = _turn_context(_isolate_vault_dir, _metadata(card, **episode))

    assert context is not None and context.viewed_product is None


# --- la política `turno-v4`: el paso de descubrimiento con la ficha ------------

TH = {"detect": 0.70, "covered": 0.70, "answers": 0.70, "purchase_confirm": 0.85,
      "purchase_retract": 0.20, "given": 0.85}
CALABAZA = ViewedProduct(title="Calabaza", handle="calabaza", from_catalog=True)


def _policy(policy_id: str):
    from src.plugins.chats.agent.sales.decisions.policies import get_policy

    try:
        return get_policy(policy_id)
    except KeyError:
        pytest.fail(f"no existe la política {policy_id}")


def _ctx(stage: str = "etapa_descubrimiento", *, viewed: ViewedProduct | None = CALABAZA, **kw: object) -> TurnContext:
    return TurnContext(window=Window(lines=(f"[asesor] {LIST_SENT}",)), facts=(f"Etapa: {stage}",),
                       stage=stage, viewed_product=viewed, **kw)


def _me_gusta_esta() -> PerceptionResult:
    """Lo que contestó Jev en el turno 3 de la conversación de prueba."""
    return PerceptionResult(ok=True, provider="fake", model="typesafe/jev-1.13-20260917", answers=(
        TypedAnswer(id="topic.gusto", kind="noul", p=0.65),
        TypedAnswer(id="topic.disponibilidad", kind="noul", p=0.31),
        TypedAnswer(id="thread.bot_asked", kind="choice", choice="pregunta_abierta",
                    probs=(("pregunta_abierta", 0.96),), confidence=0.95),
        TypedAnswer(id="thread.answers_bot", kind="noul", p=0.27),
        TypedAnswer(id="thread.answer", kind="choice", choice="otra", probs=(("otra", 0.93),), confidence=0.89),
        TypedAnswer(id="msg.1.topic", kind="choice", choice="gusto", probs=(("gusto", 0.96),), confidence=0.96),
    ))


def _decide(policy_id: str, context: TurnContext, result: PerceptionResult | None = None):
    return _policy(policy_id).decide_turn(result or _me_gusta_esta(), questionnaire=RAFAGA, context=context,
                                          n_messages=1, thresholds=TH)


def test_the_customer_who_wrote_from_a_card_is_not_asked_to_choose_a_product() -> None:
    turn = _decide("turno-v4", _ctx())

    assert turn.note is not None and "Calabaza" in turn.note
    assert "ayúdale a escoger un producto" not in turn.note
    assert "Calabaza" in turn.guide["next"] and turn.guide["viewed_product"] == "Calabaza"


def test_a_product_seen_on_the_website_says_where_it_came_from() -> None:
    duo = ViewedProduct(title="Duo Zodiacal", handle="duo-zodiacal", from_catalog=False, variant="Leo")

    turn = _decide("turno-v4", _ctx(viewed=duo))

    assert "«Duo Zodiacal (Leo)»" in turn.guide["next"] and "la web" in turn.guide["next"]
    assert "catálogo de WhatsApp" not in turn.guide["next"]


_STAGES = ("etapa_descubrimiento", "etapa_variantes", "etapa_datos_envio", "etapa_cierre", "etapa_postcierre")


@pytest.mark.parametrize("stage", _STAGES)
@pytest.mark.parametrize("stagnant", [0, 3])
def test_without_a_product_card_the_turn_is_the_one_of_turno_v3(stage: str, stagnant: int) -> None:
    context = _ctx(stage, viewed=None, missing=("ciudad",) if stage == "etapa_datos_envio" else (), stagnant=stagnant)

    assert _decide("turno-v4", context) == _decide("turno-v3", context)


@pytest.mark.parametrize("stage", [s for s in _STAGES if s != "etapa_descubrimiento"])
def test_the_card_only_changes_the_discovery_step(stage: str) -> None:
    assert _decide("turno-v4", _ctx(stage)) == _decide("turno-v3", _ctx(stage))


def test_a_customer_who_only_thanks_is_still_not_pushed_to_buy() -> None:
    """La cortesía (el ingest leyó que solo agradece o saluda) no cambia por la
    ficha: el paso de v3 ya dice que no se abre una venta."""
    context = _ctx(courtesy=True)

    assert _decide("turno-v4", context) == _decide("turno-v3", context)
