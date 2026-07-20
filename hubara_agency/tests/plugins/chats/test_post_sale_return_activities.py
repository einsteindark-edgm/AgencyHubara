"""Tests de las activities del scheduler post-venta (fase roja).

Dos seams de I/O:

* `scan_post_sale_human_sessions_activity` — escanea el vault (dirs `wa_*`,
  metadata.json tolerante a corruptos) y aplica el filtro puro.
* `return_post_sale_session_to_sales_activity` — la mutación del botón
  "devolver al robot" en batch. Guards en orden:
  1. Ningún robot corriendo (`session-{sid}` / `remarketing-{sid}` en Temporal).
  2. **Pedido ENTREGADO** (regla de negocio 2026-07-17): mientras la orden
     esté en proceso (preparación/envío) el humano sigue interactuando para
     moverla de estado — la conversación se queda con él. Se consulta el
     estado REAL vía la API de orders (patrón order_sentinel, respx acá);
     estado inverificable = skip visible, nunca devolver a ciegas.
  3. Mutación vía `update()` (lock + re-check del predicado fresco).
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from temporalio.client import WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode
from temporalio.testing import ActivityEnvironment

import src.plugins.chats.agent.post_sale_return.activities as acts

pytestmark = pytest.mark.asyncio

BASE = "http://hubara-api.invalid"


def _seed(vault: Path, session_id: str, metadata: dict | str) -> None:
    d = vault / session_id
    d.mkdir(parents=True, exist_ok=True)
    text = metadata if isinstance(metadata, str) else json.dumps(metadata)
    (d / "metadata.json").write_text(text, encoding="utf-8")


def _paid_episodes(order_id: str | None = "order_A") -> list[dict]:
    """Como los deja la confirmación de pago del dashboard de orders."""
    ep: dict = {
        "episode_id": "ep-1",
        "closing_tag": "COMPRA_EXITOSA",
        "closed_at_ms": 1_752_000_000_000,
        "payment_confirmed_at_ms": 1_752_000_100_000,
    }
    if order_id:
        ep["order_id"] = order_id
    return [ep]


def _mock_order(router: respx.MockRouter, order_id: str, status: str) -> None:
    """`GET /api/orders/orders/{id}` (ruta REAL con doble `orders`)."""
    router.get(f"{BASE}/api/orders/orders/{order_id}").mock(
        return_value=httpx.Response(
            200, json={"summary": {"status": status, "pay_status": "paid"}}
        )
    )


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


@pytest.fixture
def _no_robots(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    fake = _FakeClient(running_ids=set())

    async def _fake_client():
        return fake

    monkeypatch.setattr(acts, "get_temporal_client", _fake_client)
    monkeypatch.setenv("HUBARA_API_BASE_URL", BASE)
    return fake


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


@respx.mock
async def test_return_con_pedido_entregado_aplica_la_mutacion(
    _isolate_vault_dir: Path, _no_robots: _FakeClient
) -> None:
    _seed(
        _isolate_vault_dir,
        "wa_111",
        {
            "active_route": "humano",
            "tag": "COMPRA_EXITOSA",
            "motivo": "pago confirmado",
            "status_history": [{"tag": "COMPRA_EXITOSA"}],
            "episodes": _paid_episodes("order_A"),
        },
    )
    _mock_order(respx.mock, "order_A", "delivered")

    env = ActivityEnvironment()
    result = await env.run(
        acts.return_post_sale_session_to_sales_activity, "wa_111"
    )
    assert result == "returned"
    # Consultó los DOS robots canónicos antes de tocar metadata.
    assert set(_no_robots.described) == {"session-wa_111", "remarketing-wa_111"}

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


@respx.mock
async def test_return_skipea_si_el_pedido_no_esta_entregado(
    _isolate_vault_dir: Path, _no_robots: _FakeClient
) -> None:
    """Compra pagada pero pedido EN PROCESO (shipping): el humano sigue
    gestionando la entrega — la conversación se queda con él."""
    original = {
        "active_route": "humano",
        "tag": "COMPRA_EXITOSA",
        "episodes": _paid_episodes("order_A"),
    }
    _seed(_isolate_vault_dir, "wa_111", original)
    _mock_order(respx.mock, "order_A", "shipping")

    env = ActivityEnvironment()
    result = await env.run(
        acts.return_post_sale_session_to_sales_activity, "wa_111"
    )
    assert result == "skipped_order_not_delivered"
    data = json.loads(
        (_isolate_vault_dir / "wa_111" / "metadata.json").read_text(encoding="utf-8")
    )
    assert data == original  # intacta


@respx.mock
async def test_return_skipea_si_el_estado_de_la_orden_es_inverificable(
    _isolate_vault_dir: Path, _no_robots: _FakeClient
) -> None:
    """API de orders caída/500: jamás devolver a ciegas — skip visible y el
    próximo ciclo reintenta."""
    original = {
        "active_route": "humano",
        "tag": "COMPRA_EXITOSA",
        "episodes": _paid_episodes("order_A"),
    }
    _seed(_isolate_vault_dir, "wa_111", original)
    respx.mock.get(f"{BASE}/api/orders/orders/order_A").mock(
        return_value=httpx.Response(500)
    )

    env = ActivityEnvironment()
    result = await env.run(
        acts.return_post_sale_session_to_sales_activity, "wa_111"
    )
    assert result == "skipped_order_state_unknown"
    data = json.loads(
        (_isolate_vault_dir / "wa_111" / "metadata.json").read_text(encoding="utf-8")
    )
    assert data == original


async def test_return_skipea_si_no_hay_ordenes_verificables(
    _isolate_vault_dir: Path, _no_robots: _FakeClient
) -> None:
    """Sesión pagada pero sin order_id en metadata: sin forma de verificar la
    entrega → se queda en humano (el botón manual sigue disponible)."""
    original = {
        "active_route": "humano",
        "tag": "COMPRA_EXITOSA",
        "episodes": _paid_episodes(order_id=None),
    }
    _seed(_isolate_vault_dir, "wa_111", original)

    env = ActivityEnvironment()
    result = await env.run(
        acts.return_post_sale_session_to_sales_activity, "wa_111"
    )
    assert result == "skipped_order_state_unknown"
    data = json.loads(
        (_isolate_vault_dir / "wa_111" / "metadata.json").read_text(encoding="utf-8")
    )
    assert data == original


@respx.mock
async def test_multi_orden_exige_todas_terminales_y_una_entregada(
    _isolate_vault_dir: Path, _no_robots: _FakeClient
) -> None:
    """Una orden entregada + otra aún en camino = el humano sigue trabajando.
    Entregada + cancelada = nada pendiente → se devuelve."""
    meta = {
        "active_route": "humano",
        "tag": "COMPRA_EXITOSA",
        "registered_order": {"order_id": "order_B"},
        "episodes": _paid_episodes("order_A"),
    }
    _seed(_isolate_vault_dir, "wa_111", meta)
    _mock_order(respx.mock, "order_A", "delivered")
    _mock_order(respx.mock, "order_B", "shipping")

    env = ActivityEnvironment()
    result = await env.run(
        acts.return_post_sale_session_to_sales_activity, "wa_111"
    )
    assert result == "skipped_order_not_delivered"

    # Segunda pasada: la orden B ya terminó (cancelada) → devolver.
    _mock_order(respx.mock, "order_B", "cancelled")
    result = await env.run(
        acts.return_post_sale_session_to_sales_activity, "wa_111"
    )
    assert result == "returned"


@respx.mock
async def test_return_skipea_si_hay_robot_corriendo_sin_tocar_metadata(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = {
        "active_route": "humano",
        "tag": "COMPRA_EXITOSA",
        "episodes": _paid_episodes("order_A"),
    }
    _seed(_isolate_vault_dir, "wa_111", original)
    fake = _FakeClient(running_ids={"session-wa_111"})

    async def _fake_client():
        return fake

    monkeypatch.setattr(acts, "get_temporal_client", _fake_client)
    monkeypatch.setenv("HUBARA_API_BASE_URL", BASE)

    env = ActivityEnvironment()
    result = await env.run(
        acts.return_post_sale_session_to_sales_activity, "wa_111"
    )
    assert result == "skipped_robot_running"
    data = json.loads(
        (_isolate_vault_dir / "wa_111" / "metadata.json").read_text(encoding="utf-8")
    )
    assert data == original  # intacta (ni siquiera consultó orders)


async def test_return_propaga_rpc_transitorio_sin_tocar_metadata(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un UNAVAILABLE/DEADLINE en el describe NO significa "robot no corre":
    absorberlo mutaría una sesión que puede tener el bot vivo. Debe propagar
    (el RetryPolicy del workflow reintenta; peor caso cuenta como failed)."""
    original = {
        "active_route": "humano",
        "tag": "COMPRA_EXITOSA",
        "episodes": _paid_episodes("order_A"),
    }
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


