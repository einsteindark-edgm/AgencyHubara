"""Calibración del juez contra etiquetas humanas (HU-SC-4).

Positivo = `falla`. Se reporta TPR y TNR por separado (los fallos son raros:
la precisión cruda engaña) y kappa de Cohen. Un check de juez solo puede
reprobar un episodio si está calibrado (n ≥ MIN_LABELS y kappa ≥ umbral).
"""
from __future__ import annotations

from src.plugins.chats.agent.sales_eval.scorecard import calibration as cal


def _record(session: str, verdicts: dict[str, str]) -> dict:
    return {
        "session_id": session, "episode_id": "ep_001", "ts": "2026-09-14T10:00:00+00:00",
        "results": [{"check_id": k, "verdict": v, "source": "judge", "evidence": "e", "critique": "c"}
                    for k, v in verdicts.items()],
    }


def _label(session: str, check: str, verdict: str) -> dict:
    return {"session_id": session, "episode_id": "ep_001", "check_id": check, "verdict": verdict}


def test_confusion_counts_rates_and_kappa() -> None:
    records = [_record(f"wa_{i}", {"DES-04": j}) for i, j in enumerate(["falla", "falla", "pasa", "pasa", "falla", "pasa"])]
    labels = [_label(f"wa_{i}", "DES-04", h) for i, h in enumerate(["falla", "pasa", "pasa", "pasa", "falla", "falla"])]

    rows = {r["check_id"]: r for r in cal.compute_calibration(records, labels)}

    r = rows["DES-04"]
    assert (r["tp"], r["fp"], r["tn"], r["fn"], r["n"]) == (2, 1, 2, 1, 6)
    assert r["tpr"] == round(2 / 3, 4) and r["tnr"] == round(2 / 3, 4)
    # po = 4/6; pe = (3*3 + 3*3)/36 = 0.5 → kappa = (0.6667-0.5)/0.5
    assert r["kappa"] == round((4 / 6 - 0.5) / 0.5, 4)
    assert r["status"] == "sin_datos"  # n < REVIEW_MIN


def test_status_thresholds_and_calibrated_set(monkeypatch) -> None:
    monkeypatch.setattr(cal, "MIN_LABELS", 4)
    monkeypatch.setattr(cal, "REVIEW_MIN", 2)
    good = [_record(f"wa_{i}", {"EST-04": v}) for i, v in enumerate(["falla", "pasa", "pasa", "falla"])]
    good_labels = [_label(f"wa_{i}", "EST-04", v) for i, v in enumerate(["falla", "pasa", "pasa", "falla"])]
    bad = [_record(f"wb_{i}", {"TAG-04": v}) for i, v in enumerate(["falla", "falla", "pasa"])]
    bad_labels = [_label(f"wb_{i}", "TAG-04", v) for i, v in enumerate(["pasa", "pasa", "falla"])]

    rows = {r["check_id"]: r for r in cal.compute_calibration(good + bad, good_labels + bad_labels)}

    assert rows["EST-04"]["status"] == "confiable"
    assert rows["TAG-04"]["status"] == "revisar"
    assert cal.calibrated_checks(rows.values()) == {"EST-04"}


def test_unknown_or_na_verdicts_do_not_enter_the_matrix_and_all_judge_checks_listed() -> None:
    records = [_record("wa_1", {"DES-04": "desconocido"}), _record("wa_2", {"DES-04": "no_aplica"})]
    labels = [_label("wa_1", "DES-04", "falla"), _label("wa_2", "DES-04", "pasa")]

    rows = {r["check_id"]: r for r in cal.compute_calibration(records, labels)}

    assert rows["DES-04"]["n"] == 0 and rows["DES-04"]["kappa"] is None
    assert "EST-08" in rows  # todos los checks de juez aparecen, aunque sin datos


def test_label_queue_prioritizes_unknown_then_failures_and_skips_labeled() -> None:
    records = [
        _record("wa_1", {"DES-04": "pasa", "EST-04": "falla"}),
        _record("wa_2", {"DES-04": "desconocido"}),
        _record("wa_3", {"EST-07": "falla"}),
    ]
    labels = [_label("wa_3", "EST-07", "falla")]

    items = cal.label_queue(records, labels, limit=10, sample_every=1)

    assert [(i["session_id"], i["check_id"], i["reason"]) for i in items] == [
        ("wa_2", "DES-04", "desconocido"),
        ("wa_1", "EST-04", "falla"),
        ("wa_1", "DES-04", "muestra"),
    ]
    assert items[0]["check_name"]


def test_labels_carry_the_judge_verdict_they_were_made_against() -> None:
    """La etiqueta guarda el veredicto del juez que el humano vio: la
    calibración no necesita releer meses de scorecards en cada llamada."""
    pairs = [("falla", "falla"), ("falla", "pasa"), ("pasa", "pasa")]
    labels = [{**_label(f"wa_{i}", "DES-04", h), "judge_verdict": j} for i, (j, h) in enumerate(pairs)]

    rows = {r["check_id"]: r for r in cal.compute_calibration([], labels)}

    r = rows["DES-04"]
    assert (r["tp"], r["fp"], r["tn"], r["fn"], r["n"]) == (1, 1, 1, 0, 3)
