"""The ONLY fakes of the sandbox — external systems, nothing else.

* ``SandboxMedusaClient``  — subclass of the REAL ``HttpMedusaClient``: every
  public method (list/get/patch/convert/cancel/payments/promotions) is the repo's
  own code; only the transport (``_do_request``) is replaced by an emulation of
  the Medusa v2 Admin REST endpoints over ``data/medusa/store.json``. So
  ``MedusaOrderQuery`` / ``MedusaOrderCommand`` / ``OrderFactsStore`` /
  ``MedusaPromotionsPort`` all run for real on top of it.
* ``FakeTemporalClient``    — records ``start_workflow`` / ``terminate`` /
  ``signal`` to ``temporal.log``; "running" workflow ids live in
  ``data/temporal/workflows.json``. Never opens a connection.
* ``install_whatsapp_recorder`` — wraps ``src.platform.whatsapp.client``: the
  repo's own FakeSend path runs (``WHATSAPP_ACCESS_TOKEN`` is empty) and every
  would-be WhatsApp payload is appended to ``sent.log``.

Imported by ``launcher.py`` inside the worktree venv (it imports repo modules).
"""
from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sandbox_common import (
    MEDUSA_STORE,
    SANDBOX_DIR,
    TEMPORAL_STORE,
    iso_utc,
    iso_z,
    locked_update,
    now_ms,
    read_json,
)

SENT_LOG = SANDBOX_DIR / "sent.log"
TEMPORAL_LOG = SANDBOX_DIR / "temporal.log"
MEDUSA_LOG = SANDBOX_DIR / "medusa.log"
BLOCKED_LOG = SANDBOX_DIR / "blocked.log"


def log_line(path: Path, record: dict[str, Any]) -> None:
    record = {"ts": iso_utc(now_ms()), **record}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# Medusa v2 Admin API emulation (transport-level fake)
# ─────────────────────────────────────────────────────────────────────────────


