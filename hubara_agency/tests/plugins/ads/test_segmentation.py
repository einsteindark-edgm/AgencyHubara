"""Tests de la agrupación jerárquica campaña → adset → ad buckets.

Segmentación (2026-07-10): el vault agrupa episodios por `source_id` (= AD id
de Meta). `segmentation.py` recompone la jerarquía real de Ads Manager:
buckets (ads) → segmentos (ad sets) → campañas, usando el resolver
`fetch_meta_ad_names` (ad → {campaign, adset}). Todo puro — el IO (Graph,
cache) vive en la capa API.
"""
from __future__ import annotations

from src.plugins.ads.aggregation import DIRECT_CAMPAIGN_ID, AdsCampaignSummary
from src.plugins.ads.segmentation import (
    UNSEGMENTED_ADSET_ID,
    group_buckets_by_adset,
    group_buckets_by_campaign,
    scope_source_ids,
)


def _bucket(**overrides) -> AdsCampaignSummary:
    base = dict(
        id="AD_1",
        name="Chatea con nosotros",
        source_type="ad",
        started=2,
        first_seen_ms=1_000,
        last_seen_ms=2_000,
        conversations={
            "no_reply": 1, "nuevo": 1, "activo": 0, "calificado": 0,
            "cotizado": 0, "ganado": 0, "perdido": 0,
        },
    )
    base.update(overrides)
    return AdsCampaignSummary(**base)


def _names(ad_id: str, *, campaign="CAMP_9", campaign_name="Día del Padre",
           adset="ADSET_A", adset_name="Hombres 25-45") -> dict:
    return {
        "ad_name": f"Ad {ad_id}",
        "campaign_name": campaign_name,
        "campaign_id": campaign,
        "adset_id": adset,
        "adset_name": adset_name,
        "thumbnail_url": None,
    }


