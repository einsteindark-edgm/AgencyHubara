"""Los scripts de operador escriben `metadata.json` con el store (sexta
revisión del PR #393).

`spread_dia_del_padre_segments.py` y `seed_test_ctwa_sessions.py` lo escribían
con `write_text`: sin candado (pisaban lo que otro escritor puso entre cargar
y aplicar) y a medias (un lector veía un archivo cortado, «dañado»). Ahora van
por `FilesystemMetadataStore.update` (candado + atómico + copia buena), y el
gate de un solo escritor también mira `scripts/`.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.platform.state import FilesystemMetadataStore
from src.plugins.ads.synthetic_seed import plan_segment_spread

SID = "wa_573001234567"


def test_the_segment_spread_keeps_what_another_writer_wrote_after_loading(tmp_path: Path) -> None:
    from scripts.spread_dia_del_padre_segments import apply_spread_to_vault

    vault = tmp_path / "vault"
    store = FilesystemMetadataStore(vault)
    store.write(
        SID,
        {
            "seeded_test": True,
            "origin": {"source_id": "AD_OLD"},
            "last_touch": {"source_id": "AD_OLD"},
            "episodes": [{"episode_id": "ep_001", "order_id": "order_1", "referral_snapshot": {"source_id": "AD_OLD"}}],
        },
    )
    campaign = frozenset({"AD_OLD", "AD_NEW"})
    plan = plan_segment_spread([(SID, store.read(SID))], campaign, ["AD_NEW"])
    # Entre cargar las sesiones y aplicar, la conversación sigue viva.
    store.update(SID, lambda d: {**d, "last_inbound_message_id": "wamid.nuevo"})

    assert apply_spread_to_vault(vault, plan, campaign) == [SID]

    doc = store.read(SID)
    assert doc["last_inbound_message_id"] == "wamid.nuevo", "el reparto pisó lo que otro escribió"
    assert doc["origin"]["source_id"] == "AD_NEW"
    assert doc["last_touch"]["source_id"] == "AD_NEW"
    assert doc["episodes"][0]["referral_snapshot"]["source_id"] == "AD_NEW"


def test_the_segment_spread_skips_a_session_that_is_no_longer_eligible(tmp_path: Path) -> None:
    """Se vuelve a planear sobre lo fresco: si ya no es de la campaña (otro
    escritor la re-atribuyó), no se toca."""
    from scripts.spread_dia_del_padre_segments import apply_spread_to_vault

    vault = tmp_path / "vault"
    store = FilesystemMetadataStore(vault)
    store.write(SID, {"seeded_test": True, "origin": {"source_id": "AD_OLD"}})
    campaign = frozenset({"AD_OLD", "AD_NEW"})
    plan = plan_segment_spread([(SID, store.read(SID))], campaign, ["AD_NEW"])
    store.update(SID, lambda d: {**d, "origin": {"source_id": "AD_OTRA_CAMPANA"}})
    before = (vault / SID / "metadata.json").read_bytes()

    assert apply_spread_to_vault(vault, plan, campaign) == []

    assert (vault / SID / "metadata.json").read_bytes() == before


def test_medusa_is_restamped_only_for_the_sessions_the_vault_wrote() -> None:
    """Octava revisión (M8): una sesión que el vault saltó (ya no era de la
    campaña) no se re-estampa en Medusa: su orden quedaría con otro anuncio."""
    from scripts.spread_dia_del_padre_segments import plan_to_restamp

    plan = [
        {"session_key": "wa_573001234567", "new_source_id": "AD_NEW", "order_ids": ["order_1"]},
        {"session_key": "wa_573009876543", "new_source_id": "AD_NEW", "order_ids": ["order_2"]},
    ]

    assert [p["session_key"] for p in plan_to_restamp(plan, ["wa_573009876543"])] == ["wa_573009876543"]


def test_reseeding_a_session_goes_through_the_store(tmp_path: Path) -> None:
    from scripts.seed_test_ctwa_sessions import write_seed_session

    vault = tmp_path / "vault"
    store = FilesystemMetadataStore(vault)
    old = {"seeded_test": True, "tag": "INTERESADO", "v": 1}
    store.write(SID, old)
    spec = {
        "session_key": SID,
        "metadata": {"seeded_test": True, "tag": "RETOMA_VENTA", "v": 2},
        "history_msgs": 2,
        "last_inbound_ms": None,
    }

    write_seed_session(vault, spec)

    prev = vault / SID / "metadata.json.prev"
    assert prev.exists(), "no pasó por el store (sin candado ni copia buena)"
    assert json.loads(prev.read_text(encoding="utf-8")) == old
    assert store.read(SID) == spec["metadata"]
    history = (vault / SID / "sessions" / f"{SID}.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["content"] for line in history] == ["[seed] mensaje 0", "[seed] mensaje 1"]