def build_medusa_client_class(http_client_cls: type, api_error_cls: type) -> type:
    """Create the subclass lazily (the repo classes are imported by the launcher)."""

    class _NoNetwork:
        def __getattr__(self, name: str) -> Any:
            raise RuntimeError("sandbox: the fake Medusa client never opens HTTP connections")

        async def aclose(self) -> None:
            return None

    class SandboxMedusaClient(http_client_cls):  # type: ignore[misc, valid-type]
        """``HttpMedusaClient`` whose transport is an in-process Medusa emulation."""

        def __init__(self, store_path: Path = MEDUSA_STORE) -> None:  # noqa: D401 — no super(): no httpx client
            self.base_url = "http://medusa.sandbox.invalid"
            self._admin_token = "sandbox-fake-not-a-token"
            self._admin_email = None
            self._admin_password = None
            self._jwt = None
            self._http = _NoNetwork()
            self._store_path = store_path

        # -- helpers ---------------------------------------------------------

        def _err(self, status: int, path: str, detail: str) -> Exception:
            log_line(MEDUSA_LOG, {"status": status, "path": path, "detail": detail})
            return api_error_cls(status, path, json.dumps({"message": detail}))

        def _read(self) -> dict[str, Any]:
            data = read_json(self._store_path, None)
            if not isinstance(data, dict):
                raise self._err(503, "/", "sandbox store missing — run seed.py")
            return data

        def _mutate(self, fn: Callable[[dict[str, Any]], Any]) -> Any:
            box: dict[str, Any] = {}

            def _apply(data: Any) -> Any:
                if not isinstance(data, dict):
                    raise self._err(503, "/", "sandbox store missing — run seed.py")
                box["out"] = fn(data)
                return data

            locked_update(self._store_path, _apply)
            return box.get("out")

        @staticmethod
        def _find(data: dict[str, Any], order_id: str) -> dict[str, Any] | None:
            return next((o for o in data.get("orders", []) if o.get("id") == order_id), None)

        @staticmethod
        def _page(items: list[dict[str, Any]], params: dict[str, Any] | None, key: str) -> dict[str, Any]:
            params = params or {}
            gte = params.get("created_at[$gte]")
            if gte:
                items = [o for o in items if str(o.get("created_at") or "") >= str(gte).replace("+00:00", "Z")]
            items = sorted(items, key=lambda o: str(o.get("created_at") or ""), reverse=True)
            limit = int(params.get("limit") or 50)
            offset = int(params.get("offset") or 0)
            return {key: items[offset:offset + limit], "count": len(items), "offset": offset, "limit": limit}

        # -- the emulated REST surface ----------------------------------------

        async def _do_request(
            self,
            method: str,
            path: str,
            *,
            params: dict[str, Any] | None = None,
            json: dict[str, Any] | None = None,  # noqa: A002 — mirrors the real signature
        ) -> dict[str, Any]:
            result = self._route(method.upper(), path, params, json)
            log_line(MEDUSA_LOG, {"method": method.upper(), "path": path,
                                  "params": {k: v for k, v in (params or {}).items() if k != "fields"},
                                  "body_keys": sorted((json or {}).keys()), "status": 200})
            return result

        def _route(self, method: str, path: str, params: dict[str, Any] | None,
                   body: dict[str, Any] | None) -> dict[str, Any]:
            body = body or {}
            m = re.fullmatch
            if method == "GET" and path == "/admin/orders":
                data = self._read()
                return self._page([o for o in data["orders"] if o.get("status") != "draft"], params, "orders")
            if method == "GET" and path == "/admin/draft-orders":
                data = self._read()
                return self._page([o for o in data["orders"] if o.get("status") == "draft"], params, "draft_orders")
            if hit := m(r"/admin/orders/([^/]+)", path):
                oid = hit.group(1)
                if method == "GET":
                    order = self._find(self._read(), oid)
                    if order is None:
                        raise self._err(404, path, f"Order id {oid} not found")
                    return {"order": order}  # Medusa v2 also serves drafts here (status="draft")
                if method == "POST":
                    return {"order": self._update_metadata(oid, body, path, drafts_only=False)}
            if method == "GET" and (hit := m(r"/admin/draft-orders/([^/]+)", path)):
                order = self._find(self._read(), hit.group(1))
                if order is None or order.get("status") != "draft":
                    raise self._err(404, path, f"Draft order id {hit.group(1)} not found")
                return {"draft_order": order}
            if method == "POST" and (hit := m(r"/admin/draft-orders/([^/]+)", path)):
                return {"draft_order": self._update_metadata(hit.group(1), body, path, drafts_only=True)}
            if method == "DELETE" and (hit := m(r"/admin/draft-orders/([^/]+)", path)):
                return self._delete_draft(hit.group(1), path)
            if method == "POST" and (hit := m(r"/admin/draft-orders/([^/]+)/convert-to-order", path)):
                return {"order": self._convert(hit.group(1), path)}
            if method == "POST" and (hit := m(r"/admin/orders/([^/]+)/cancel", path)):
                return {"order": self._cancel(hit.group(1), path)}
            if method == "POST" and path == "/admin/payment-collections":
                return {"payment_collection": self._new_collection(body, path)}
            if method == "POST" and (hit := m(r"/admin/payment-collections/([^/]+)/mark-as-paid", path)):
                return {"payment_collection": self._mark_paid(hit.group(1), body, path)}
            if method == "POST" and (hit := m(r"/admin/payments/([^/]+)/refund", path)):
                return {"payment": self._refund(hit.group(1), body, path)}
            if method == "GET" and path == "/admin/promotions":
                promos = self._read().get("promotions", [])
                return {"promotions": promos, "count": len(promos), "offset": 0, "limit": len(promos)}
            if method == "GET" and (hit := m(r"/admin/promotions/([^/]+)", path)):
                promo = next((p for p in self._read().get("promotions", []) if p.get("id") == hit.group(1)), None)
                if promo is None:
                    raise self._err(404, path, "Promotion not found")
                return {"promotion": promo}
            if method == "GET" and path in ("/admin/campaigns", "/admin/product-tags", "/admin/customers"):
                key = path.rsplit("/", 1)[-1].replace("-", "_")
                return {key: [], "count": 0, "offset": 0, "limit": 0}
            raise self._err(501, path, f"sandbox fake Medusa: {method} {path} is not emulated")

        # -- mutations ---------------------------------------------------------

        def _update_metadata(self, oid: str, body: dict[str, Any], path: str, *, drafts_only: bool) -> dict[str, Any]:
            def fn(data: dict[str, Any]) -> dict[str, Any]:
                order = self._find(data, oid)
                if order is None or (drafts_only and order.get("status") != "draft"):
                    raise self._err(404, path, f"Order id {oid} not found")
                if "metadata" in body:
                    # The real client already merged: it sends the WHOLE metadata.
                    order["metadata"] = body["metadata"]
                order["updated_at"] = iso_z(now_ms())
                return order

            return self._mutate(fn)

        def _convert(self, oid: str, path: str) -> dict[str, Any]:
            def fn(data: dict[str, Any]) -> dict[str, Any]:
                order = self._find(data, oid)
                if order is None or order.get("status") != "draft":
                    raise self._err(404, path, f"Draft order id {oid} not found")
                order.update(status="pending", payment_status="not_paid", updated_at=iso_z(now_ms()))
                return order

            return self._mutate(fn)

        def _cancel(self, oid: str, path: str) -> dict[str, Any]:
            def fn(data: dict[str, Any]) -> dict[str, Any]:
                order = self._find(data, oid)
                if order is None:
                    raise self._err(404, path, f"Order id {oid} not found")
                if order.get("status") == "canceled":
                    raise self._err(422, path, "Order is already canceled")
                stamp = iso_z(now_ms())
                order.update(status="canceled", canceled_at=stamp, updated_at=stamp)
                return order

            return self._mutate(fn)

        def _delete_draft(self, oid: str, path: str) -> dict[str, Any]:
            def fn(data: dict[str, Any]) -> dict[str, Any]:
                order = self._find(data, oid)
                if order is None or order.get("status") != "draft":
                    raise self._err(404, path, f"Draft order id {oid} not found")
                data["orders"] = [o for o in data["orders"] if o is not order]
                return {"id": oid, "object": "draft-order", "deleted": True}

            return self._mutate(fn)

        def _next(self, data: dict[str, Any], prefix: str) -> str:
            seq = data.setdefault("seq", {})
            seq["n"] = int(seq.get("n") or 100) + 1
            return f"{prefix}_sbx_{seq['n']}"

        def _new_collection(self, body: dict[str, Any], path: str) -> dict[str, Any]:
            def fn(data: dict[str, Any]) -> dict[str, Any]:
                order = self._find(data, str(body.get("order_id")))
                if order is None:
                    raise self._err(404, path, "Order not found")
                pc = {"id": self._next(data, "paycol"), "amount": int(body.get("amount") or 0),
                      "status": "not_paid", "currency_code": "cop", "payments": []}
                order.setdefault("payment_collections", []).append(pc)
                return pc

            return self._mutate(fn)

        def _mark_paid(self, pc_id: str, body: dict[str, Any], path: str) -> dict[str, Any]:
            def fn(data: dict[str, Any]) -> dict[str, Any]:
                order = self._find(data, str(body.get("order_id")))
                pc = next((p for p in (order or {}).get("payment_collections", []) if p.get("id") == pc_id), None)
                if order is None or pc is None:
                    raise self._err(404, path, "Payment collection not found")
                amount = int(pc.get("amount") or 0)
                pc["status"] = "paid"
                pc["payments"].append({"id": self._next(data, "pay"), "amount": amount,
                                       "provider_id": "pp_system_default", "captured_at": iso_z(now_ms()),
                                       "captures": [{"id": self._next(data, "capt"), "amount": amount}],
                                       "refunds": []})
                order.update(payment_status="captured", updated_at=iso_z(now_ms()))
                return pc

            return self._mutate(fn)

        def _refund(self, pay_id: str, body: dict[str, Any], path: str) -> dict[str, Any]:
            def fn(data: dict[str, Any]) -> dict[str, Any]:
                for order in data.get("orders", []):
                    for pc in order.get("payment_collections", []):
                        for pay in pc.get("payments", []):
                            if pay.get("id") != pay_id:
                                continue
                            pay.setdefault("refunds", []).append(
                                {"id": self._next(data, "ref"), "amount": int(body.get("amount") or 0),
                                 "note": body.get("note")})
                            captured = sum(int(c.get("amount") or 0) for c in pay.get("captures", []))
                            refunded = sum(int(r.get("amount") or 0) for r in pay["refunds"])
                            order["payment_status"] = "refunded" if refunded >= captured else "partially_refunded"
                            order["updated_at"] = iso_z(now_ms())
                            return pay
                raise self._err(404, path, "Payment not found")

            return self._mutate(fn)

    return SandboxMedusaClient