class TestGroupByCampaign:
    def test_merges_two_ads_of_same_campaign_into_one_row(self):
        """Dos ads de la misma campaña → UNA fila con agregados exactos
        (suma de counts, min/max de fechas, avg_ticket recompuesto)."""
        b1 = _bucket(
            id="AD_1", started=2, first_seen_ms=1_000, last_seen_ms=2_000,
            revenue=50_000, avg_ticket=50_000, revenue_count=1,
            avg_episode_duration_ms=100, duration_count=1,
            llm_cost_usd=0.002, llm_tokens=1000,
            capi_leads_sent=1,
        )
        b2 = _bucket(
            id="AD_2", started=3, first_seen_ms=500, last_seen_ms=3_000,
            conversations={
                "no_reply": 0, "nuevo": 1, "activo": 1, "calificado": 1,
                "cotizado": 0, "ganado": 0, "perdido": 0,
            },
            revenue=30_000, avg_ticket=15_000, revenue_count=2,
            avg_episode_duration_ms=300, duration_count=3,
            llm_cost_usd=0.001, llm_tokens=500,
            capi_purchases_sent=2,
        )
        names = {"AD_1": _names("AD_1"), "AD_2": _names("AD_2", adset="ADSET_B")}
        rows = group_buckets_by_campaign([b1, b2], names)
        assert len(rows) == 1
        row = rows[0]
        assert row.id == "CAMP_9"
        assert row.name == "Día del Padre"
        assert row.meta_campaign_id == "CAMP_9"
        assert row.started == 5
        assert row.first_seen_ms == 500
        assert row.last_seen_ms == 3_000
        assert row.conversations["nuevo"] == 2
        assert row.conversations["no_reply"] == 1
        assert row.revenue == 80_000
        assert row.revenue_count == 3
        assert row.avg_ticket == round(80_000 / 3)
        # duración: promedio ponderado exacto (100·1 + 300·3) / 4
        assert row.avg_episode_duration_ms == 250
        assert row.duration_count == 4
        assert row.llm_cost_usd == 0.003
        assert row.llm_tokens == 1500
        assert row.capi_leads_sent == 1
        assert row.capi_purchases_sent == 2
        # multi-ad: el creative/adset por-ad no aplica a la fila campaña
        assert row.ad_set is None
        assert row.meta_adset_id is None

    def test_single_ad_campaign_keeps_creative_detail(self):
        """Campaña con UN ad → la fila conserva el detalle por-ad (mismo
        comportamiento que el enrich actual: 'Campaña · Ad', creative,
        adset) pero con id = campaign_id (estable para el drill-down)."""
        b = _bucket(id="AD_1")
        rows = group_buckets_by_campaign([b], {"AD_1": _names("AD_1")})
        assert len(rows) == 1
        row = rows[0]
        assert row.id == "CAMP_9"
        assert row.name == "Día del Padre · Ad AD_1"
        assert row.creative_title == "Chatea con nosotros"
        assert row.ad_set == "Hombres 25-45"
        assert row.meta_adset_id == "ADSET_A"

    def test_unresolved_and_direct_buckets_pass_through(self):
        """Sin entry en names (Graph caído / ad borrado) o bucket `direct`
        → la fila pasa intacta (degradación = comportamiento actual)."""
        direct = _bucket(id=DIRECT_CAMPAIGN_ID, source_type="direct",
                         name="Clientes directos · sin campaña")
        unresolved = _bucket(id="AD_GONE")
        rows = group_buckets_by_campaign([direct, unresolved], {})
        assert {r.id for r in rows} == {DIRECT_CAMPAIGN_ID, "AD_GONE"}
        by_id = {r.id: r for r in rows}
        assert by_id["AD_GONE"] is unresolved
        assert by_id[DIRECT_CAMPAIGN_ID] is direct

    def test_rows_sorted_by_last_seen_desc(self):
        b1 = _bucket(id="AD_1", last_seen_ms=1_000)
        b2 = _bucket(id="AD_2", last_seen_ms=9_000)
        names = {
            "AD_1": _names("AD_1"),
            "AD_2": _names("AD_2", campaign="CAMP_OTHER",
                           campaign_name="Otra"),
        }
        rows = group_buckets_by_campaign([b1, b2], names)
        assert [r.id for r in rows] == ["CAMP_OTHER", "CAMP_9"]

    def test_two_campaigns_stay_separate(self):
        b1 = _bucket(id="AD_1")
        b2 = _bucket(id="AD_2")
        names = {
            "AD_1": _names("AD_1"),
            "AD_2": _names("AD_2", campaign="CAMP_OTHER", campaign_name="Otra"),
        }
        rows = group_buckets_by_campaign([b1, b2], names)
        assert {r.id for r in rows} == {"CAMP_9", "CAMP_OTHER"}

    def test_revenue_none_when_no_bucket_had_sales(self):
        """None honesto se preserva en el merge: todos None → None (no 0)."""
        b1 = _bucket(id="AD_1")
        b2 = _bucket(id="AD_2")
        names = {"AD_1": _names("AD_1"), "AD_2": _names("AD_2")}
        row = group_buckets_by_campaign([b1, b2], names)[0]
        assert row.revenue is None
        assert row.avg_ticket is None
        assert row.llm_cost_usd is None
        assert row.avg_episode_duration_ms is None


class TestGroupByAdset:
    def test_groups_campaign_buckets_by_adset(self):
        """Drill-down de UNA campaña: sus buckets (ads) agrupados por
        segmento — fila con id/name del ad set y merge exacto."""
        b1 = _bucket(id="AD_1", started=2)
        b2 = _bucket(id="AD_2", started=3)
        b3 = _bucket(id="AD_3", started=1)
        names = {
            "AD_1": _names("AD_1", adset="ADSET_A", adset_name="Hombres 25-45"),
            "AD_2": _names("AD_2", adset="ADSET_A", adset_name="Hombres 25-45"),
            "AD_3": _names("AD_3", adset="ADSET_B", adset_name="Mujeres 30-50"),
        }
        rows = group_buckets_by_adset([b1, b2, b3], names)
        by_id = {r.id: r for r in rows}
        assert set(by_id) == {"ADSET_A", "ADSET_B"}
        assert by_id["ADSET_A"].name == "Hombres 25-45"
        assert by_id["ADSET_A"].started == 5
        assert by_id["ADSET_A"].ad_set == "Hombres 25-45"
        assert by_id["ADSET_A"].meta_adset_id == "ADSET_A"
        assert by_id["ADSET_A"].meta_campaign_id == "CAMP_9"
        assert by_id["ADSET_B"].started == 1

    def test_bucket_without_adset_falls_to_unsegmented(self):
        """Ad resuelto sin adset (nodo raro de Graph) → bucket 'sin
        segmento' visible, nunca desaparece del drill-down."""
        b = _bucket(id="AD_1")
        names = {"AD_1": _names("AD_1", adset=None, adset_name=None)}
        rows = group_buckets_by_adset([b], names)
        assert rows[0].id == UNSEGMENTED_ADSET_ID
        assert rows[0].started == 2


