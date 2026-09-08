"""D1.5 — `IngestHandover`: `messaging_handovers` → `control_owner` por sesión.

Vault REAL (gotcha 1): `metadata.json` de la sesión. Quién controla el hilo lo
decide el `new_owner_app_id` del webhook contra NUESTRO app id (`WHATSAPP_APP_ID`):
igual → `hubara`; distinto → `mba`. Sin app id configurado no se decide nada.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.constants import CONTROL_OWNER_HUBARA, CONTROL_OWNER_MBA
from src.platform.state import FilesystemMetadataStore
from src.plugins.chats.agent.sales.parsers import parse_messaging_handovers
from src.plugins.chats.agent.sales.use_cases import ingest_handover as mod
from src.plugins.chats.agent.sales.use_cases.ingest_handover import (
    CONTROL_HISTORY_CAP,
    HandoverIngestResult,
    IngestHandover,
    control_owner_for,
)
from tests.plugins.chats import standby_payloads as P

SESSION = f"wa_{P.CUSTOMER}"
NOW_MS = 1_757_300_000_000


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    v.mkdir()
    return v


def _use_case(vault: Path, *, our_app_id: str = P.OUR_APP_ID, allowed=lambda customer: True,
              now_ms: int = NOW_MS) -> IngestHandover:
    return IngestHandover(
        metadata_store=FilesystemMetadataStore(vault),
        vault_dir=vault,
        now_ms=lambda: now_ms,
        is_customer_allowed=allowed,
        our_app_id=lambda: our_app_id,
    )


def _meta(vault: Path) -> dict[str, Any]:
    return json.loads((vault / SESSION / "metadata.json").read_text(encoding="utf-8"))


async def _run(vault: Path, body: dict[str, Any], **kw: Any) -> HandoverIngestResult:
    return await _use_case(vault, **kw).execute(parse_messaging_handovers(body))


def test_control_owner_for_is_pure_and_only_decides_with_our_app_id_configured() -> None:
    assert control_owner_for(P.OUR_APP_ID, our_app_id=P.OUR_APP_ID) == CONTROL_OWNER_HUBARA == "hubara"
    assert control_owner_for(P.MBA_APP_ID, our_app_id=P.OUR_APP_ID) == CONTROL_OWNER_MBA == "mba"
    assert control_owner_for(f" {P.OUR_APP_ID} ", our_app_id=P.OUR_APP_ID) == "hubara"
    assert control_owner_for(None, our_app_id=P.OUR_APP_ID) is None
    assert control_owner_for(P.OUR_APP_ID, our_app_id="") is None
    assert control_owner_for("", our_app_id=P.OUR_APP_ID) is None


async def test_sending_from_hubara_takes_the_thread_mba_to_hubara_and_keeps_the_rest_of_the_metadata(vault: Path) -> None:
    FilesystemMetadataStore(vault).write(SESSION, {"tag": "INTERESADO", "active_route": "ventas"})
    res = await _run(vault, P.handover(P.OUR_APP_ID, P.MBA_APP_ID, ts="1757300060", metadata="tomado al enviar"))
    assert res == HandoverIngestResult(applied=1, sessions=(SESSION,))
    m = _meta(vault)
    assert m["control_owner"] == "hubara"
    assert m["control_owner_since_ms"] == 1757300060000
    assert m["control_owner_updated_at_ms"] == 1757300060000
    assert m["control_owner_app_id"] == P.OUR_APP_ID
    assert m["tag"] == "INTERESADO" and m["active_route"] == "ventas"
    assert m["control_history"] == [{
        "owner": "hubara", "kind": "control_taken", "previous_owner_app_id": P.MBA_APP_ID,
        "new_owner_app_id": P.OUR_APP_ID, "at_ms": 1757300060000, "received_at_ms": NOW_MS,
        "metadata": "tomado al enviar", "source": "messaging_handovers",
    }]


async def test_release_gives_the_thread_back_to_mba_hubara_to_mba(vault: Path) -> None:
    await _run(vault, P.handover(P.OUR_APP_ID, P.MBA_APP_ID, ts="1757300050"))
    res = await _run(vault, P.handover_messenger_style(P.MBA_APP_ID, P.OUR_APP_ID))  # ts 1757300060000
    assert res.applied == 1
    m = _meta(vault)
    assert m["control_owner"] == "mba" and m["control_owner_app_id"] == P.MBA_APP_ID
    assert m["control_owner_since_ms"] == 1757300060000
    assert [h["owner"] for h in m["control_history"]] == ["hubara", "mba"]
    assert m["control_history"][-1]["kind"] == "pass_thread_control" and m["control_history"][-1]["metadata"] == "release"


async def test_a_first_handover_for_an_unknown_customer_creates_the_session(vault: Path) -> None:
    """Meta puede avisar del control ANTES del primer inbound `standby` (o el
    cliente nunca escribió): la sesión nace con solo el control."""
    res = await _run(vault, P.handover(P.MBA_APP_ID, None, ts="1757300060"))
    assert res.applied == 1 and _meta(vault)["control_owner"] == "mba"
    assert _meta(vault)["control_history"][0]["previous_owner_app_id"] is None


async def test_a_redelivered_handover_changes_nothing(vault: Path) -> None:
    body = P.handover(P.OUR_APP_ID, P.MBA_APP_ID, ts="1757300060")
    assert (await _run(vault, body)).applied == 1
    res = await _run(vault, body, now_ms=NOW_MS + 5_000)
    assert res == HandoverIngestResult(unchanged=1, sessions=(SESSION,))
    m = _meta(vault)
    assert len(m["control_history"]) == 1 and m["control_history"][0]["received_at_ms"] == NOW_MS


async def test_a_late_redelivery_of_an_older_event_does_not_flip_the_owner(vault: Path) -> None:
    """Take (t1) → release (t2) → el take de t1 reentregado tarde: el dueño
    sigue siendo MBA (M-1 de la revisión)."""
    take = P.handover(P.OUR_APP_ID, P.MBA_APP_ID, ts="1757300050")
    await _run(vault, take)
    await _run(vault, P.handover_messenger_style(P.MBA_APP_ID, P.OUR_APP_ID))  # t2 = 1757300060000
    res = await _run(vault, take, now_ms=NOW_MS + 9_000)
    assert res == HandoverIngestResult(unchanged=1, sessions=(SESSION,))  # ya está en la historia
    older = P.handover(P.OUR_APP_ID, P.MBA_APP_ID, ts="1757300055", kind="take_thread_control")
    res = await _run(vault, older, now_ms=NOW_MS + 9_000)
    assert res == HandoverIngestResult(stale=1, sessions=(SESSION,))
    m = _meta(vault)
    assert m["control_owner"] == "mba" and m["control_owner_updated_at_ms"] == 1757300060000
    assert [h["owner"] for h in m["control_history"]] == ["hubara", "mba"]


async def test_events_without_a_timestamp_are_deduped_by_content_against_the_last_one(vault: Path) -> None:
    body = P.handover(P.OUR_APP_ID, P.MBA_APP_ID, metadata="x")
    del body["entry"][0]["changes"][0]["value"]["messaging_handovers"][0]["timestamp"]
    assert (await _run(vault, body)).applied == 1
    assert _meta(vault)["control_owner_since_ms"] == NOW_MS  # hora de recepción
    res = await _run(vault, body, now_ms=NOW_MS + 3_000)
    assert res == HandoverIngestResult(unchanged=1, sessions=(SESSION,))
    release = P.handover(P.MBA_APP_ID, P.OUR_APP_ID, metadata="release")
    del release["entry"][0]["changes"][0]["value"]["messaging_handovers"][0]["timestamp"]
    assert (await _run(vault, release, now_ms=NOW_MS + 4_000)).applied == 1
    assert _meta(vault)["control_owner"] == "mba" and len(_meta(vault)["control_history"]) == 2


async def test_a_mixed_body_applies_the_allowed_customer_and_rejects_the_other(vault: Path) -> None:
    other = "573009999999"
    body = P.merged(P.handover(), P.handover(customer=other))
    res = await _run(vault, body, allowed=lambda c: c == P.CUSTOMER)
    assert res == HandoverIngestResult(applied=1, rejected=1, sessions=(SESSION,))
    assert not (vault / f"wa_{other}").exists()


async def test_the_same_owner_again_keeps_since_but_records_the_event(vault: Path) -> None:
    await _run(vault, P.handover(P.OUR_APP_ID, P.MBA_APP_ID, ts="1757300050"))
    await _run(vault, P.handover(P.OUR_APP_ID, P.OUR_APP_ID, ts="1757300060"))
    m = _meta(vault)
    assert m["control_owner"] == "hubara"
    assert m["control_owner_since_ms"] == 1757300050000 and m["control_owner_updated_at_ms"] == 1757300060000
    assert len(m["control_history"]) == 2


async def test_customers_outside_the_closed_list_are_rejected_and_nothing_is_written(vault: Path) -> None:
    res = await _run(vault, P.handover(), allowed=lambda customer: False)
    assert res == HandoverIngestResult(rejected=1)
    assert not (vault / SESSION).exists()


async def test_without_our_app_id_configured_nothing_is_decided_nor_written(vault: Path) -> None:
    res = await _run(vault, P.handover(), our_app_id="")
    assert res == HandoverIngestResult(skipped=1)
    assert not (vault / SESSION / "metadata.json").exists()


async def test_a_null_new_owner_is_skipped(vault: Path) -> None:
    res = await _run(vault, P.handover(new_owner=None, previous_owner=P.OUR_APP_ID))
    assert res == HandoverIngestResult(skipped=1)


async def test_the_history_is_capped(vault: Path) -> None:
    uc = _use_case(vault)
    for i in range(CONTROL_HISTORY_CAP + 7):
        owner = P.OUR_APP_ID if i % 2 else P.MBA_APP_ID
        await uc.execute(parse_messaging_handovers(P.handover(owner, None, ts=str(1757300000 + i))))
    hist = _meta(vault)["control_history"]
    assert len(hist) == CONTROL_HISTORY_CAP and hist[-1]["at_ms"] == (1757300000 + CONTROL_HISTORY_CAP + 6) * 1000


def test_the_module_only_imports_the_sdk_and_its_own_plugin() -> None:
    """P-28 + nada de Temporal: el handover no arranca workflows ni emite eventos."""
    tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    src_imports = {m for m in imported if m.startswith("src.")}
    assert src_imports and all(m.startswith(("src.sdk.", "src.plugins.chats.")) for m in src_imports), src_imports
    assert not any("temporal" in m or "event" in m for m in src_imports)


async def test_the_mba_control_endpoint_reads_what_chats_wrote(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """E2E entre plugins por el vault: chats escribe, mba expone
    `GET /api/mba/sessions/{session_key}/control` (plano de gestión)."""
    from src.plugins.mba import api as mba_api

    monkeypatch.setattr(mba_api, "WORKSPACE_VAULT_DIR", vault)
    app = FastAPI()
    app.include_router(mba_api.router, prefix="/api/mba")
    client = TestClient(app)
    assert client.get(f"/api/mba/sessions/{SESSION}/control").status_code == 404
    assert client.get(f"/api/mba/sessions/{SESSION}%0A/control").status_code == 422  # `$` vs newline
    await _run(vault, P.handover(P.OUR_APP_ID, P.MBA_APP_ID, ts="1757300060", metadata="x"))
    r = client.get(f"/api/mba/sessions/{SESSION}/control")
    assert r.status_code == 200
    body = r.json()
    assert body["session_key"] == SESSION and body["control_owner"] == "hubara"
    assert body["control_owner_since_ms"] == 1757300060000 and body["control_owner_app_id"] == P.OUR_APP_ID
    assert body["history"][-1]["kind"] == "control_taken"