# ─────────────────────────────────────────────────────────────────────────────
# Temporal
# ─────────────────────────────────────────────────────────────────────────────


def _running() -> dict[str, Any]:
    data = read_json(TEMPORAL_STORE, {}) or {}
    return data.get("running") or {}


def _set_running(fn: Callable[[dict[str, Any]], None]) -> None:
    def _apply(data: Any) -> Any:
        data = data if isinstance(data, dict) else {}
        running = data.setdefault("running", {})
        fn(running)
        return data

    locked_update(TEMPORAL_STORE, _apply, default={})


@dataclass
class _Description:
    id: str
    status: Any
    workflow_type: str
    task_queue: str


class FakeWorkflowHandle:
    def __init__(self, client: FakeTemporalClient, workflow_id: str, run_id: str | None = None) -> None:
        self._client = client
        self.id = workflow_id
        self.run_id = run_id
        self.result_run_id = run_id
        self.first_execution_run_id = run_id

    def _not_found(self, op: str) -> Exception:
        log_line(TEMPORAL_LOG, {"op": op, "workflow_id": self.id, "result": "NOT_FOUND"})
        return self._client._rpc_error(
            f"sandbox: workflow not found for ID: {self.id}", self._client._status.NOT_FOUND, b""
        )

    async def describe(self, **_: Any) -> _Description:
        info = _running().get(self.id)
        if info is None:
            raise self._not_found("describe")
        log_line(TEMPORAL_LOG, {"op": "describe", "workflow_id": self.id, "result": "RUNNING"})
        return _Description(self.id, self._client._wes.RUNNING, info.get("type", "?"), info.get("task_queue", "?"))

    async def terminate(self, *args: Any, reason: str | None = None, **_: Any) -> None:
        if self.id not in _running():
            raise self._not_found("terminate")
        _set_running(lambda r: r.pop(self.id, None))
        log_line(TEMPORAL_LOG, {"op": "terminate", "workflow_id": self.id, "reason": reason})

    async def cancel(self, **_: Any) -> None:
        if self.id not in _running():
            raise self._not_found("cancel")
        _set_running(lambda r: r.pop(self.id, None))
        log_line(TEMPORAL_LOG, {"op": "cancel", "workflow_id": self.id})

    async def signal(self, signal: Any, arg: Any = None, *args: Any, **_: Any) -> None:
        name = getattr(signal, "__name__", signal)
        if self.id not in _running():
            raise self._not_found(f"signal:{name}")
        log_line(TEMPORAL_LOG, {"op": "signal", "workflow_id": self.id, "signal": name, "arg": arg})

    async def query(self, query: Any, *args: Any, **_: Any) -> Any:
        raise self._not_found(f"query:{getattr(query, '__name__', query)}")

    async def result(self, **_: Any) -> Any:
        raise self._not_found("result")


