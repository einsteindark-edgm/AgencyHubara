"""`record_remarketing_touch_activity`: el peldaño queda en el vault.

Read-modify-write bajo el flock del `FilesystemMetadataStore` (hallazgo L-2 de
la revisión): perder un toque `template` dejaría pasar una tercera plantilla en
el día; pisar `last_inbound_at_ms` rompería el ancla de la escalera.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from temporalio.testing import ActivityEnvironment

from src.platform.whatsapp import activities as act
from src.platform.whatsapp.activities import record_remarketing_touch_activity
from src.platform.whatsapp.reengagement_ladder import NEXT_TOUCH_MORNING

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


@pytest.mark.asyncio
async def test_el_toque_agenda_el_siguiente_para_la_manana_del_cliente(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    # Decisión del operador 2026-10-13: el 2º toque al que se calló tras el
    # catálogo sale a la mañana siguiente, en la hora local del cliente y sin
    # salir de su ventana de 24 h.
    bog = ZoneInfo("America/Bogota")
    escribio = int(datetime(2026, 10, 13, 18, 0, tzinfo=bog).timestamp() * 1000)
    toque = int(datetime(2026, 10, 13, 20, 0, tzinfo=bog).timestamp() * 1000)
    d = _isolate_vault_dir / SID
    d.mkdir(parents=True)
    (d / "metadata.json").write_text(
        json.dumps({
            "last_inbound_at_ms": escribio,
            "service_window_expires_at_ms": escribio + 24 * 3600 * 1000,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(act, "_now_ms", lambda: toque)

    await ActivityEnvironment().run(
        record_remarketing_touch_activity, SID, "free_form", NEXT_TOUCH_MORNING
    )

    data = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
    nueve = int(datetime(2026, 10, 14, 9, 0, tzinfo=bog).timestamp() * 1000)
    assert data["remarketing_touches"][-1]["next_at_ms"] == nueve


@pytest.mark.asyncio
async def test_sin_pedido_de_agenda_el_siguiente_usa_el_hueco_normal(_isolate_vault_dir: Path):
    d = _isolate_vault_dir / SID
    d.mkdir(parents=True)
    (d / "metadata.json").write_text(json.dumps({"last_inbound_at_ms": 123}), encoding="utf-8")
    await ActivityEnvironment().run(record_remarketing_touch_activity, SID, "free_form")
    data = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
    assert "next_at_ms" not in data["remarketing_touches"][-1]
