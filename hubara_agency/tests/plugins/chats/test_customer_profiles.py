"""El perfil «interesado sin compra» (pedido del operador, 2026-10-20).

«Colocar un tag de perfilamiento a esos clientes, que está interesado pero
que todavía no; luego hacemos una pasada y etiquetamos a los que no
alcanzaron a estar etiquetados antes del desarrollo, y que ese perfilamiento
sirva como filtro de público para las campañas de WhatsApp».

La etiqueta de la conversación (`tag`) cambia sola (INTERESADO → SIN_RESPUESTA)
y no sirve de público. El perfil queda en la persona (`profile_tags`), con el
episodio en que mostró la intención, de dónde salió y la campaña. «Sin
compra» no se borra a mano: el público lo calcula (ningún pedido en la
conversación desde ese episodio).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.plugins.chats.shared import customer_profiles as cp


def _metadata(*episodes: dict) -> dict:
    return {"episodes": list(episodes)}


EP1 = {"episode_id": "ep_001", "started_at_ms": 1_000, "sales_campaign": "velitas-7-dic"}


# ── marcar el perfil ─────────────────────────────────────────────────────────


def test_the_stamp_keeps_the_episode_the_source_and_the_campaign() -> None:
    metadata = _metadata(dict(EP1))

    assert cp.stamp_interest(metadata, now_ms=5_000, source="jev") is True

    assert metadata[cp.PROFILE_KEY][cp.INTERESADO_SIN_COMPRA] == {
        "first_at_ms": 5_000, "at_ms": 5_000, "episode_id": "ep_001", "source": "jev", "campaign": "velitas-7-dic",
    }


def test_a_new_stamp_keeps_when_it_was_first_seen() -> None:
    metadata = _metadata(dict(EP1))
    cp.stamp_interest(metadata, now_ms=5_000, source="regla")

    cp.stamp_interest(metadata, now_ms=9_000, source="jev")

    mark = metadata[cp.PROFILE_KEY][cp.INTERESADO_SIN_COMPRA]
    assert mark["first_at_ms"] == 5_000 and mark["at_ms"] == 9_000 and mark["source"] == "jev"


def test_an_episode_with_its_order_is_not_stamped() -> None:
    metadata = _metadata({**EP1, "order_id": "order_1"})

    assert cp.stamp_interest(metadata, now_ms=5_000, source="jev") is False
    assert cp.PROFILE_KEY not in metadata


# ── la intención en la lectura de este mensaje ───────────────────────────────


def _readings(kind=None, *, choice: str | None = None, confidence: float = 0.9, by: str = "jev"):
    verdicts = ()
    if choice is not None:
        verdicts = ({"capability": "compra", "by": by, "provider": by, "value": [kind, "text"],
                     "answers": [{"q": "compra.que_hace", "choice": choice, "confidence": confidence}]},)
    return SimpleNamespace(purchase=(kind, "text"), verdicts=verdicts)


@pytest.mark.parametrize(
    ("readings", "source"),
    [
        (_readings(choice="elige"), "jev"),
        (_readings("deferral", choice="aplaza"), "jev"),
        (_readings("deferral"), "regla"),  # sin Jev: la regla de hoy leyó el aplazamiento
        (_readings("affirmation"), "regla"),  # el carrito o el botón «Confirmar»
        (_readings(choice="pregunta"), None),  # preguntar el precio es interés, no intención
        (_readings(choice="elige", confidence=0.5), None),
    ],
)
def test_the_intent_in_the_reading_of_this_message(readings, source) -> None:
    assert cp.intent_in_reading(readings) == source


# ── la pasada por las conversaciones de antes ────────────────────────────────


def _row(choice: str, at_ms: int) -> dict:
    return {"at_ms": at_ms, "stage": "ingest", "capability": "compra", "by": "jev", "provider": "jev",
            "value": [None, "text"], "answers": [{"q": "compra.que_hace", "choice": choice, "confidence": 0.9}]}


def test_history_finds_the_last_episode_where_jev_read_intent() -> None:
    metadata = _metadata({"episode_id": "ep_001", "started_at_ms": 1_000}, {"episode_id": "ep_002", "started_at_ms": 50_000})

    found = cp.history_interest(metadata, decisions=[_row("elige", 2_000), _row("aplaza", 60_000)], traces=[])

    assert found is not None and found[0]["episode_id"] == "ep_002" and found[1] == "jev"


def test_history_without_jev_reads_the_half_made_order_and_the_funnel_stage() -> None:
    with_draft = _metadata({"episode_id": "ep_001", "started_at_ms": 1_000,
                            "order_draft": {"items": [{"producto": "Caja de Velas de deseos cuadradas"}]}})
    by_stage = _metadata({"episode_id": "ep_001", "started_at_ms": 1_000})
    traces = [{"episode_id": "ep_001", "stage_out": "variantes"}]

    assert cp.history_interest(with_draft, decisions=[], traces=[])[1] == "pedido_a_medias"
    assert cp.history_interest(by_stage, decisions=[], traces=traces)[1] == "etapa"


def test_history_skips_who_bought_after_showing_interest() -> None:
    metadata = _metadata({"episode_id": "ep_001", "started_at_ms": 1_000},
                         {"episode_id": "ep_002", "started_at_ms": 50_000, "order_id": "order_1"})

    assert cp.history_interest(metadata, decisions=[_row("elige", 2_000)], traces=[]) is None


def test_history_of_who_only_asked_the_price_is_empty() -> None:
    metadata = _metadata({"episode_id": "ep_001", "started_at_ms": 1_000})

    assert cp.history_interest(metadata, decisions=[_row("pregunta", 2_000)], traces=[]) is None



# ── revisión del 2026-10-20: quien acaba de comprar no es «sin compra» ──────
#
# El episodio cierra con su pedido; el siguiente mensaje del cliente («ya te
# transferí») abre un episodio de posventa (`after_order`) sin `order_id` y Jev
# lee `da_datos`/`confirma`: sin esto quedaba marcado y la campaña del 5 % le
# llegaba a un comprador reciente.

DAY = 86_400_000
BOUGHT = {"episode_id": "ep_001", "started_at_ms": 1_000, "closed_at_ms": 10 * DAY,
          "closing_tag": "CONFIRMADO_PAGO_PENDIENTE", "order_id": "order_1"}


def test_a_post_sale_episode_is_not_stamped() -> None:
    marked = _metadata(dict(BOUGHT), {"episode_id": "ep_002", "started_at_ms": 10 * DAY + 60_000,
                                      "after_order": {"order_id": "order_1"}})
    # Medusa no respondió y no hubo `after_order`: el pedido de hace 2 días lo dice.
    unmarked = _metadata(dict(BOUGHT), {"episode_id": "ep_002", "started_at_ms": 12 * DAY})

    for metadata in (marked, unmarked):
        assert cp.stamp_interest(metadata, now_ms=12 * DAY, source="jev") is False
        assert cp.PROFILE_KEY not in metadata


def test_interest_long_after_an_old_purchase_is_stamped() -> None:
    metadata = _metadata(dict(BOUGHT), {"episode_id": "ep_002", "started_at_ms": 60 * DAY})

    assert cp.stamp_interest(metadata, now_ms=60 * DAY, source="jev") is True


def test_history_skips_the_post_sale_episode() -> None:
    metadata = _metadata(dict(BOUGHT), {"episode_id": "ep_002", "started_at_ms": 12 * DAY,
                                        "after_order": {"order_id": "order_1"}})

    assert cp.history_interest(metadata, decisions=[_row("da_datos", 12 * DAY + 1)], traces=[]) is None


# ── revisión del 2026-10-20: la intención sale de la decisión, no de la respuesta cruda ─


def test_a_raw_confirma_the_engine_retracted_is_not_intent() -> None:
    """«Sí, muéstrame el catálogo»: Jev puede responder `confirma` 0,9, pero
    el paquete lo retira (el asesor no preguntó por la compra). La afirmación
    llega por la decisión final (`purchase`), no por la respuesta cruda."""
    assert cp.intent_in_reading(_readings(None, choice="confirma")) is None


def test_the_engine_verdict_of_a_real_reading_has_the_shape_the_profile_reads() -> None:
    from src.plugins.chats.agent.sales.decisions.capabilities import Verdict

    verdict = Verdict(capability="compra", value=[None, "text"], by="jev", provider="jev", rule=[None, "text"],
                      answers=({"q": "compra.que_hace", "choice": "elige", "confidence": 0.9},))
    readings = SimpleNamespace(purchase=(None, "text"), verdicts=(verdict.to_trace(),))

    assert cp.intent_in_reading(readings) == "jev"


# ── revisión del 2026-10-20: la pasada dice cuándo mostró la intención ──────


def test_history_says_when_the_customer_showed_the_intent() -> None:
    episode = {"episode_id": "ep_001", "started_at_ms": 1_000}
    stage = [{"episode_id": "ep_001", "stage_out": "variantes", "recorded_at_ms": 4_000},
             {"episode_id": "ep_001", "stage_out": "descubrimiento", "recorded_at_ms": 6_000}]
    draft = {**episode, "order_draft": {"items": [{"producto": "Caja de Velas de deseos cuadradas"}]}}

    by_jev = cp.history_interest(_metadata(dict(episode)), decisions=[_row("elige", 2_000), _row("aplaza", 3_000)], traces=[])
    by_stage = cp.history_interest(_metadata(dict(episode)), decisions=[], traces=stage)
    by_draft = cp.history_interest(_metadata(draft), decisions=[], traces=[])

    assert (by_jev.source, by_jev.at_ms) == ("jev", 3_000)
    assert (by_stage.source, by_stage.at_ms) == ("etapa", 4_000)
    assert (by_draft.source, by_draft.at_ms) == ("pedido_a_medias", 1_000)  # sin hora: el inicio del episodio