class FakeTemporalClient:
    """Enough of ``temporalio.client.Client`` for the API process."""

    def __init__(self) -> None:
        from temporalio.client import WorkflowExecutionStatus
        from temporalio.exceptions import WorkflowAlreadyStartedError
        from temporalio.service import RPCError, RPCStatusCode

        self._wes = WorkflowExecutionStatus
        self._already = WorkflowAlreadyStartedError
        self._rpc_error = RPCError
        self._status = RPCStatusCode
        self.namespace = "sandbox"
        self.identity = "sandbox-fake-temporal"

    def get_workflow_handle(self, workflow_id: str, *, run_id: str | None = None, **_: Any) -> FakeWorkflowHandle:
        return FakeWorkflowHandle(self, workflow_id, run_id)

    def get_workflow_handle_for(self, _workflow: Any, workflow_id: str, **kwargs: Any) -> FakeWorkflowHandle:
        return self.get_workflow_handle(workflow_id, **kwargs)

    async def start_workflow(self, workflow: Any, arg: Any = None, *args: Any, id: str, task_queue: str,  # noqa: A002
                             **kwargs: Any) -> FakeWorkflowHandle:
        name = getattr(workflow, "__name__", str(workflow))
        if id in _running():
            log_line(TEMPORAL_LOG, {"op": "start_workflow", "workflow": name, "workflow_id": id,
                                    "result": "ALREADY_STARTED"})
            raise self._already(id, name)
        start_delay = kwargs.get("start_delay")
        _set_running(lambda r: r.__setitem__(id, {"type": name, "task_queue": task_queue}))
        log_line(TEMPORAL_LOG, {"op": "start_workflow", "workflow": name, "workflow_id": id,
                                "task_queue": task_queue, "arg": arg,
                                "start_delay_s": start_delay.total_seconds() if start_delay else None})
        return FakeWorkflowHandle(self, id, run_id=f"sbx-run-{uuid.uuid4().hex[:8]}")

    async def execute_workflow(self, workflow: Any, arg: Any = None, *args: Any, id: str, task_queue: str,  # noqa: A002
                               **kwargs: Any) -> Any:
        await self.start_workflow(workflow, arg, id=id, task_queue=task_queue, **kwargs)
        raise RuntimeError("sandbox: no Temporal workers — execute_workflow cannot return a result")

    async def signal_with_start_workflow(self, workflow: Any, arg: Any = None, *args: Any, id: str,  # noqa: A002
                                         task_queue: str, start_signal: str | None = None,
                                         start_signal_args: Any = None, **kwargs: Any) -> FakeWorkflowHandle:
        name = getattr(workflow, "__name__", str(workflow))
        _set_running(lambda r: r.setdefault(id, {"type": name, "task_queue": task_queue}))
        log_line(TEMPORAL_LOG, {"op": "signal_with_start", "workflow": name, "workflow_id": id,
                                "signal": start_signal, "signal_args": start_signal_args, "arg": arg})
        return FakeWorkflowHandle(self, id)

    def list_workflows(self, *args: Any, **kwargs: Any) -> Any:
        log_line(TEMPORAL_LOG, {"op": "list_workflows", "query": args[0] if args else kwargs.get("query")})

        async def _empty() -> Any:
            if False:  # pragma: no cover — async generator with no items
                yield None

        return _empty()

    async def count_workflows(self, *args: Any, **kwargs: Any) -> Any:
        log_line(TEMPORAL_LOG, {"op": "count_workflows"})
        return type("Count", (), {"count": 0, "groups": []})()

    def __getattr__(self, name: str) -> Any:
        log_line(TEMPORAL_LOG, {"op": name, "result": "NOT_EMULATED"})
        raise RuntimeError(f"sandbox: Temporal client operation {name!r} is not emulated (no real Temporal here)")


