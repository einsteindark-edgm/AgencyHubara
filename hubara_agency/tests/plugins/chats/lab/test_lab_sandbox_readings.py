"""El sandbox corre las lecturas del ingest (motor de decisiones F2).

Antes el sandbox arrancaba el workflow directo, sin el paso de lecturas del
ingest: el aplazamiento nunca se registraba, el check CON-02 no se juzgaba en
los brazos simulados y la confirmación de compra llegaba con estado
inconsistente. Ahora cada mensaje de la ráfaga pasa por el MISMO proveedor
de lecturas y la MISMA escritura que en producción, con el bot del brazo.
"""
from __future__ import annotations

from pathlib import Path

from tests.plugins.chats.lab.test_lab_sandbox_leaks import _run_probe
from tests.plugins.chats.lab.test_lab_sandbox_materialize import T0, _case


def test_a_deferral_in_the_burst_reaches_the_turn_like_in_production(tmp_path: Path) -> None:
    case = _case(burst=[{"text": "les escribo la otra semana", "ts_ms": T0 + 60_000, "wamid": "wamid.D"}],
                 real={"inbound_text": "les escribo la otra semana", "sent_texts": ["Listo"]})

    report = _run_probe(tmp_path, case)

    result = report["result"]
    assert result["error"] is None, result
    assert any("EL CLIENTE APLAZÓ" in note for note in result["plugin_context"]), result["plugin_context"]
    assert [v["capability"] for v in result["readings"][0]] == ["compra", "retoma", "baja"]


async def test_the_readings_read_what_the_customer_wrote_not_what_the_ingest_added(tmp_path: Path, monkeypatch) -> None:
    """En producción las lecturas del ingest leen el texto crudo del cliente
    (`effective.text`), no el turno con la campaña citada. En el sandbox el
    mensaje trae los dos (`text` y `raw_text`): las lecturas leen el crudo."""
    from src.plugins.chats.agent.sales_lab.sandbox.readings import apply_burst_readings

    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    metadata: dict = {"episodes": [{"episode_id": "ep_1", "started_at_ms": T0, "closed_at_ms": None}]}
    wrapped = {"text": "[campaña citada: «les escribo la otra semana con novedades»]\nhola", "raw_text": "hola",
               "ts_ms": T0 + 60_000, "wamid": "wamid.R"}

    await apply_burst_readings(metadata, [wrapped], session_id="wa_573001234567", vault_dir=tmp_path, at_ms=T0 + 60_000)
    wrapped_only = {k: v for k, v in wrapped.items() if k != "raw_text"}
    control: dict = {"episodes": [{"episode_id": "ep_1", "started_at_ms": T0, "closed_at_ms": None}]}
    await apply_burst_readings(control, [wrapped_only], session_id="wa_573001234567", vault_dir=tmp_path, at_ms=T0 + 60_000)

    assert control != metadata, "el texto envuelto sí registra un aplazamiento (control del test)"
    assert not any("deferral" in key for key in metadata), metadata
