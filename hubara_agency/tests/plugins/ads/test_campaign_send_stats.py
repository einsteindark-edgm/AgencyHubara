"""Una campaña de WhatsApp (plugin marketing) en Ads, desde que se envía.

Pedido del operador (2026-09-25): lanzar una campaña real tiene que crear su
fila en Ads, con las estadísticas del envío como una campaña de Meta —
enviados, entregados, leídos, fallidos, respuestas, bajas y gasto real — y el
embudo de sus conversaciones. Antes la fila solo nacía cuando alguien
respondía, y de lo enviado no se veía nada.

La fuente es el touch de cada destinatario (`campaign_touches`): lo estampa el
envío con el id del mensaje y el webhook de estados anota ahí entregado /
leído / fallido y el precio (`delivery`). Los touches de prueba no cuentan.
"""
import json
from pathlib import Path

from src.plugins.ads.aggregation import list_ads_campaigns

_DAY_MS = 24 * 60 * 60 * 1000
_T0 = 1_790_000_000_000  # envío de la campaña
_PRICING = {"billable": True, "pricing_type": "regular", "category": "marketing"}


def _seed(vault: Path, session_id: str, metadata: dict) -> None:
    d = vault / session_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")


def _touch(wamid: str | None, delivery: dict | None = None, **extra) -> dict:
    touch = {"campaign_id": "mkt-amor", "campaign_name": "Amor y amistad", "sent_at_ms": _T0, **extra}
    if wamid:
        touch["wa_message_id"] = wamid
    if delivery is not None:
        touch["delivery"] = delivery
    return touch


def _delivered(**extra) -> dict:
    return {"status": "delivered", "delivered_at_ms": _T0 + 2_000, "pricing": _PRICING,
            "cost_usd_micros": 12500, "rate_card_version": "co_v1", **extra}


def _reply_episode(episode_id: str = "ep_002") -> dict:
    return {"episode_id": episode_id, "started_at_ms": _T0 + 3_600_000, "closed_at_ms": None,
            "referral_snapshot": {"channel": "direct"}}


def _row(vault: Path, **kw):
    [row] = [c for c in list_ads_campaigns(vault, **kw) if c.id == "mkt-amor"]
    return row


def test_a_sent_campaign_is_in_ads_before_anyone_replies(tmp_path: Path) -> None:
    _seed(tmp_path, "wa_573000000001", {"campaign_touches": [_touch("wamid.1", _delivered())]})

    row = _row(tmp_path)

    assert row.source_type == "hubara_campaign"
    assert row.name == "Amor y amistad"
    assert row.started == 0  # nadie respondió todavía
    assert row.whatsapp_send is not None
    assert row.whatsapp_send.sent == 1 and row.whatsapp_send.delivered == 1
    assert row.first_seen_ms == _T0 and row.last_seen_ms == _T0


def test_the_send_funnel_and_the_real_spend_of_a_campaign(tmp_path: Path) -> None:
    read = _delivered(status="read", read_at_ms=_T0 + 60_000)
    _seed(tmp_path, "wa_573000000001", {"campaign_touches": [_touch("wamid.1", read)],
                                         "episodes": [_reply_episode()]})
    _seed(tmp_path, "wa_573000000002", {"campaign_touches": [_touch("wamid.2", _delivered())]})
    _seed(tmp_path, "wa_573000000003", {"campaign_touches": [_touch(
        "wamid.3", {"status": "failed", "failed_at_ms": _T0 + 1_000, "error_code": 131049})]})
    # Enviado y todavía sin noticias de Meta: su precio está pendiente.
    _seed(tmp_path, "wa_573000000004", {"campaign_touches": [_touch("wamid.4")]})
    # Enviado antes de que el touch guardara el id del mensaje.
    _seed(tmp_path, "wa_573000000005", {"campaign_touches": [_touch(None)]})
    # Se dio de baja por esta campaña ("NO MÁS").
    _seed(tmp_path, "wa_573001111111", {
        "campaign_touches": [_touch("wamid.6", _delivered())],
        "marketing_opt_out": True, "marketing_opt_out_at_ms": _T0 + 7_200_000,
        "marketing_opt_out_source": "texto", "marketing_opt_out_campaign_id": "mkt-amor",
    })

    stats = _row(tmp_path).whatsapp_send

    assert (stats.sent, stats.delivered, stats.read, stats.failed) == (6, 3, 1, 1)
    assert (stats.replied, stats.opted_out) == (1, 1)
    assert stats.cost_usd_micros == 3 * 12500
    assert stats.cost_pending == 1  # wamid.4
    assert stats.untracked == 1  # el touch sin id


def test_test_sends_do_not_count(tmp_path: Path) -> None:
    _seed(tmp_path, "wa_573000000001", {"campaign_touches": [_touch("wamid.T", _delivered(), test=True)],
                                         "episodes": [_reply_episode()]})

    assert [c for c in list_ads_campaigns(tmp_path) if c.id == "mkt-amor"] == []


