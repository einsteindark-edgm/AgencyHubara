"""Tests de las activities del scheduler post-venta (fase roja).

Dos seams de I/O:

* `scan_post_sale_human_sessions_activity` — escanea el vault (dirs `wa_*`,
  metadata.json tolerante a corruptos) y aplica el filtro puro.
* `return_post_sale_session_to_sales_activity` — la mutación del botón
  "devolver al robot" en batch: chequea que NO haya robot corriendo
  (`session-{sid}` / `remarketing-{sid}` RUNNING en Temporal) y aplica
  tag=RETOMA_VENTA + active_route=ventas vía `update()` (lock + re-check del
  predicado fresco — un cliente pudo escribir entre el scan y el write).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from temporalio.client import WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode
from temporalio.testing import ActivityEnvironment

import src.plugins.chats.agent.post_sale_return.activities as acts

pytestmark = pytest.mark.asyncio


def _seed(vault: Path, session_id: str, metadata: dict | str) -> None:
    d = vault / session_id
    d.mkdir(parents=True, exist_ok=True)
    text = metadata if isinstance(metadata, str) else json.dumps(metadata)
    (d / "metadata.json").write_text(text, encoding="utf-8")


def _paid_episodes() -> list[dict]:
    """Como los deja la confirmación de pago del dashboard de orders."""
    return [
        {
            "episode_id": "ep-1",
            "closing_tag": "COMPRA_EXITOSA",
            "closed_at_ms": 1_752_000_000_000,
            "payment_confirmed_at_ms": 1_752_000_100_000,
        }
    ]


class _FakeHandle:
    def __init__(self, status: WorkflowExecutionStatus | None) -> None:
        self._status = status

    async def describe(self):
        if self._status is None:
            # La firma REAL del backend postgres de Temporal (ver
            # tests/platform/orchestration/test_dispatcher.py:533).
            raise RPCError(
                "sql: no rows in result set", RPCStatusCode.NOT_FOUND, None  # type: ignore[arg-type]
            )

        class _Desc:
            status = self._status

        return _Desc()


class _FakeClient:
    """`running_ids`: workflow ids que aparecen como RUNNING en Temporal."""

    def __init__(self, running_ids: set[str] | None = None) -> None:
        self.running_ids = running_ids or set()
        self.described: list[str] = []

    def get_workflow_handle(self, workflow_id: str) -> _FakeHandle:
        self.described.append(workflow_id)
        if workflow_id in self.running_ids:
            return _FakeHandle(WorkflowExecutionStatus.RUNNING)
        return _FakeHandle(None)


async def test_scan_devuelve_solo_compra_exitosa_en_humano(
    _isolate_vault_dir: Path,
) -> None:
    _seed(
        _isolate_vault_dir,
        "wa_111",
        {
            "active_route": "humano",
            "tag": "COMPRA_EXITOSA",
            "episodes": _paid_episodes(),
        },
    )
    _seed(_isolate_vault_dir, "wa_222", {"active_route": "humano", "tag": "HUMANO"})
    _seed(
        _isolate_vault_dir,
        "wa_333",
        {
            "active_route": "ventas",
            "tag": "COMPRA_EXITOSA",
            "episodes": _paid_episodes(),
        },
    )
    _seed(_isolate_vault_dir, "wa_444", "{corrupto")  # no tumba el scan
    _seed(_isolate_vault_dir, "_campaigns", {"active_route": "humano"})  # no wa_*

    env = ActivityEnvironment()
    result = await env.run(acts.scan_post_sale_human_sessions_activity)
    assert result == ["wa_111"]


async def test_return_aplica_la_mutacion_del_boton_devolver_al_robot(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(
        _isolate_vault_dir,
        "wa_111",
        {
            "active_route": "humano",
            "tag": "COMPRA_EXITOSA",
            "motivo": "pago confirmado",
            "status_history": [{"tag": "COMPRA_EXITOSA"}],
            "episodes": _paid_episodes(),
        },
    )
    fake = _FakeClient(running_ids=set())

    async def _fake_client():
        return fake

    monkeypatch.setattr(acts, "get_temporal_client", _fake_client)

    env = ActivityEnvironment()
    result = await env.run(
        acts.return_post_sale_session_to_sales_activity, "wa_111"
    )
    assert result == "returned"
    # Consultó los DOS robots canónicos antes de tocar metadata.
    assert set(fake.described) == {"session-wa_111", "remarketing-wa_111"}

    data = json.loads(
        (_isolate_vault_dir / "wa_111" / "metadata.json").read_text(encoding="utf-8")
    )
    assert data["active_route"] == "ventas"
    assert data["tag"] == "RETOMA_VENTA"
    # Misma historia continua que el botón: appendea, no pisa.
    assert len(data["status_history"]) == 2
    last = data["status_history"][-1]
    assert last["tag"] == "RETOMA_VENTA"
    assert last["active_route"] == "ventas"
    assert last["source"] == "post_sale_return_scheduler"
    assert last["timestamp"] > 0


async def test_return_skipea_si_hay_robot_corriendo_sin_tocar_metadata(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = {
        "active_route": "humano",
        "tag": "COMPRA_EXITOSA",
        "episodes": _paid_episodes(),
    }
    _seed(_isolate_vault_dir, "wa_111", original)
    fake = _FakeClient(running_ids={"session-wa_111"})

    async def _fake_client():
        return fake

    monkeypatch.setattr(acts, "get_temporal_client", _fake_client)

    env = ActivityEnvironment()
    result = await env.run(
        acts.return_post_sale_session_to_sales_activity, "wa_111"
    )
    assert result == "skipped_robot_running"
    data = json.loads(
        (_isolate_vault_dir / "wa_111" / "metadata.json").read_text(encoding="utf-8")
    )
    assert data == original  # intacta


async def test_return_propaga_rpc_transitorio_sin_tocar_metadata(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un UNAVAILABLE/DEADLINE en el describe NO significa "robot no corre":
    absorberlo mutaría una sesión que puede tener el bot vivo. Debe propagar
    (el RetryPolicy del workflow reintenta; peor caso cuenta como failed)."""
    original = {"active_route": "humano", "tag": "COMPRA_EXITOSA"}
    _seed(_isolate_vault_dir, "wa_111", original)

    class _UnavailableHandle:
        async def describe(self):
            raise RPCError(
                "service unavailable", RPCStatusCode.UNAVAILABLE, None  # type: ignore[arg-type]
            )

    class _UnavailableClient:
        def get_workflow_handle(self, workflow_id: str):
            return _UnavailableHandle()

    async def _fake_client():
        return _UnavailableClient()

    monkeypatch.setattr(acts, "get_temporal_client", _fake_client)

    env = ActivityEnvironment()
    with pytest.raises(RPCError):
        await env.run(acts.return_post_sale_session_to_sales_activity, "wa_111")
    data = json.loads(
        (_isolate_vault_dir / "wa_111" / "metadata.json").read_text(encoding="utf-8")
    )
    assert data == original  # intacta — no se decidió con información rota