class TestMergeMetaAdsets:
    def test_fills_metrics_on_matching_adset_row(self):
        from src.plugins.ads.meta.parse import MetaAdsetMetrics
        from src.plugins.ads.segmentation import merge_meta_adsets

        row = _bucket(id="ADSET_A", name="Hombres 25-45")
        metrics = [
            MetaAdsetMetrics(
                adset_id="ADSET_A", adset_name="Hombres 25-45",
                campaign_id="CAMP_9", spend=320500.0, impressions=15000,
                reach=12100, clicks=210, messaging_conversations_started=44,
            )
        ]
        out = merge_meta_adsets([row], metrics)
        assert len(out) == 1
        assert out[0].spend == 320500.0
        assert out[0].impressions == 15000
        assert out[0].messaging_conversations_started == 44
        # los agregados del vault no se tocan
        assert out[0].started == 2

    def test_adset_with_spend_but_no_chats_enters_standalone(self):
        """Segmento activo sin conversaciones atribuidas → fila standalone
        (started=0), para que el operador VEA dónde gasta sin resultados."""
        from src.plugins.ads.meta.parse import MetaAdsetMetrics
        from src.plugins.ads.segmentation import merge_meta_adsets

        metrics = [
            MetaAdsetMetrics(
                adset_id="ADSET_NEW", adset_name="Lookalike compradores",
                campaign_id="CAMP_9", spend=50000.0, impressions=3000,
                reach=2500, clicks=40, messaging_conversations_started=0,
            )
        ]
        out = merge_meta_adsets([], metrics)
        assert len(out) == 1
        assert out[0].id == "ADSET_NEW"
        assert out[0].name == "Lookalike compradores"
        assert out[0].started == 0
        assert out[0].conversations is None
        assert out[0].spend == 50000.0

    def test_adset_without_spend_and_no_chats_is_noise(self):
        from src.plugins.ads.meta.parse import MetaAdsetMetrics
        from src.plugins.ads.segmentation import merge_meta_adsets

        metrics = [
            MetaAdsetMetrics(
                adset_id="ADSET_IDLE", adset_name="Dormido",
                campaign_id="CAMP_9", spend=0.0, impressions=0,
                reach=0, clicks=0, messaging_conversations_started=0,
            )
        ]
        assert merge_meta_adsets([], metrics) == []


class TestCollectSourceIds:
    def test_collects_from_origin_and_episode_snapshots(self):
        """Los ad ids del vault salen del origin sticky Y de los
        referral_snapshot por episodio (re-atribución FU2) — ambos alimentan
        el resolver de nombres/scope sin re-agregar campañas."""
        from pathlib import Path

        from src.plugins.ads.segmentation import collect_source_ids

        sessions = [
            (
                Path("/v/wa_1"),
                {
                    "origin": {"channel": "ad", "source_id": "AD_1"},
                    "episodes": [
                        {"episode_id": "e1", "referral_snapshot": None},
                        {
                            "episode_id": "e2",
                            "referral_snapshot": {
                                "channel": "ad", "source_id": "AD_2",
                            },
                        },
                    ],
                },
            ),
            (Path("/v/wa_2"), {"origin": {"channel": "direct"}}),
            (Path("/v/wa_3"), {}),
        ]
        assert collect_source_ids(sessions) == frozenset({"AD_1", "AD_2"})


class TestScopeSourceIds:
    def test_campaign_scope_collects_its_ads(self):
        names = {
            "AD_1": _names("AD_1"),
            "AD_2": _names("AD_2", adset="ADSET_B"),
            "AD_3": _names("AD_3", campaign="CAMP_OTHER", campaign_name="Otra"),
        }
        assert scope_source_ids(names, campaign_id="CAMP_9") == frozenset(
            {"AD_1", "AD_2"}
        )

    def test_adset_scope_narrows_to_segment(self):
        names = {
            "AD_1": _names("AD_1", adset="ADSET_A"),
            "AD_2": _names("AD_2", adset="ADSET_B"),
        }
        assert scope_source_ids(
            names, campaign_id="CAMP_9", adset_id="ADSET_B"
        ) == frozenset({"AD_2"})

    def test_unknown_campaign_returns_empty(self):
        """Id que no matchea ninguna campaña resuelta → frozenset vacío;
        la capa API interpreta vacío como 'usar el id crudo' (back-compat
        con ids legacy = source_id y con el bucket direct)."""
        assert scope_source_ids({}, campaign_id="whatever") == frozenset()