def test_the_window_counts_what_was_sent_in_it(tmp_path: Path) -> None:
    _seed(tmp_path, "wa_573000000001", {"campaign_touches": [_touch("wamid.1", _delivered())]})

    assert _row(tmp_path, since_ms=_T0 - _DAY_MS, until_ms=_T0 + _DAY_MS).whatsapp_send.sent == 1
    assert [c.id for c in list_ads_campaigns(tmp_path, since_ms=_T0 + _DAY_MS)] == []


def test_two_campaigns_keep_their_numbers_apart(tmp_path: Path) -> None:
    """Varias campañas (pregunta del operador, 2026-09-25): envío, entrega y
    costo van a SU campaña por el id de campaña del touch; una respuesta va a
    la última campaña que recibió el cliente en los 7 días previos
    (last-touch, como Meta). Entre dos campañas al mismo cliente hay al menos
    48 h (respiro de marketing)."""
    first, second = _T0, _T0 + 3 * _DAY_MS

    def touch(campaign_id: str, name: str, at: int, wamid: str, delivery: dict) -> dict:
        return {"campaign_id": campaign_id, "campaign_name": name, "sent_at_ms": at,
                "wa_message_id": wamid, "delivery": delivery}

    read = {**_delivered(), "status": "read", "read_at_ms": second + 60_000}
    # Recibió las dos y respondió después de la segunda.
    _seed(tmp_path, "wa_573000000001", {
        "campaign_touches": [touch("mkt-amor", "Amor y amistad", first, "wamid.A1", _delivered()),
                             touch("mkt-halloween", "Halloween", second, "wamid.H1", read)],
        "episodes": [{"episode_id": "ep_003", "started_at_ms": second + 3_600_000,
                      "closed_at_ms": None, "referral_snapshot": {"channel": "direct"}}],
    })
    # Solo recibió la primera, y respondió.
    _seed(tmp_path, "wa_573000000002", {
        "campaign_touches": [touch("mkt-amor", "Amor y amistad", first, "wamid.A2", _delivered())],
        "episodes": [_reply_episode()],
    })
    # Solo la segunda, y falló.
    _seed(tmp_path, "wa_573000000003", {"campaign_touches": [touch(
        "mkt-halloween", "Halloween", second, "wamid.H2",
        {"status": "failed", "failed_at_ms": second + 1_000, "error_code": 131049})]})

    rows = {c.id: c for c in list_ads_campaigns(tmp_path)}
    amor, halloween = rows["mkt-amor"], rows["mkt-halloween"]

    assert (amor.name, halloween.name) == ("Amor y amistad", "Halloween")
    a, h = amor.whatsapp_send, halloween.whatsapp_send
    assert (a.sent, a.delivered, a.read, a.failed, a.replied, amor.started) == (2, 2, 0, 0, 1, 1)
    assert (h.sent, h.delivered, h.read, h.failed, h.replied, halloween.started) == (2, 1, 1, 1, 1, 1)
    assert (a.cost_usd_micros, h.cost_usd_micros) == (2 * 12500, 12500)


def test_a_reply_quoting_an_older_campaign_is_counted_for_that_campaign(tmp_path: Path) -> None:
    """El cliente respondió citando el mensaje de Amor aunque después le llegó
    Halloween: el webhook marcó el episodio con Amor y Ads lo respeta
    (2026-09-25)."""
    from src.plugins.ads.aggregation import list_attributed_conversations

    first, second = _T0, _T0 + 3 * _DAY_MS
    amor = {"campaign_id": "mkt-amor", "campaign_name": "Amor y amistad", "sent_at_ms": first,
            "wa_message_id": "wamid.A1", "delivery": _delivered()}
    halloween = {"campaign_id": "mkt-halloween", "campaign_name": "Halloween", "sent_at_ms": second,
                 "wa_message_id": "wamid.H1", "delivery": _delivered()}
    _seed(tmp_path, "wa_573000000001", {"campaign_touches": [amor, halloween], "episodes": [{
        "episode_id": "ep_004", "started_at_ms": second + 3_600_000, "closed_at_ms": None,
        "referral_snapshot": {"channel": "direct"},
        "opened_by_campaign": {"campaign_id": "mkt-amor", "campaign_name": "Amor y amistad",
                               "sent_at_ms": first, "wa_message_id": "wamid.A1"},
    }]})

    rows = {c.id: c for c in list_ads_campaigns(tmp_path)}

    assert (rows["mkt-amor"].started, rows["mkt-amor"].whatsapp_send.replied) == (1, 1)
    assert (rows["mkt-halloween"].started, rows["mkt-halloween"].whatsapp_send.replied) == (0, 0)
    assert [c.episode_id for c in list_attributed_conversations(tmp_path, "mkt-amor")] == ["ep_004"]


