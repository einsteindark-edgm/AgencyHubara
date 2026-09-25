"""`matching_campaign_touch` es del read model de atribución (platform) y se
consume vía SDK — lo comparten ads (agrupación) y marketing (stats)."""


def test_connectorkit_reexporta_matching_campaign_touch():
    import src.platform.attribution as impl
    import src.sdk.connectorkit as kit

    assert kit.matching_campaign_touch is impl.matching_campaign_touch


def test_matching_campaign_touch_gana_el_mas_reciente_en_ventana():
    from src.platform.attribution import matching_campaign_touch

    day = 24 * 60 * 60 * 1000
    touches = [
        {"campaign_id": "mkt-a", "sent_at_ms": 1_000},
        {"campaign_id": "mkt-b", "sent_at_ms": 2_000},
        {"campaign_id": "mkt-c", "sent_at_ms": 100_000 + 8 * day},  # futura
        "basura",
        {"campaign_id": None, "sent_at_ms": 3_000},
    ]
    best = matching_campaign_touch(touches, 5_000)
    assert best is not None and best["campaign_id"] == "mkt-b"
    assert matching_campaign_touch(touches, 2_000 + 8 * day) is None
    assert matching_campaign_touch(None, 5_000) is None
    assert matching_campaign_touch(touches, None) is None


def test_touch_de_envio_de_prueba_no_atribuye():
    """El envío de prueba del operador deja touch (el bot sabe qué recibió),
    pero no cuenta para respuestas/ventas de la campaña ni para Ads."""
    from src.platform.attribution import matching_campaign_touch

    touches = [
        {"campaign_id": "mkt-a", "sent_at_ms": 1_000},
        {"campaign_id": "mkt-a", "sent_at_ms": 2_000, "test": True},
    ]
    assert matching_campaign_touch(touches, 5_000)["sent_at_ms"] == 1_000
    assert matching_campaign_touch(touches[1:], 5_000) is None


# --- Lo que Meta dice de cada mensaje de campaña (2026-09-25) ------------------
#
# Pedido del operador: una campaña de marketing tiene que registrar en Ads
# entregados, leídos y gasto real, como una campaña de Meta. El touch del
# contacto guarda el id del mensaje (`wa_message_id`) y el webhook de estados
# anota ahí lo que va pasando: el touch es el registro por destinatario.


def _touch(**extra):
    return {"campaign_id": "mkt-a", "campaign_name": "Amor", "sent_at_ms": 1_000,
            "wa_message_id": "wamid.CAMP1", **extra}


def test_encuentra_el_touch_de_un_mensaje_de_campana():
    from src.platform.attribution import campaign_touch_for_message

    touches = ["basura", {"campaign_id": "mkt-b", "sent_at_ms": 1}, _touch()]
    assert campaign_touch_for_message(touches, "wamid.CAMP1") is touches[2]
    assert campaign_touch_for_message(touches, "wamid.OTRO") is None
    assert campaign_touch_for_message(None, "wamid.CAMP1") is None
    assert campaign_touch_for_message(touches, "") is None


def test_el_estado_avanza_y_no_retrocede_aunque_meta_lo_mande_desordenado():
    from src.platform.attribution import apply_campaign_delivery, campaign_delivery

    touch = _touch()
    # Meta puede mandar "read" antes que "delivered": leído implica entregado.
    assert apply_campaign_delivery(touch, status="read", at_ms=5_000)
    assert apply_campaign_delivery(touch, status="delivered", at_ms=4_000)
    state = campaign_delivery(touch)
    assert state.delivered and state.read and not state.failed
    assert touch["delivery"]["status"] == "read"
    assert touch["delivery"]["delivered_at_ms"] == 4_000
    assert touch["delivery"]["read_at_ms"] == 5_000
    # El mismo webhook otra vez no cambia nada.
    assert not apply_campaign_delivery(touch, status="read", at_ms=5_000)


def test_un_mensaje_que_fallo_guarda_el_codigo_de_meta():
    from src.platform.attribution import apply_campaign_delivery, campaign_delivery

    touch = _touch()
    assert apply_campaign_delivery(touch, status="failed", at_ms=2_000, error_code=131049)

    state = campaign_delivery(touch)
    assert state.failed and not state.delivered
    assert touch["delivery"]["error_code"] == 131049


def test_el_precio_se_anota_una_sola_vez():
    from src.platform.attribution import apply_campaign_delivery, campaign_delivery

    pricing = {"billable": True, "pricing_type": "regular", "category": "marketing"}
    touch = _touch()
    apply_campaign_delivery(touch, status="sent", at_ms=1_500, pricing=pricing,
                            cost_usd_micros=12_500, rate_card_version="co_v1")
    apply_campaign_delivery(touch, status="delivered", at_ms=2_000, pricing=pricing,
                            cost_usd_micros=99_999, rate_card_version="otra")

    state = campaign_delivery(touch)
    assert state.cost_usd_micros == 12_500 and state.priced
    assert touch["delivery"]["pricing"]["category"] == "marketing"
    assert touch["delivery"]["rate_card_version"] == "co_v1"


