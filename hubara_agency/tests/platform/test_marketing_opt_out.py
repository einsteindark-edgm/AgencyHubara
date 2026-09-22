"""Detector de opt-out de marketing — la promesa del template hecha real.

El template de campañas promete "respóndeme NO MÁS y te doy de baja". Este
detector es quien la cumple: inbound con pedido de baja + campaña reciente
(touch en ventana) ⇒ `marketing_opt_out=true`. Determinista, sin LLM.
"""
import pytest

from src.platform.whatsapp.marketing_opt_out import detect_marketing_opt_out

_NOW = 1_750_000_000_000
_HOUR = 60 * 60 * 1000
_TOUCHED = {
    "campaign_touches": [
        {"campaign_id": "mkt-1", "campaign_name": "Promo", "sent_at_ms": _NOW - _HOUR}
    ]
}


@pytest.mark.parametrize(
    "text",
    [
        "NO MÁS",
        "no mas",
        "No más promociones por favor",
        "no quiero recibir más mensajes",
        "dame de baja",
        "denme de baja por favor",
        "baja",
        "BAJA.",
        "no me envíes más promos",
        "no me escribas más",
    ],
)
def test_detecta_pedidos_de_baja_con_campana_reciente(text) -> None:
    assert detect_marketing_opt_out(text, _TOUCHED, _NOW) is True


@pytest.mark.parametrize(
    "text",
    [
        "quiero 2 velas del catálogo",
        "me gusta la baja del precio, la quiero comprar",  # "baja" en frase larga
        "hola",
        "",
        None,
        "cuánto vale el envío?",
    ],
)
def test_no_detecta_texto_normal_de_venta(text) -> None:
    assert detect_marketing_opt_out(text, _TOUCHED, _NOW) is False


def test_sin_campana_reciente_no_marca_opt_out() -> None:
    """Sin touch en ventana, "no más" puede ser parte de una conversación
    normal con el bot — el detector solo honra la promesa del template."""
    old_touch = {
        "campaign_touches": [
            {"campaign_id": "mkt-1", "sent_at_ms": _NOW - 10 * 24 * _HOUR}
        ]
    }
    assert detect_marketing_opt_out("NO MÁS", {}, _NOW) is False
    assert detect_marketing_opt_out("NO MÁS", old_touch, _NOW) is False


def test_messagingkit_reexporta_el_detector() -> None:
    import src.platform.whatsapp.marketing_opt_out as impl
    import src.sdk.messagingkit as kit

    assert kit.detect_marketing_opt_out is impl.detect_marketing_opt_out


# --- registro de la baja: fecha, origen y campaña que la provocó ----------


def test_opt_out_campaign_id_es_la_campana_del_touch_reciente() -> None:
    from src.platform.whatsapp.marketing_opt_out import opt_out_campaign_id

    assert opt_out_campaign_id(_TOUCHED, _NOW) == "mkt-1"
    # Sin touch en ventana (o solo escalera de remarketing): no hay campaña.
    assert opt_out_campaign_id({}, _NOW) is None
    ladder = {"remarketing_touches": [{"kind": "template", "at_ms": _NOW - _HOUR}]}
    assert opt_out_campaign_id(ladder, _NOW) is None


def test_mark_marketing_opt_out_guarda_fecha_origen_y_campana() -> None:
    from src.platform.whatsapp.marketing_opt_out import (
        OPT_OUT_SOURCE_META,
        OPT_OUT_SOURCE_TEXT,
        mark_marketing_opt_out,
        marketing_opt_out_info,
    )

    metadata: dict = {"tag": "INTERESADO"}
    mark_marketing_opt_out(
        metadata, now_ms=_NOW, source=OPT_OUT_SOURCE_TEXT, campaign_id="mkt-1"
    )
    assert metadata["marketing_opt_out"] is True
    assert metadata["marketing_opt_out_at_ms"] == _NOW
    assert metadata["marketing_opt_out_source"] == "texto"
    assert metadata["marketing_opt_out_campaign_id"] == "mkt-1"
    info = marketing_opt_out_info(metadata)
    assert (info.at_ms, info.source, info.campaign_id) == (_NOW, "texto", "mkt-1")

    # Sticky: una segunda baja (Meta, otra campaña) NO pisa la primera —
    # la métrica de bajas por campaña cuenta la que la provocó.
    mark_marketing_opt_out(
        metadata, now_ms=_NOW + _HOUR, source=OPT_OUT_SOURCE_META, campaign_id="mkt-2"
    )
    assert metadata["marketing_opt_out_campaign_id"] == "mkt-1"
    assert metadata["marketing_opt_out_at_ms"] == _NOW


def test_marketing_opt_out_info_tolera_metadata_viejo_y_sin_baja() -> None:
    from src.platform.whatsapp.marketing_opt_out import marketing_opt_out_info

    assert marketing_opt_out_info({"tag": "INTERESADO"}) is None
    # Bajas de antes de este cambio: solo el flag (y a veces la fecha).
    info = marketing_opt_out_info({"marketing_opt_out": True})
    assert info is not None
    assert (info.at_ms, info.source, info.campaign_id) == (None, None, None)


def test_is_meta_opt_out_failure_reconoce_el_131050() -> None:
    from src.platform.whatsapp.marketing_opt_out import is_meta_opt_out_failure

    assert is_meta_opt_out_failure("TemplateMetaError131050") is True
    assert is_meta_opt_out_failure("TemplateMetaError131049") is False
    assert is_meta_opt_out_failure(None) is False


def test_131050_es_non_retryable_en_el_send() -> None:
    # Meta: "el destinatario eligió no recibir mensajes de marketing de tu
    # negocio" — reintentar es gastar y ensuciar la calidad del número.
    from src.platform.whatsapp.activities import NON_RETRYABLE_META_ERROR_CODES

    assert "131050" in NON_RETRYABLE_META_ERROR_CODES


def test_messagingkit_reexporta_el_registro_de_baja() -> None:
    from src.sdk import messagingkit

    for name in (
        "mark_marketing_opt_out",
        "opt_out_campaign_id",
        "marketing_opt_out_info",
        "is_meta_opt_out_failure",
        "OPT_OUT_SOURCE_TEXT",
        "OPT_OUT_SOURCE_META",
    ):
        assert hasattr(messagingkit, name), name
