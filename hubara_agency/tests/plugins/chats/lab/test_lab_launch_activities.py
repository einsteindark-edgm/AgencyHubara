"""Activities del lanzador de corridas (plan §3.7), con el almacén en disco y
dobles del lanzador, de las promociones y de los order facts."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from src.plugins.chats.agent.sales_lab.launch import activities as acts
from src.plugins.chats.agent.sales_lab.launch.contracts import (
    DispatchInput,
    ExportBenchInput,
    LabOrder,
    PollInput,
)
from src.platform.orders.facts import OrderFactsStore
from src.sdk.connectorkit import FakePromotionsPort, OrderFacts, OrderFactsSnapshot, PromotionsUnavailableError
from src.sdk.labkit import FilesystemLabStore
from tests.platform.orders.fakes import FakeQuery, order_summary
from tests.plugins.chats.lab.test_bench_export import NOW_MS, SINCE_MS, _vault


class FakeFacts:
    def __init__(self) -> None:
        self.asked: list[set[str]] = []
        self.test_orders: set[str] = set()  # marcados "prueba" en Órdenes
        self.down = False

    async def get_facts(self, order_ids):
        ids = set(order_ids)
        self.asked.append(ids)
        if self.down:
            raise ConnectionError("Medusa no responde")
        facts = {
            i: OrderFacts(order_id=i, display_id="31", total_cop=89000, currency_code="cop",
                          pay_status="paid", stage="delivered", customer="Cliente", is_draft=False,
                          is_test=i in self.test_orders)
            for i in ids
        }
        return OrderFactsSnapshot(facts=facts)

    def invalidate(self, order_id=None) -> None:
        return None


class FakeLauncher:
    def __init__(self, answer: str = "dispatched") -> None:
        self.calls: list[tuple] = []
        self.answer = answer

    def start_box(self) -> None:
        self.calls.append(("start",))

    def dispatch(self, run_id: str, image: str) -> str:
        self.calls.append(("dispatch", run_id, image))
        return self.answer

    def cancel(self, run_id: str) -> str:
        self.calls.append(("cancel", run_id))
        return "cancel_requested"


@pytest.fixture
def lab(tmp_path: Path, monkeypatch) -> dict:
    vault = _vault(tmp_path)
    meta = vault / "wa_573001234567" / "metadata.json"
    data = json.loads(meta.read_text())
    data["episodes"][0]["order_id"] = "order_01TEST"
    meta.write_text(json.dumps(data))
    store = FilesystemLabStore(tmp_path / "bucket")
    launcher = FakeLauncher()
    facts = FakeFacts()
    monkeypatch.setattr(acts, "get_lab_store", lambda: store)
    monkeypatch.setattr(acts, "get_lab_launcher", lambda: launcher)
    monkeypatch.setattr(acts, "_vault_dir", lambda: vault)
    monkeypatch.setattr(acts, "get_order_facts_port", lambda: facts)
    monkeypatch.setattr(acts, "get_promotions_port", lambda: FakePromotionsPort([]))
    monkeypatch.setattr(acts, "_now_ms", lambda: NOW_MS)
    monkeypatch.setenv("LAB_INTERNAL_NUMBERS", "573000000099")
    return {"store": store, "launcher": launcher, "facts": facts, "vault": vault}


async def test_export_uploads_the_bench_with_promotions_and_the_facts_of_its_orders(lab) -> None:
    info = await ActivityEnvironment().run(
        acts.export_bench_snapshot_activity, ExportBenchInput(bench_id="bench-run-20260923-a1b2", since_ms=SINCE_MS)
    )

    assert (info.sessions, info.customer_turns) == (1, 1)
    store = lab["store"]
    manifest = json.loads(store.get_bytes("bench/bench-run-20260923-a1b2/manifest.json"))
    assert manifest["counts"]["sessions"] == 1
    assert json.loads(store.get_bytes("bench/bench-run-20260923-a1b2/promotions.json")) == []
    facts = json.loads(store.get_bytes("bench/bench-run-20260923-a1b2/order_facts.json"))
    assert facts["order_01TEST"]["pay_status"] == "paid"
    assert lab["facts"].asked == [{"order_01TEST"}]


async def test_export_leaves_out_the_conversation_whose_order_is_marked_as_test(lab) -> None:
    lab["facts"].test_orders = {"order_01TEST"}

    info = await ActivityEnvironment().run(
        acts.export_bench_snapshot_activity, ExportBenchInput(bench_id="bench-run-20260923-a1b2", since_ms=SINCE_MS)
    )

    manifest = json.loads(lab["store"].get_bytes("bench/bench-run-20260923-a1b2/manifest.json"))
    assert {"session_id": "wa_573001234567", "reason": "pedido_de_prueba"} in manifest["exclusions"]
    assert (info.sessions, info.customer_turns) == (0, 0)
    assert not lab["store"].get_bytes("bench/bench-run-20260923-a1b2/vault/wa_573001234567/metadata.json")
    assert json.loads(lab["store"].get_bytes("bench/bench-run-20260923-a1b2/order_facts.json")) == {}


async def test_export_survives_order_facts_down_keeping_the_conversation_with_a_note(lab) -> None:
    lab["facts"].down = True

    info = await ActivityEnvironment().run(
        acts.export_bench_snapshot_activity, ExportBenchInput(bench_id="bench-run-20260923-a1b2", since_ms=SINCE_MS)
    )

    manifest = json.loads(lab["store"].get_bytes("bench/bench-run-20260923-a1b2/manifest.json"))
    assert info.sessions == 1
    assert manifest["notes"] == [
        "1 conversación con pedido quedó en el banco sin verificar si el pedido es de prueba (OrderFacts no respondió)"
    ]


class HangingFacts:
    def invalidate(self, order_id=None) -> None:
        return None

    async def get_facts(self, order_ids):
        await asyncio.Event().wait()  # Medusa colgado: nunca contesta


async def test_export_does_not_wait_forever_for_order_facts(lab, monkeypatch) -> None:
    monkeypatch.setattr(acts, "get_order_facts_port", lambda: HangingFacts())
    monkeypatch.setattr(acts, "_ORDER_FACTS_TIMEOUT_S", 0.05)

    info = await ActivityEnvironment().run(
        acts.export_bench_snapshot_activity, ExportBenchInput(bench_id="bench-run-20260923-a1b2", since_ms=SINCE_MS)
    )

    manifest = json.loads(lab["store"].get_bytes("bench/bench-run-20260923-a1b2/manifest.json"))
    assert info.sessions == 1
    assert manifest["notes"] == [
        "1 conversación con pedido quedó en el banco sin verificar si el pedido es de prueba (OrderFacts no respondió)"
    ]


class DownPromotions:
    async def list_active(self):
        raise PromotionsUnavailableError("Medusa no responde")


async def test_without_promotions_the_export_fails_in_sight_before_waiting_for_order_facts(lab, monkeypatch) -> None:
    # Con Medusa caído la caja no tiene cupones que simular: el export falla a la vista
    # (el lanzador lo muestra), sin gastar antes la espera de OrderFacts.
    monkeypatch.setattr(acts, "get_promotions_port", lambda: DownPromotions())

    with pytest.raises(PromotionsUnavailableError):
        await ActivityEnvironment().run(
            acts.export_bench_snapshot_activity, ExportBenchInput(bench_id="bench-run-20260923-a1b2", since_ms=SINCE_MS)
        )

    assert lab["facts"].asked == []
    assert lab["store"].get_bytes("bench/bench-run-20260923-a1b2/manifest.json") is None


async def test_a_test_mark_made_after_an_earlier_export_counts_in_the_next_bench(lab, monkeypatch) -> None:
    # El store del worker `sales_eval` no oye el bus del dashboard (vive en la API):
    # un valor vencido se serviría tal cual. El banco tiene que leer la marca de hoy.
    now = [0.0]
    medusa = FakeQuery([order_summary("order_01TEST", 89000)])  # todavía no es de prueba
    store = OrderFactsStore(medusa, ttl_s=60, clock=lambda: now[0])
    monkeypatch.setattr(acts, "get_order_facts_port", lambda: store)
    env = ActivityEnvironment()
    await env.run(acts.export_bench_snapshot_activity, ExportBenchInput(bench_id="bench-run-20260923-a1b2", since_ms=SINCE_MS))

    medusa.edit("order_01TEST", is_test=True)  # el operador lo marca "prueba" en Órdenes
    now[0] += 3600
    await env.run(acts.export_bench_snapshot_activity, ExportBenchInput(bench_id="bench-run-20260923-c3d4", since_ms=SINCE_MS))

    manifest = json.loads(lab["store"].get_bytes("bench/bench-run-20260923-c3d4/manifest.json"))
    assert {"session_id": "wa_573001234567", "reason": "pedido_de_prueba"} in manifest["exclusions"]


async def test_export_without_a_lab_store_fails_without_retries(monkeypatch) -> None:
    monkeypatch.setattr(acts, "get_lab_store", lambda: None)

    with pytest.raises(ApplicationError) as err:
        await ActivityEnvironment().run(
            acts.export_bench_snapshot_activity, ExportBenchInput(bench_id="bench-x-123456", since_ms=SINCE_MS)
        )
    assert err.value.non_retryable


async def test_read_bench_info_of_an_existing_bench(lab) -> None:
    await ActivityEnvironment().run(
        acts.export_bench_snapshot_activity, ExportBenchInput(bench_id="bench-run-20260923-a1b2", since_ms=SINCE_MS)
    )

    info = await ActivityEnvironment().run(acts.read_bench_info_activity, "bench-run-20260923-a1b2")

    assert info.bench_id == "bench-run-20260923-a1b2" and info.sessions == 1


async def test_read_bench_info_of_a_missing_bench_is_a_clear_error(lab) -> None:
    with pytest.raises(ApplicationError, match="no existe"):
        await ActivityEnvironment().run(acts.read_bench_info_activity, "bench-no-existe")


async def test_order_is_written_where_the_box_reads_it(lab) -> None:
    order = LabOrder(run_id="run-20260923-a1b2", bench_id="bench-run-20260923-a1b2", arms=["A1", "B"], reps=1,
                     image="ghcr.io/o/agencyhubara:abc", estimate_usd=20.0, spend_limit_usd=120.0, requested_at_ms=NOW_MS)

    await ActivityEnvironment().run(acts.write_order_activity, order)

    saved = json.loads(lab["store"].get_bytes("orders/run-20260923-a1b2.json"))
    assert saved["arms"] == ["A1", "B"] and saved["spend_limit_usd"] == 120.0


async def test_box_activities_go_through_the_launcher(lab) -> None:
    env = ActivityEnvironment()

    await env.run(acts.start_box_activity)
    answer = await env.run(acts.dispatch_run_activity, DispatchInput(run_id="run-20260923-a1b2", image="ghcr.io/o/a:b"))
    cancelled = await env.run(acts.cancel_run_activity, "run-20260923-a1b2")

    assert (answer, cancelled) == ("dispatched", "cancel_requested")
    assert [c[0] for c in lab["launcher"].calls] == ["start", "dispatch", "cancel"]


async def test_poll_returns_when_the_box_reports_a_terminal_phase(lab) -> None:
    lab["store"].put_bytes(
        "runs/run-20260923-a1b2/progress.json",
        json.dumps({"run_id": "run-20260923-a1b2", "phase": "done", "turns_done": 20, "turns_total": 20,
                    "spent_usd": 18.2, "updated_at_ms": NOW_MS}).encode(),
    )

    progress = await ActivityEnvironment().run(acts.poll_run_activity, PollInput(run_id="run-20260923-a1b2", poll_s=0.01))

    assert (progress.phase, progress.turns_done, progress.spent_usd) == ("done", 20, 18.2)


async def test_poll_fails_if_the_box_never_reports(lab) -> None:
    with pytest.raises(ApplicationError, match="no reportó") as err:
        await ActivityEnvironment().run(
            acts.poll_run_activity, PollInput(run_id="run-20260923-a1b2", poll_s=0.01, start_grace_s=0.05)
        )
    assert err.value.non_retryable


async def test_poll_fails_if_the_box_stops_reporting(lab, monkeypatch) -> None:
    lab["store"].put_bytes(
        "runs/run-20260923-a1b2/progress.json",
        json.dumps({"run_id": "run-20260923-a1b2", "phase": "running", "updated_at_ms": NOW_MS - 3_600_000}).encode(),
    )

    with pytest.raises(ApplicationError, match="dejó de reportar"):
        await ActivityEnvironment().run(
            acts.poll_run_activity, PollInput(run_id="run-20260923-a1b2", poll_s=0.01, stale_after_s=60)
        )