def test_touch_sin_datos_de_entrega():
    from src.platform.attribution import campaign_delivery

    state = campaign_delivery({"campaign_id": "mkt-a", "sent_at_ms": 1})
    assert not (state.delivered or state.read or state.failed or state.priced)
    assert state.cost_usd_micros is None and not state.has_message_id
    assert campaign_delivery(_touch()).has_message_id


def test_connectorkit_reexporta_el_registro_de_entrega():
    import src.platform.attribution as impl
    import src.sdk.connectorkit as kit

    assert kit.apply_campaign_delivery is impl.apply_campaign_delivery
    assert kit.campaign_delivery is impl.campaign_delivery
    assert kit.campaign_touch_for_message is impl.campaign_touch_for_message


# --- A qué campaña se atribuye una conversación (2026-09-25) ---------------------
#
# Pedido del operador: si el cliente responde CITANDO el mensaje de una
# campaña, la conversación es de esa campaña aunque después le haya llegado
# otra. El webhook de chats toma esa decisión al abrir el episodio
# (`opened_by_campaign`) y todos los que atribuyen (Ads, Marketing) la leen de
# ahí: una sola regla, la misma que vio el bot.

_DAY = 24 * 60 * 60 * 1000
_AMOR = {"campaign_id": "mkt-amor", "campaign_name": "Amor", "sent_at_ms": 1_000,
         "wa_message_id": "wamid.AMOR"}
_HALLOWEEN = {"campaign_id": "mkt-halloween", "campaign_name": "Halloween",
              "sent_at_ms": 1_000 + 3 * _DAY, "wa_message_id": "wamid.HALLOWEEN"}


def test_la_conversacion_abierta_citando_una_campana_es_de_esa_campana():
    from src.platform.attribution import attributed_campaign_touch

    episode = {"started_at_ms": 1_000 + 4 * _DAY,
               "opened_by_campaign": {"campaign_id": "mkt-amor", "sent_at_ms": 1_000,
                                      "wa_message_id": "wamid.AMOR"}}

    touch = attributed_campaign_touch(episode, [_AMOR, _HALLOWEEN], episode["started_at_ms"])

    assert touch is not None and touch["campaign_id"] == "mkt-amor"


def test_una_conversacion_abierta_por_un_envio_de_prueba_no_se_atribuye():
    from src.platform.attribution import attributed_campaign_touch

    prueba = {**_HALLOWEEN, "test": True}
    # Episodios viejos: la marca no dice que era prueba, el touch sí.
    legacy = {"started_at_ms": 1_000 + 4 * _DAY,
              "opened_by_campaign": {"campaign_id": "mkt-halloween", "sent_at_ms": prueba["sent_at_ms"]}}
    marked = {"started_at_ms": 1_000 + 4 * _DAY,
              "opened_by_campaign": {"campaign_id": "mkt-halloween", "sent_at_ms": prueba["sent_at_ms"],
                                     "test": True}}

    # Aunque haya otro envío REAL en ventana: esa conversación respondía a la prueba.
    assert attributed_campaign_touch(legacy, [_AMOR, prueba], legacy["started_at_ms"]) is None
    assert attributed_campaign_touch(marked, [_AMOR, prueba], marked["started_at_ms"]) is None


def test_sin_marca_se_atribuye_al_ultimo_envio_en_ventana():
    from src.platform.attribution import attributed_campaign_touch

    episode = {"started_at_ms": 1_000 + 4 * _DAY}

    touch = attributed_campaign_touch(episode, [_AMOR, _HALLOWEEN], episode["started_at_ms"])

    assert touch is not None and touch["campaign_id"] == "mkt-halloween"


def test_la_marca_sirve_aunque_el_touch_ya_no_este_en_la_lista():
    """El contacto guarda sus últimas 20 campañas: la marca del episodio basta."""
    from src.platform.attribution import attributed_campaign_touch

    episode = {"started_at_ms": 5_000,
               "opened_by_campaign": {"campaign_id": "mkt-vieja", "campaign_name": "Vieja",
                                      "sent_at_ms": 1_000}}

    touch = attributed_campaign_touch(episode, [_HALLOWEEN], 5_000)

    assert touch is not None
    assert (touch["campaign_id"], touch["campaign_name"], touch["sent_at_ms"]) == ("mkt-vieja", "Vieja", 1_000)


def test_connectorkit_reexporta_la_regla_de_atribucion():
    import src.platform.attribution as impl
    import src.sdk.connectorkit as kit

    assert kit.attributed_campaign_touch is impl.attributed_campaign_touch