async def test_return_aborta_si_el_estado_cambio_entre_scan_y_lock(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Entre el scan y esta activity, el humano retomó la conversación.
    original = {"active_route": "humano", "tag": "HUMANO"}
    _seed(_isolate_vault_dir, "wa_111", original)
    fake = _FakeClient(running_ids=set())

    async def _fake_client():
        return fake

    monkeypatch.setattr(acts, "get_temporal_client", _fake_client)

    env = ActivityEnvironment()
    result = await env.run(
        acts.return_post_sale_session_to_sales_activity, "wa_111"
    )
    assert result == "skipped_state_changed"
    data = json.loads(
        (_isolate_vault_dir / "wa_111" / "metadata.json").read_text(encoding="utf-8")
    )
    assert data == original  # el mutator abortó: nada escrito


async def test_return_aborta_si_no_hay_pago_confirmado_en_el_recheck(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El re-check bajo lock usa el predicado COMPLETO (is_returnable): una
    sesión con tag COMPRA_EXITOSA pero SIN pago verificado no se devuelve —
    se queda en humano hasta que el humano confirme el pago."""
    original = {
        "active_route": "humano",
        "tag": "COMPRA_EXITOSA",
        "episodes": [{"closing_tag": "COMPRA_EXITOSA", "closed_at_ms": 1}],
    }
    _seed(_isolate_vault_dir, "wa_111", original)
    fake = _FakeClient(running_ids=set())

    async def _fake_client():
        return fake

    monkeypatch.setattr(acts, "get_temporal_client", _fake_client)

    env = ActivityEnvironment()
    result = await env.run(
        acts.return_post_sale_session_to_sales_activity, "wa_111"
    )
    assert result == "skipped_state_changed"
    data = json.loads(
        (_isolate_vault_dir / "wa_111" / "metadata.json").read_text(encoding="utf-8")
    )
    assert data == original  # intacta
