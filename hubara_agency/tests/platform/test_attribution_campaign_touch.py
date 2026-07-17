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
