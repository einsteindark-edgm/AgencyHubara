"""`record_remarketing_touch_activity`: el peldaño queda en el vault.

Read-modify-write bajo el flock del `FilesystemMetadataStore` (hallazgo L-2 de
la revisión): perder un toque `template` dejaría pasar una tercera plantilla en
el día; pisar `last_inbound_at_ms` rompería el ancla de la escalera.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment

from src.platform.whatsapp.activities import record_remarketing_touch_activity

SID = "wa_573001234567"


@pytest.mark.asyncio
async def test_registra_el_toque_sin_tocar_el_resto(_isolate_vault_dir: Path):
    d = _isolate_vault_dir / SID
    d.mkdir(parents=True)
    (d / "metadata.json").write_text(
        json.dumps({"tag": "INTERESADO", "last_inbound_at_ms": 123}), encoding="utf-8"
    )
    await ActivityEnvironment().run(record_remarketing_touch_activity, SID, "template")

    data = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
    assert [t["kind"] for t in data["remarketing_touches"]] == ["template"]
    assert data["tag"] == "INTERESADO" and data["last_inbound_at_ms"] == 123
    assert (d / "metadata.json.lock").exists(), "la escritura va bajo el flock del store"


@pytest.mark.asyncio
async def test_sin_metadata_no_crea_una_sesion_fantasma(_isolate_vault_dir: Path):
    await ActivityEnvironment().run(record_remarketing_touch_activity, SID, "free_form")
    assert not (_isolate_vault_dir / SID / "metadata.json").exists()
