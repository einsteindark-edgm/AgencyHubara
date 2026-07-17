"""CampaignStore — historial de campañas persistente en el vault.

El vault es el único estado que sobrevive deploys (PR #183); las campañas
viven en `<vault>/_campaigns/<id>.json` con escritura atómica.
"""
from src.plugins.marketing.campaign_store import CampaignStore
from src.plugins.marketing.domain.campaigns import new_campaign


def _store(tmp_path):
    return CampaignStore(tmp_path)


def test_save_y_get_roundtrip(tmp_path) -> None:
    store = _store(tmp_path)
    campaign = new_campaign(
        campaign_id="mkt-20260717-ab12",
        name="Día de la madre · velas",
        now_ms=1_000,
    )
    store.save(campaign)
    loaded = store.get("mkt-20260717-ab12")
    assert loaded == campaign
    assert loaded["status"] == "draft"
    assert loaded["created_at_ms"] == 1_000


def test_get_inexistente_devuelve_none(tmp_path) -> None:
    assert _store(tmp_path).get("mkt-nope") is None


def test_list_ordena_por_updated_at_desc(tmp_path) -> None:
    store = _store(tmp_path)
    a = new_campaign(campaign_id="mkt-a", name="A", now_ms=1_000)
    b = new_campaign(campaign_id="mkt-b", name="B", now_ms=2_000)
    store.save(a)
    store.save(b)
    ids = [c["id"] for c in store.list_campaigns()]
    assert ids == ["mkt-b", "mkt-a"]


def test_delete_borra_el_archivo(tmp_path) -> None:
    store = _store(tmp_path)
    store.save(new_campaign(campaign_id="mkt-x", name="X", now_ms=1))
    assert store.delete("mkt-x") is True
    assert store.get("mkt-x") is None
    assert store.delete("mkt-x") is False