@respx.mock
async def test_return_aborta_si_el_estado_cambio_entre_scan_y_lock(
    _isolate_vault_dir: Path, _no_robots: _FakeClient
) -> None:
    # Entre el scan y esta activity, el humano retomó la conversación.
    original = {
        "active_route": "humano",
        "tag": "HUMANO",
        "episodes": _paid_episodes("order_A"),
    }
    _seed(_isolate_vault_dir, "wa_111", original)
    _mock_order(respx.mock, "order_A", "delivered")

    env = ActivityEnvironment()
    result = await env.run(
        acts.return_post_sale_session_to_sales_activity, "wa_111"
    )
    assert result == "skipped_state_changed"
    data = json.loads(
        (_isolate_vault_dir / "wa_111" / "metadata.json").read_text(encoding="utf-8")
    )
    assert data == original  # el mutator abortó: nada escrito


@respx.mock
async def test_return_aborta_si_no_hay_pago_confirmado_en_el_recheck(
    _isolate_vault_dir: Path, _no_robots: _FakeClient
) -> None:
    """El re-check bajo lock usa el predicado COMPLETO (is_returnable): una
    sesión con tag COMPRA_EXITOSA pero SIN pago verificado no se devuelve —
    se queda en humano hasta que el humano confirme el pago."""
    original = {
        "active_route": "humano",
        "tag": "COMPRA_EXITOSA",
        "episodes": [
            {
                "closing_tag": "COMPRA_EXITOSA",
                "closed_at_ms": 1,
                "order_id": "order_A",
            }
        ],
    }
    _seed(_isolate_vault_dir, "wa_111", original)
    _mock_order(respx.mock, "order_A", "delivered")

    env = ActivityEnvironment()
    result = await env.run(
        acts.return_post_sale_session_to_sales_activity, "wa_111"
    )
    assert result == "skipped_state_changed"
    data = json.loads(
        (_isolate_vault_dir / "wa_111" / "metadata.json").read_text(encoding="utf-8")
    )
    assert data == original  # intacta
