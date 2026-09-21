"""BODY component del submit de plantillas.

Una plantilla SIN variables (`followup_interest_marketing_v1`, escalera de
reactivación 2026-09-18) no lleva `example`: mandar `{"body_text": [[]]}` hace
que Meta rechace el submit.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import whatsapp_provision as wp  # noqa: E402


def test_body_without_variables_omits_example():
    comp = wp._body_component({"body": "Hola, sin variables.", "example": []})
    assert comp == {"type": "BODY", "text": "Hola, sin variables."}


def test_body_with_variables_keeps_example():
    comp = wp._body_component({"body": "Hola {{1}}", "example": ["Camila"]})
    assert comp == {
        "type": "BODY",
        "text": "Hola {{1}}",
        "example": {"body_text": [["Camila"]]},
    }
