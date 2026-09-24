"""Sandbox de UN turno (plan §3.4 y §3.6, PR 11): se arma desde el banco,
truncado al inicio del turno, con un número ficticio.

"Teacher forcing" exige que el bot vea exactamente lo que había ANTES del
turno y nada de después:
  * historial del dashboard, trazas e historial del LLM cortados en el turno;
  * metadata como estaba al empezar: borrador, cierre, orden, cupón, tag y
    ruta del inicio (no los del final del día);
  * nada pendiente que dispare efectos (intents de UI, CAPI, dedupe de envíos);
  * el número real no aparece en ningún archivo del sandbox.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.plugins.chats.agent.sales_lab.sandbox.materialize import (
    materialize_case,
    metadata_as_of,
    sim_session_id,
)

SID = "wa_573001234567"
WS = "app-hubara-agency-src-plugins-chats-agent-sales-workspace"
WS_PATH = "/app/hubara_agency/src/plugins/chats/agent/sales/workspace"
T0 = 1_789_500_000_000
AT = T0 + 69_000  # inicio del turno 2


def _iso(ms: int) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _metadata() -> dict:
    return {
        "tag": "CONFIRMADO_PAGO_PENDIENTE",
        "active_route": "humano",
        "escalation_reason": "pago",
        "phone": "573001234567",
        "pending_ui_intents": [{"kind": "products_list"}],
        "recent_freeform_sends": [{"text": "hola", "at_ms": T0}],
        "capi_outbox": [{"event": "Purchase"}],
        "last_inbound_signal": {"kind": "confirm", "at_ms": T0 + 500_000},
        "registered_order": {"order_id": "HUB-1"},
        "checkout_verification": {"verified": True},
        "shipping_flow_awaiting_reply_since_ms": T0 + 300_000,
        "status_history": [
            {"tag": "INTERESADO", "timestamp": (T0 + 10_000) / 1000},
            {"tag": "CONFIRMADO_PAGO_PENDIENTE", "timestamp": (T0 + 500_000) / 1000},
        ],
        "episodes": [
            {
                "episode_id": "ep_001",
                "started_at_ms": T0,
                "order_draft": {"slots": {"producto": "cubo-love", "ciudad": "Bogotá"}, "updated_at_ms": T0 + 400_000},
                "order_id": "HUB-1",
                "applied_coupon": {"code": "AMOR26", "applied_at_ms": T0 + 450_000},
                "llm_history_reset": {"at_ms": T0, "applied": [WS_PATH]},
            }
        ],
    }


def _case(**over) -> dict:
    case = {
        "case_id": f"{SID}/ep_001/t2",
        "session_id": SID,
        "episode_id": "ep_001",
        "turn": 2,
        "turn_key": "run:abc/t:2",
        "at_ms": AT,
        "trigger": "customer",
        "burst": [{"text": "me mandas el catálogo", "ts_ms": T0 + 60_000, "wamid": "wamid.B"}],
        "dashboard_prefix": 2,
        "llm_prefix": 2,
        "stage_in": "descubrimiento",
        "draft": {"producto": "cubo-love", "ciudad": "Bogotá"},
        "state": {"tag": "CONFIRMADO_PAGO_PENDIENTE"},
        "episodes_at": [
            {
                "episode_id": "ep_001",
                "started_at_ms": T0,
                "closed_at_ms": None,
                "order_draft": {"slots": {"producto": "cubo-love", "ciudad": "Bogotá"}, "updated_at_ms": T0 + 400_000},
                "order_id": "HUB-1",
                "applied_coupon": {"code": "AMOR26", "applied_at_ms": T0 + 450_000},
                "llm_history_reset": {"at_ms": T0, "applied": [WS_PATH]},
            }
        ],
        "real": {"inbound_text": "me mandas el catálogo", "sent_texts": ["Claro"]},
        "draft_before": {"producto": "cubo-love"},
        "state_before": {"tag": "INTERESADO", "route": "ventas", "escalation_reason": None, "closing_tag": None, "order_id": None},
        "first_in_episode": False,
    }
    case.update(over)
    return case


def _bench(tmp_path: Path) -> Path:
    b = tmp_path / "bench"
    s = b / "vault" / SID
    (s / "sessions").mkdir(parents=True)
    (s / "evals").mkdir()
    (s / "metadata.json").write_text(json.dumps(_metadata()), encoding="utf-8")
    events = [
        {"role": "user", "content": "hola", "timestamp": _iso(T0 + 1_000)},
        {"role": "assistant", "content": "¡Buenas tardes! Tu número 573001234567 quedó registrado", "timestamp": _iso(T0 + 9_000)},
        {"role": "user", "content": "me mandas el catálogo", "timestamp": _iso(T0 + 60_000)},
        {"role": "assistant", "content": "Claro", "timestamp": _iso(T0 + 75_000)},
    ]
    (s / "sessions" / f"{SID}.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    traces = [
        {"turn": 1, "episode_id": "ep_001", "session_id": SID, "turn_started_ms": T0 + 3_000},
        {"turn": 2, "episode_id": "ep_001", "session_id": SID, "turn_started_ms": AT},
    ]
    (s / "evals" / "turn_traces.jsonl").write_text("\n".join(json.dumps(t) for t in traces) + "\n", encoding="utf-8")
    llm = b / "agent_state" / WS / "sessions"
    llm.mkdir(parents=True)
    lines = [
        {"_type": "metadata", "key": f"whatsapp:{SID}", "last_consolidated": 3, "summary": "resumen de la tarde"},
        {"role": "user", "content": "hola", "timestamp": _iso(T0 + 8_000)},
        {"role": "assistant", "content": "¡Buenas tardes!", "timestamp": _iso(T0 + 8_000)},
        {"role": "user", "content": "me mandas el catálogo", "timestamp": _iso(T0 + 74_000)},
        {"role": "assistant", "content": "Claro", "timestamp": _iso(T0 + 74_000)},
    ]
    (llm / f"{SID}.jsonl").write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    (b / "catalog").mkdir()
    (b / "catalog" / "manifest.json").write_text("{}", encoding="utf-8")
    return b


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_sim_session_id_is_fictitious_stable_and_same_shape() -> None:
    sim = sim_session_id(SID)

    assert sim == sim_session_id(SID)
    assert sim.startswith("wa_570") and len(sim) == len(SID) and sim[3:].isdigit()
    assert sim != sim_session_id("wa_573007654321")


def test_metadata_as_of_keeps_only_what_existed_when_the_turn_started() -> None:
    meta = metadata_as_of(_metadata(), _case(), sales_workspace_path=WS_PATH)

    assert (meta["tag"], meta["active_route"]) == ("INTERESADO", "ventas")
    assert "escalation_reason" not in meta
    [episode] = meta["episodes"]
    assert episode["order_draft"] == {"slots": {"producto": "cubo-love"}}  # se actualizó DESPUÉS: el de la traza anterior
    assert "order_id" not in episode and "applied_coupon" not in episode
    assert [h["tag"] for h in meta["status_history"]] == ["INTERESADO"]
    assert "shipping_flow_awaiting_reply_since_ms" not in meta
    assert meta["phone_number_id"] == "lab-sandbox"  # el envío del sandbox nunca apunta al número del negocio
    for key in ("pending_ui_intents", "recent_freeform_sends", "capi_outbox", "last_inbound_signal",
                "registered_order", "checkout_verification", "pending_handoff_summary"):
        assert key not in meta, key


def test_metadata_as_of_keeps_a_draft_that_did_not_change_after_the_turn_started() -> None:
    meta = _metadata()
    meta["episodes"][0]["order_draft"]["updated_at_ms"] = T0 + 50_000
    case = _case(episodes_at=[dict(meta["episodes"][0], closed_at_ms=None)])

    [episode] = metadata_as_of(meta, case, sales_workspace_path=WS_PATH)["episodes"]

    assert episode["order_draft"]["slots"] == {"producto": "cubo-love", "ciudad": "Bogotá"}


def test_first_turn_of_the_episode_applies_the_llm_history_reset_again() -> None:
    """En producción el reset del historial se aplicó en el primer turno del
    episodio: el sandbox de ese turno lo tiene pendiente, como estaba."""
    first = metadata_as_of(_metadata(), _case(first_in_episode=True), sales_workspace_path=WS_PATH)
    later = metadata_as_of(_metadata(), _case(), sales_workspace_path=WS_PATH)

    assert first["episodes"][0]["llm_history_reset"]["applied"] == []
    assert later["episodes"][0]["llm_history_reset"]["applied"] == [WS_PATH]


def test_handoff_turn_seeds_the_handoff_summary() -> None:
    case = _case(trigger="handoff", real={"inbound_text": "[HANDOFF] el cliente quiere 3 velas"})

    meta = metadata_as_of(_metadata(), case, sales_workspace_path=WS_PATH)

    assert meta["pending_handoff_summary"] == "[HANDOFF] el cliente quiere 3 velas"


def test_materialize_truncates_every_history_at_the_turn(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    box = materialize_case(bench, _case(), tmp_path / "sandbox", bench_workspace=WS, sales_workspace=WS, sales_workspace_path=WS_PATH)
    sim = box.session_id

    events = _jsonl(box.vault_dir / sim / "sessions" / f"{sim}.jsonl")
    assert [e["content"] for e in events][:1] == ["hola"] and len(events) == 2
    assert [t["turn"] for t in _jsonl(box.vault_dir / sim / "evals" / "turn_traces.jsonl")] == [1]
    llm = _jsonl(box.state_dir / WS / "sessions" / f"{sim}.jsonl")
    assert llm[0]["_type"] == "metadata" and len(llm) == 3  # metadatos + 2 mensajes del prefijo
    assert llm[0]["last_consolidated"] <= 2 and "summary" not in llm[0]  # el resumen se escribió después
    assert (box.catalog_dir / "manifest.json").is_file()


def test_the_real_number_never_reaches_the_sandbox(tmp_path: Path) -> None:
    box = materialize_case(_bench(tmp_path), _case(), tmp_path / "sandbox", bench_workspace=WS, sales_workspace=WS, sales_workspace_path=WS_PATH)

    for path in (tmp_path / "sandbox").rglob("*"):
        if path.is_file():
            assert "3001234567" not in path.read_text(encoding="utf-8"), path
    assert not (box.vault_dir / SID).exists()


def test_materialize_does_not_touch_the_bench(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    before = {p: p.read_bytes() for p in bench.rglob("*") if p.is_file()}

    materialize_case(bench, _case(), tmp_path / "sandbox", bench_workspace=WS, sales_workspace=WS, sales_workspace_path=WS_PATH)

    assert {p: p.read_bytes() for p in bench.rglob("*") if p.is_file()} == before


def test_the_llm_history_goes_under_the_local_workspace_slug(tmp_path: Path) -> None:
    """El slug del historial sale del path absoluto del workspace: en la caja
    es el de producción (/app/…); en CI es otro. Se lee con el del banco y se
    escribe con el local."""
    box = materialize_case(
        _bench(tmp_path), _case(), tmp_path / "sandbox",
        bench_workspace=WS, sales_workspace="local-slug", sales_workspace_path="/ci/sales/workspace",
    )

    assert (box.state_dir / "local-slug" / "sessions" / f"{box.session_id}.jsonl").is_file()
    assert not (box.state_dir / WS).exists()


def test_a_later_turn_never_reapplies_the_reset_even_with_another_workspace_path() -> None:
    meta = metadata_as_of(_metadata(), _case(), sales_workspace_path="/ci/sales/workspace")

    assert "/ci/sales/workspace" in meta["episodes"][0]["llm_history_reset"]["applied"]

