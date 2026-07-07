"""Per-tool TCK + golden de `parse-conversations` (espejo de la matriz Free-First).

El golden se alimenta de `fixtures/reengagement_matrix_golden.json` — COPIA
byte-a-byte de la matriz de hubara (`hubara_agency/tests/platform/fixtures/`),
cuyo test corre los mismos casos contra `decide_reengagement` REAL (la
autoridad). El checksum de abajo detecta drift entre las dos copias: si la
política cambia en hubara, este test se pone rojo hasta re-sincronizar.

Mapeo matriz → espejo (el espejo NO conoce el rate card — no afirma costos):
  expect.allowed=true  → action=dispatch + channel/category de la matriz
  expect.allowed=false → action=suppress + reason=expect.suppress_reason
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from sdk.testkit.tool_checks import run_tool_checks, tool_level
from sdk.tool_model import load_tool
from tools.parse_conversations.impl import run

GA = Path(__file__).resolve().parents[2]
TOOL = GA / "tools" / "parse_conversations" / "tool.yaml"
MATRIX = GA / "fixtures" / "reengagement_matrix_golden.json"

#: MISMO valor que MATRIX_SHA256 en
#: hubara_agency/tests/platform/test_reengagement_matrix_golden.py.
MATRIX_SHA256 = "3a56faa53fb38e7d495221bb3015d863289fc51af2d0b4c1b94eb71894452183"

_M = json.loads(MATRIX.read_text(encoding="utf-8"))

#: reason esperado del espejo cuando la matriz dice dispatch (allowed=true).
#: Derivable de la geometría del caso — el nombre del caso lo documenta.
_DISPATCH_REASON = {
    "csw_open_free_form_charged_post_oct": "csw_free_form",
    "csw_and_ctwa_open_free_form_free": "csw_free_form",
    "ctwa_open_cold_marketing_template_free": "ctwa_72h_free",
    "ctwa_open_hook_utility_template_free": "ctwa_72h_free",
    "phase_b_hook_utility_cheap": "transactional_hook",
    "phase_b_payment_pending_tag_alone_is_hook": "transactional_hook",
    "phase_b_optin_one_paid_marketing": "phase_b_high_value",
}


def test_contrato_carga_y_certifica_C2() -> None:
    c = load_tool(TOOL)
    assert run_tool_checks(c, GA)["errors"] == []
    assert tool_level(c, GA) == "C2"


def test_matrix_checksum_synced_with_hubara() -> None:
    digest = hashlib.sha256(MATRIX.read_bytes()).hexdigest()
    assert digest == MATRIX_SHA256, (
        "La copia local de la matriz divergió de hubara: re-copiá "
        "hubara_agency/tests/platform/fixtures/reengagement_matrix_golden.json "
        "y actualizá MATRIX_SHA256 en ambos lados."
    )


def _payload_from_case(case: dict) -> dict:
    convo = {
        "session_id": f"wa_{case['name']}",
        "service_window_expires_at_ms": case["metadata"][
            "service_window_expires_at_ms"
        ],
        "ctwa_window_expires_at_ms": case["metadata"]["ctwa_window_expires_at_ms"],
        "lead": case["lead"],
    }
    return {"now_ms": _M["now_ms"], "conversations": [convo]}


@pytest.mark.parametrize("case", _M["cases"], ids=[c["name"] for c in _M["cases"]])
def test_impl_golden_espeja_la_matriz(case: dict) -> None:
    out = run(payload=_payload_from_case(case))
    (classified,) = out["conversations"]
    assert classified["session_id"] == f"wa_{case['name']}"
    expect = case["expect"]
    if expect["allowed"]:
        assert classified["action"] == "dispatch", case["name"]
        assert classified["recommended_channel"] == expect["channel"]
        assert (
            classified["recommended_category"] == expect["recommended_category"]
        ), case["name"]
        assert classified["reason"] == _DISPATCH_REASON[case["name"]]
    else:
        assert classified["action"] == "suppress", case["name"]
        assert classified["reason"] == expect["suppress_reason"], case["name"]


def test_falta_now_ms_da_error_de_dominio() -> None:
    with pytest.raises(ValueError):  # MF-7: el seam falla con error de dominio
        run(payload={"conversations": []})
