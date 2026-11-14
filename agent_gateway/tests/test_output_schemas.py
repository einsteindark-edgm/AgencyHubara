"""`outputSchema` de cada tool = contrato Zod del dashboard (generado), y el server lo cumple."""

import copy
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

from hubara_gateway.backend import BackendResponse
from hubara_gateway.catalog import TOOLS
from hubara_gateway.schemas import output_schema
from hubara_gateway.tools import call_tool
from tests.test_contract_fixtures import ROLE_TO_TOOL, ReplayBackend, _args, _entries
from tests.test_contract_fixtures import FIXTURES as FIXTURES_DIR
from tests.test_tools import FakeBackend

REPO = Path(__file__).resolve().parents[2]
TSX = REPO / "frontend_dashboard/node_modules/.bin/tsx"


@pytest.mark.parametrize("tool", sorted(TOOLS))
def test_every_tool_has_object_rooted_draft7_output_schema(tool: str) -> None:
    schema = output_schema(tool)

    assert schema["type"] == "object"
    Draft7Validator.check_schema(schema)
    assert "$schema" not in json.dumps(schema)
    assert '"additionalProperties": false' not in json.dumps(schema)


@pytest.mark.skipif(not (FIXTURES_DIR / "manifest.json").is_file(), reason="fixtures del plugin no disponibles")
@pytest.mark.parametrize("entry", _entries(), ids=lambda e: e["file"])
async def test_real_bodies_conform_to_output_schema(entry: dict) -> None:
    body = json.loads((FIXTURES_DIR / entry["file"]).read_text())["body"]
    tool = ROLE_TO_TOOL[entry["meta"]["role"]]

    for pii_allowed in (True, False):
        result = await call_tool(tool, _args(entry), ReplayBackend(entry, copy.deepcopy(body)), pii_allowed=pii_allowed)
        errors = list(Draft7Validator(output_schema(tool)).iter_errors(result.structured_content))
        assert errors == [], [e.message for e in errors[:3]]


async def test_body_violating_the_contract_is_infra_error_not_bad_structured_content() -> None:
    backend = FakeBackend(BackendResponse(200, {"campaigns": "not-a-list", "orders_stale": False}))

    result = await call_tool("ads_get_campaigns", {}, backend)

    assert result.is_error is True
    assert result.structured_content is None
    assert "contract_violation" in result.content[0].text


async def test_unknown_extra_field_from_backend_does_not_break_the_contract() -> None:
    backend = FakeBackend(BackendResponse(200, {"connected": False, "new_field_2027": 1}))

    result = await call_tool("ads_get_meta_status", {}, backend)

    assert result.is_error is not True
    assert result.structured_content == {"connected": False, "new_field_2027": 1}


def test_committed_schemas_match_the_zod_contracts(tmp_path: Path) -> None:
    if not TSX.exists() or shutil.which("node") is None:
        # En CI (REQUIRE_SCHEMA_DRIFT_CHECK=1) faltar node/tsx es un error, no un skip silencioso (L-10).
        if os.environ.get("REQUIRE_SCHEMA_DRIFT_CHECK") == "1":
            pytest.fail("REQUIRE_SCHEMA_DRIFT_CHECK=1 pero no hay node + frontend_dashboard/node_modules")
        pytest.skip("sin node_modules del frontend")
    subprocess.run(
        [str(TSX), str(REPO / "agent_gateway/scripts/gen_output_schemas.mts")],
        cwd=REPO / "frontend_dashboard",
        env={**os.environ, "SCHEMAS_OUT_DIR": str(tmp_path)},
        check=True,
        capture_output=True,
    )
    committed = REPO / "agent_gateway/hubara_gateway/schemas"
    for tool in TOOLS:
        assert json.loads((tmp_path / f"{tool}.json").read_text()) == json.loads(
            (committed / f"{tool}.json").read_text()
        ), f"{tool}: corré scripts/gen_output_schemas.mts (los contratos Zod cambiaron)"
