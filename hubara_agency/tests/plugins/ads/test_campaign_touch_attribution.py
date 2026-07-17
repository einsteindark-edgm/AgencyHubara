"""Atribución de campañas internas de marketing (canal `hubara_campaign`).

El plugin marketing estampa `campaign_touches` en el metadata al enviar una
campaña. Un episodio que ARRANCA dentro de la ventana post-touch (7 días) y
NO vino de un referral Meta se atribuye a la campaña — así el panel de Ads
agrupa las respuestas a campañas junto a las campañas Meta, sin imports
cross-plugin (la data viaja por el vault).
"""
import json
from pathlib import Path

from src.plugins.ads.aggregation import (
    list_ads_campaigns,
    list_attributed_conversations,
)

_DAY_MS = 24 * 60 * 60 * 1000
_T0 = 1_750_000_000_000  # touch de la campaña


def _seed(vault: Path, session_id: str, metadata: dict) -> None:
    d = vault / session_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")


def _touch(campaign_id="mkt-abc", name="Promo madre", at=_T0):
    return {"campaign_id": campaign_id, "campaign_name": name, "sent_at_ms": at}


def test_episodio_post_touch_se_agrupa_por_campana(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        "wa_+571",
        {
            "origin": {"channel": "direct", "first_seen_ms": _T0 - 30 * _DAY_MS},
            "campaign_touches": [_touch()],
            "episodes": [
                {
                    "episode_id": "ep_001",
                    "started_at_ms": _T0 + 2 * 60 * 60 * 1000,  # 2h post-touch
                    "closed_at_ms": None,
                    "referral_snapshot": {"channel": "direct"},
                }
            ],
        },
    )
    campaigns = list_ads_campaigns(tmp_path)
    assert len(campaigns) == 1
    campaign = campaigns[0]
    assert campaign.id == "mkt-abc"
    assert campaign.source_type == "hubara_campaign"
    assert campaign.name == "Promo madre"
    assert campaign.started == 1

    convs = list_attributed_conversations(tmp_path, "mkt-abc")
    assert [c.episode_id for c in convs] == ["ep_001"]
    assert convs[0].ad_headline == "Promo madre"


def test_referral_meta_del_episodio_gana_sobre_el_touch(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        "wa_+572",
        {
            "origin": {"channel": "direct", "first_seen_ms": _T0},
            "campaign_touches": [_touch()],
            "episodes": [
                {
                    "episode_id": "ep_001",
                    "started_at_ms": _T0 + _DAY_MS,
                    "closed_at_ms": None,
                    "referral_snapshot": {
                        "channel": "ad",
                        "source_id": "AD_B",
                        "headline": "Anuncio B",
                    },
                }
            ],
        },
    )
    campaigns = {c.id for c in list_ads_campaigns(tmp_path)}
    assert campaigns == {"AD_B"}


def test_episodio_fuera_de_ventana_no_se_atribuye(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        "wa_+573",
        {
            "origin": {"channel": "direct", "first_seen_ms": _T0 - 30 * _DAY_MS},
            "campaign_touches": [_touch()],
            "episodes": [
                {
                    "episode_id": "ep_001",
                    "started_at_ms": _T0 + 8 * _DAY_MS,  # ventana de 7d vencida
                    "closed_at_ms": None,
                    "referral_snapshot": {"channel": "direct"},
                },
                {
                    "episode_id": "ep_000",
                    "started_at_ms": _T0 - _DAY_MS,  # ANTES del touch
                    "closed_at_ms": _T0 - _DAY_MS + 1000,
                    "referral_snapshot": {"channel": "direct"},
                },
            ],
        },
    )
    ids = {c.id for c in list_ads_campaigns(tmp_path)}
    assert ids == {"direct"}
