"""Goldens unificados con el scorecard (HU-SC-5): cada corrida del runner real
se convierte en trazas con la MISMA tubería de producción y se evalúa con el
mismo registro; con `--repeat k` se reporta pass^k."""
from __future__ import annotations

import json

from src.plugins.chats.agent.sales_eval.scorecard import golden
from src.plugins.chats.agent.sales_eval.scorecard.registry import SPECS_BY_ID


def _meta(slots: dict, tag: str = "NO_ETIQUETADO") -> dict:
    return {"tag": tag, "active_route": "ventas",
            "episodes": [{"episode_id": "ep_001", "order_draft": {"slots": slots}}]}


def _run() -> dict:
    return {
        "turns": [
            {"role": "user", "content": "hola, quiero una vela"},
            {"role": "assistant", "content": "¡Buenos días! Bienvenido a *Hubara*", "final": "¡Buenos días! Bienvenido a *Hubara*",
             "pre_tool": [], "tools": ["send_quick_replies"],
             "tool_outputs": [{"name": "send_quick_replies", "output": json.dumps({"queued": True})}],
             "metadata": _meta({})},
            {"role": "user", "content": "voy en camino a casa, luego te escribo"},
            {"role": "assistant", "content": "Perfecto", "final": "", "pre_tool": ["Perfecto, te dejo el formulario"],
             "tools": ["request_shipping_details"],
             "tool_outputs": [{"name": "request_shipping_details", "output": json.dumps({"queued": True})}],
             "metadata": _meta({"producto": "cubo-love", "aroma": "Café", "color": "Azul", "cantidad": "1"})},
        ],
        "ledger": [
            {"turn": 0, "name": "send_quick_replies", "args": {"body": "¿Te muestro el catálogo?"}},
            {"turn": 1, "name": "request_shipping_details", "args": {"order_total_cop": 89000}},
        ],
    }


def test_golden_run_becomes_prod_shaped_traces() -> None:
    traces = golden.traces_from_golden_run(_run(), session_id="wa_golden_x")

    assert [t["turn"] for t in traces] == [1, 2]
    assert traces[0]["first_contact"] is True
    assert traces[0]["sent_texts"] == ["¡Buenos días! Bienvenido a *Hubara*"]
    # El texto junto a tools no llega al cliente en prod (default-deny).
    assert traces[1]["sent_texts"] == []
    assert traces[1]["discarded_narration"] == ["Perfecto, te dejo el formulario"]
    assert traces[1]["tools"][0]["name"] == "request_shipping_details"
    assert traces[1]["stage_out"] == "confirmacion"
    # Sin ingest en el runner, la señal se detecta del texto del cliente.
    assert traces[1]["signal"]["kind"] == "deferral"


def test_golden_run_scores_like_production() -> None:
    record = golden.score_golden_run(_run(), session_id="wa_golden_x")

    failing = {r["check_id"] for r in record["results"] if r["verdict"] == "falla"}
    assert {"CON-01", "CON-02"} <= failing
    assert record["verdict"] == "FALLA"


def test_pass_hat_k_requires_every_run_to_hold() -> None:
    runs = [
        {"verdict": "PASA", "checks": {"CON-01": "pasa", "DES-01": "pasa", "VAR-01": "no_aplica"}},
        {"verdict": "FALLA", "checks": {"CON-01": "falla", "DES-01": "pasa", "VAR-01": "no_aplica"}},
        {"verdict": "PASA", "checks": {"CON-01": "pasa", "DES-01": "pasa", "VAR-01": "no_aplica"}},
    ]

    out = golden.pass_hat_k(runs)

    assert out["scenario"] == {"k": 3, "pass_at_1": round(2 / 3, 4), "pass_hat_k": False}
    assert out["checks"]["CON-01"] == {"k": 3, "decided": 3, "passes": 2, "pass_at_1": round(2 / 3, 4), "pass_hat_k": False}
    assert out["checks"]["DES-01"]["pass_hat_k"] is True
    assert out["checks"]["VAR-01"]["pass_hat_k"] is None


def test_every_legacy_behavior_type_maps_to_a_check_or_is_declared_uncovered() -> None:
    vocab = {"greeting_first_turn", "forbidden_opener_absent", "offer_catalog_opening", "search_before_naming",
             "no_hallucinated_product", "escalate", "no_escalation", "register_order_attempted", "tag_set",
             "tag_not_set", "single_closing_message", "no_forbidden_closing", "no_voseo", "no_em_dash",
             "emoji_allowlist", "no_reask", "no_ai_reveal", "proactive_offering_back_to_catalog",
             "tool_called", "tool_not_called"}

    mapped = {b for b in vocab if golden.check_ids_for_behavior(b)}

    assert vocab - mapped == golden.BEHAVIORS_WITHOUT_CHECK
    for b in mapped:
        assert all(cid in SPECS_BY_ID for cid in golden.check_ids_for_behavior(b))


def test_markdown_report_lists_verdicts_and_pass_hat_k() -> None:
    summary = [{"id": "cierre_canonico", "category": "cierre",
                "scorecards": [{"verdict": "PASA", "checks": {"CIE-01": "pasa"}},
                               {"verdict": "FALLA", "checks": {"CIE-01": "falla"}}]}]

    md = golden.format_scorecard_md(summary)

    assert "cierre_canonico" in md
    assert "PASA · FALLA" in md
    assert "CIE-01" in md