def test_a_conversation_opened_by_a_test_send_is_not_attributed(tmp_path: Path) -> None:
    """El operador probó Halloween en su número, que días antes había recibido
    Amor de verdad: su respuesta era a la prueba — no cuenta para Amor."""
    first, second = _T0, _T0 + 3 * _DAY_MS
    amor = {"campaign_id": "mkt-amor", "campaign_name": "Amor y amistad", "sent_at_ms": first,
            "wa_message_id": "wamid.A1", "delivery": _delivered()}
    prueba = {"campaign_id": "mkt-halloween", "campaign_name": "Halloween", "sent_at_ms": second,
              "wa_message_id": "wamid.T1", "test": True}
    _seed(tmp_path, "wa_573000000001", {"campaign_touches": [amor, prueba], "episodes": [{
        "episode_id": "ep_004", "started_at_ms": second + 3_600_000, "closed_at_ms": None,
        "referral_snapshot": {"channel": "direct"},
        "opened_by_campaign": {"campaign_id": "mkt-halloween", "sent_at_ms": second,
                               "wa_message_id": "wamid.T1", "test": True},
    }]})

    rows = {c.id: c for c in list_ads_campaigns(tmp_path)}

    assert (rows["mkt-amor"].started, rows["mkt-amor"].whatsapp_send.replied) == (0, 0)
    assert "mkt-halloween" not in rows
    assert rows["direct"].started == 1


def test_a_meta_campaign_has_no_whatsapp_send_stats(tmp_path: Path) -> None:
    _seed(tmp_path, "wa_573000000001", {"episodes": [{
        "episode_id": "ep_001", "started_at_ms": _T0, "closed_at_ms": None,
        "referral_snapshot": {"channel": "ad", "source_id": "AD_1", "headline": "Anuncio"}}]})

    [row] = list_ads_campaigns(tmp_path)

    assert row.id == "AD_1" and row.whatsapp_send is None


def test_the_template_cost_is_not_charged_again_to_the_open_conversation(tmp_path: Path) -> None:
    """El contacto venía de un anuncio con la conversación abierta: la
    plantilla de la campaña quedó en el log de esa conversación. Su costo es
    de la campaña — la fila del anuncio no lo vuelve a sumar."""
    open_from_ad = {
        "episode_id": "ep_001",
        "started_at_ms": _T0 - _DAY_MS,
        "closed_at_ms": None,
        "referral_snapshot": {"channel": "ad", "source_id": "AD_1", "headline": "Anuncio"},
        "outbound_messages": [
            {"sent_at_ms": _T0 - _DAY_MS + 1_000, "wa_message_id": "wamid.BOT", "kind": "text",
             "pricing": {"billable": False, "pricing_type": "free_entry_point",
                         "category": "referral_conversion"}, "cost_usd_micros": 0},
            {"sent_at_ms": _T0, "wa_message_id": "wamid.1", "kind": "template",
             "template_name": "campaign_promo_marketing_v1", "pricing": _PRICING,
             "cost_usd_micros": 12500},
        ],
        "cost_summary": {
            "total_usd_micros": 12500, "messages_count": 2, "messages_billable_count": 1,
            "messages_free_count": 1, "messages_pending_count": 0,
            "by_category": {"referral_conversion": {"count": 1, "usd_micros": 0},
                            "marketing": {"count": 1, "usd_micros": 12500}},
            "by_pricing_type": {},
        },
    }
    _seed(tmp_path, "wa_573000000001", {"campaign_touches": [_touch("wamid.1", _delivered())],
                                         "episodes": [open_from_ad]})

    rows = {c.id: c for c in list_ads_campaigns(tmp_path)}

    assert rows["mkt-amor"].whatsapp_send.cost_usd_micros == 12500
    assert rows["AD_1"].wa_cost_usd_micros == 0
    assert "marketing" not in (rows["AD_1"].wa_cost_by_category or {})


# --- Endpoint ---------------------------------------------------------------------

import pytest  # noqa: E402


@pytest.fixture
def ads_endpoint(tmp_path: Path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.plugins.ads.api as ads_mod

    ads_mod._scan_cache.clear()
    monkeypatch.setattr(ads_mod, "WORKSPACE_VAULT_DIR", tmp_path)
    app = FastAPI()
    app.include_router(ads_mod.router, prefix="/api/ads")
    yield TestClient(app), tmp_path
    ads_mod._scan_cache.clear()


def test_the_campaigns_endpoint_sends_the_whatsapp_send_stats(ads_endpoint, monkeypatch) -> None:
    import src.plugins.ads.api as ads_mod

    client, vault = ads_endpoint
    _seed(vault, "wa_573000000001", {"campaign_touches": [_touch("wamid.1", _delivered())]})
    asked: list[list[str]] = []
    monkeypatch.setattr(ads_mod, "_cached_meta_names", lambda ids: asked.append(list(ids)) or {})

    rows = client.get("/api/ads/campaigns").json()["campaigns"]

    [row] = [r for r in rows if r["id"] == "mkt-amor"]
    assert row["source_type"] == "hubara_campaign"
    assert row["whatsapp_send"]["sent"] == 1
    assert row["whatsapp_send"]["cost_usd_micros"] == 12500
    # Meta no conoce los ids de las campañas de WhatsApp: no se le preguntan.
    assert all("mkt-amor" not in ids for ids in asked)