_FAKE_TEMPORAL: FakeTemporalClient | None = None


async def fake_get_temporal_client() -> FakeTemporalClient:
    """Drop-in for ``src.platform.temporal.client.get_temporal_client``."""
    global _FAKE_TEMPORAL
    if _FAKE_TEMPORAL is None:
        _FAKE_TEMPORAL = FakeTemporalClient()
    return _FAKE_TEMPORAL


# ─────────────────────────────────────────────────────────────────────────────
# WhatsApp: repo FakeSend + recorder
# ─────────────────────────────────────────────────────────────────────────────


def install_whatsapp_recorder(wa_client: Any, wa_dtos: Any) -> None:
    """Record every would-be WhatsApp send to ``sent.log``.

    ``_post_json`` is the choke point of every ``send_*`` in
    ``src.platform.whatsapp.client``. The ORIGINAL function still runs: with
    ``WHATSAPP_ACCESS_TOKEN`` empty it takes the repo's own FakeSend branch
    (logs "FakeSend (no token configured)" and returns ``ok=True``) — no HTTP.
    The only change: the repo returns the constant id ``fake-<label>``; here each
    send gets a unique ``wamid.SBX…`` (Meta ids are unique; constant ids collide
    in ``outbound_text_index`` and in the app's message keys).
    """
    import asyncio
    import os

    original_post = wa_client._post_json
    original_send_message = wa_client.send_message
    original_upload = wa_client.upload_media
    original_typing = wa_client.send_typing_indicator
    #: Optional simulated Meta latency per send (seconds) — to test "sending…"
    #: states in the app or races; default 0 (instant, like the repo FakeSend).
    delay_s = float(os.environ.get("SANDBOX_SEND_DELAY_S") or 0)

    async def recording_post_json(phone_number_id: str, data: dict[str, Any], label: str) -> Any:
        if wa_client.WHATSAPP_ACCESS_TOKEN:
            raise RuntimeError("sandbox: refusing to send — WHATSAPP_ACCESS_TOKEN is set")
        if delay_s > 0:
            await asyncio.sleep(delay_s)
        result = await original_post(phone_number_id, data, label)  # repo FakeSend branch
        if getattr(result, "ok", False) and str(result.wa_message_id or "").startswith("fake-"):
            result = wa_dtos.OutboundResult(
                wa_message_id=f"wamid.SBX.{label}.{uuid.uuid4().hex[:12]}", ok=True, error=None
            )
        log_line(SENT_LOG, {"via": "whatsapp.client._post_json (repo FakeSend, no token)", "label": label,
                            "to": data.get("to"), "type": data.get("type"), "phone_number_id": phone_number_id,
                            "wa_message_id": result.wa_message_id, "payload": data})
        return result

    async def recording_send_message(phone_number_id: str, to: str, text: str) -> None:
        await original_send_message(phone_number_id, to, text)  # repo "Fake Send" branch
        log_line(SENT_LOG, {"via": "whatsapp.client.send_message (legacy, repo Fake Send)", "label": "text",
                            "to": to, "type": "text", "phone_number_id": phone_number_id,
                            "payload": {"text": {"body": text}}})

    async def recording_upload(phone_number_id: str, content: bytes, mime_type: str) -> str:
        media_id = await original_upload(phone_number_id, content, mime_type)  # repo FakeUploadMedia branch
        log_line(SENT_LOG, {"via": "whatsapp.client.upload_media (repo FakeUploadMedia)", "label": "upload",
                            "mime": mime_type, "bytes": len(content), "media_id": media_id})
        return media_id

    async def recording_typing(phone_number_id: str, message_id: str) -> None:
        await original_typing(phone_number_id, message_id)  # no token → returns immediately
        log_line(SENT_LOG, {"via": "whatsapp.client.send_typing_indicator (no-op without token)",
                            "label": "typing", "message_id": message_id})

    wa_client._post_json = recording_post_json
    wa_client.send_message = recording_send_message
    wa_client.upload_media = recording_upload
    wa_client.send_typing_indicator = recording_typing

