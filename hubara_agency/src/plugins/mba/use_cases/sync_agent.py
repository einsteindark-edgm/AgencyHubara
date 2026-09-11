"""D2.2 — ``SyncAgent``: lleva la configuración autorada de un agente a Meta
Business Agent. ``plan`` es de solo lectura (GET de todo + diff puro);
``apply`` ejecuta el plan en orden y deja el estado en el vault.

Guardas (nada se escribe en Meta):
* ``mba_disabled`` — la flag ``MBA_STANDBY_ENABLED`` está apagada: el plano de
  configuración sigue la misma regla que todo el plugin (nada cambia con MBA off).
* ``agent_unknown`` / ``sync_in_progress`` / ``remote_unavailable`` (Meta no
  respondió al leer: NO se diffea contra "vacío") / ``blocked`` (placeholders,
  problemas, sin entity_id, sin API key) / ``plan_changed`` (el operador
  confirmó un plan con otro fingerprint) / ``nothing_to_do``.

Ejecución: un ítem ``rejected`` por Meta se anota y se sigue (``partial``);
``unavailable`` / ``ambiguous`` / ``not_configured`` cortan el run
(``aborted``): lo ya creado queda registrado con su id, así el próximo run no
lo duplica. ``rollout`` y ``ai_audience`` nunca viajan (lo garantiza el dominio).

Solo se registran en ``ids`` (= borrable por el sync) los ítems que ESTE sync
creó; actualizar un ítem ajeno que coincide por clave no lo adopta. En ``sent``
va únicamente lo que el diff lee: el hash de ``never_say_phrases`` (write-only)
y la huella de la API key del connector (nunca la key ni su sha256 plano).
Un ``replace`` de UI skill cuyo create falla tras el delete se reporta como
"borrada, no recreada" y saca el id del vault (el próximo plan la crea).

El lock por agente es por PROCESO (``asyncio.Lock``): alcanza con el único
uvicorn del API; con varios workers habría que llevarlo al vault.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from src.plugins.mba.adapters.meta_admin import MbaAdminError, MbaAdminPort
from src.plugins.mba.adapters.sync_state import SyncStateStore
from src.plugins.mba.domain.config import MbaConfigDTO
from src.plugins.mba.domain.sync import (
    RemoteState,
    SyncOp,
    SyncPlan,
    api_key_fingerprint,
    body_hash,
    build_plan,
)

__all__ = ["SyncAgent", "SyncOutcome"]

_ABORT_KINDS = ("unavailable", "ambiguous", "not_configured")
_LOCKS: dict[str, asyncio.Lock] = {}


@dataclass(frozen=True)
class SyncOutcome:
    agent_id: str
    applied: bool
    reason: str
    status: str = ""  # ok | partial | aborted | ""
    results: list[dict[str, Any]] = field(default_factory=list)
    plan: dict[str, Any] | None = None
    blocked: tuple[str, ...] = ()
    error: dict[str, Any] | None = None
    state: dict[str, Any] = field(default_factory=dict)


def _err(exc: MbaAdminError) -> dict[str, Any]:
    return {"kind": exc.kind, "detail": exc.detail, "status": exc.status}


class SyncAgent:
    def __init__(
        self,
        *,
        admin: MbaAdminPort,
        state_store: SyncStateStore,
        load_config: Callable[[str], MbaConfigDTO | None],
        api_key: Callable[[], str],
        is_enabled: Callable[[], bool],
        now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        self._admin = admin
        self._store = state_store
        self._load = load_config
        self._api_key = api_key
        self._enabled = is_enabled
        self._now_ms = now_ms

    # -- lectura -------------------------------------------------------------

    async def fetch_remote(self, entity_id: str, channel: str) -> RemoteState:
        settings_list = await self._admin.get_settings(entity_id)
        settings = next(
            (s for s in settings_list if s.get("channel") == channel),
            settings_list[0] if settings_list else None,
        )
        try:
            business_info = await self._admin.get_business_info(entity_id)
        except MbaAdminError as exc:
            if exc.kind == "rejected" and exc.status == 404:
                business_info = {}
            else:
                raise
        connectors = await self._admin.list_connectors(entity_id)
        tools: dict[str, tuple[dict[str, Any], ...]] = {}
        for con in connectors:
            cid = con.get("id")
            if cid is not None:
                tools[str(cid)] = tuple(
                    await self._admin.list_connector_tools(entity_id, str(cid))
                )
        return RemoteState(
            settings=settings,
            business_info=business_info,
            faqs=tuple(await self._admin.list_faqs(entity_id)),
            skills=tuple(await self._admin.list_skills(entity_id)),
            connectors=tuple(connectors),
            tools=tools,
            ui_skills=tuple(await self._admin.list_ui_skills(entity_id)),
        )

    async def _plan(
        self, cfg: MbaConfigDTO, state: dict[str, Any]
    ) -> tuple[SyncPlan, RemoteState]:
        remote = (
            await self.fetch_remote(cfg.entity_id, cfg.channel)
            if cfg.entity_id
            else RemoteState.empty()
        )
        plan = build_plan(
            cfg,
            remote,
            managed_ids=state.get("ids") or {},
            sent_hashes=state.get("sent") or {},
            api_key=self._api_key(),
        )
        return plan, remote

    async def plan(self, agent_id: str) -> SyncPlan | None:
        """Solo lectura. Levanta ``MbaAdminError`` si Meta no responde."""
        cfg = self._load(agent_id)
        if cfg is None:
            return None
        plan, _ = await self._plan(cfg, self._store.read(agent_id))
        return plan

    # -- escritura -----------------------------------------------------------

    async def apply(
        self, agent_id: str, *, fingerprint: str | None = None
    ) -> SyncOutcome:
        if not self._enabled():
            return SyncOutcome(agent_id, False, "mba_disabled")
        cfg = self._load(agent_id)
        if cfg is None:
            return SyncOutcome(agent_id, False, "agent_unknown")
        lock = _LOCKS.setdefault(agent_id, asyncio.Lock())
        if lock.locked():
            return SyncOutcome(agent_id, False, "sync_in_progress")
        async with lock:
            return await self._apply_locked(agent_id, cfg, fingerprint)

    async def _apply_locked(
        self, agent_id: str, cfg: MbaConfigDTO, fingerprint: str | None
    ) -> SyncOutcome:
        state = self._store.read(agent_id)
        try:
            plan, remote = await self._plan(cfg, state)
        except MbaAdminError as exc:
            return SyncOutcome(
                agent_id, False, "remote_unavailable", error=_err(exc), state=state
            )
        summary = plan.summary()
        if plan.blocked:
            attempt = {
                "at_ms": self._now_ms(),
                "reason": "blocked",
                "blocked": list(plan.blocked),
                "fingerprint": plan.fingerprint,
            }
            state = self._store.update(
                agent_id, lambda st: {**st, "last_attempt": attempt}
            )
            return SyncOutcome(
                agent_id,
                False,
                "blocked",
                plan=summary,
                blocked=plan.blocked,
                state=state,
            )
        if fingerprint is not None and fingerprint != plan.fingerprint:
            return SyncOutcome(
                agent_id, False, "plan_changed", plan=summary, state=state
            )
        if not plan.changes:
            attempt = {
                "at_ms": self._now_ms(),
                "reason": "nothing_to_do",
                "fingerprint": plan.fingerprint,
            }
            state = self._store.update(
                agent_id, lambda st: {**st, "last_attempt": attempt}
            )
            return SyncOutcome(
                agent_id, False, "nothing_to_do", status="ok", plan=summary, state=state
            )

        ids: dict[str, dict[str, str]] = {
            k: dict(v) for k, v in (state.get("ids") or {}).items()
        }
        sent: dict[str, dict[str, str]] = {
            k: dict(v) for k, v in (state.get("sent") or {}).items()
        }
        settings_agent_id = (
            str(remote.settings.get("agent_id"))
            if remote.settings and remote.settings.get("agent_id")
            else None
        )
        entity_id = str(cfg.entity_id)
        results: list[dict[str, Any]] = []
        aborted = False
        for op in plan.changes:
            row: dict[str, Any] = {
                "section": op.section,
                "label": op.label,
                "action": op.action,
                "ok": False,
                "remote_id": op.remote_id,
                "error": None,
                "skipped": None,
            }
            if aborted:
                row["skipped"] = "aborted"
                results.append(row)
                continue
            connector_id = op.connector_remote_id
            if op.section == "connector_tools" and connector_id is None:
                connector_id = ids.get("connector", {}).get(op.connector_label or "")
                if connector_id is None:
                    row["skipped"] = "connector_missing"
                    results.append(row)
                    continue
            try:
                remote_id = await self._execute(
                    entity_id,
                    op,
                    connector_id,
                    settings_agent_id,
                    on_deleted=lambda: ids.get(op.section, {}).pop(op.label, None),
                )
            except MbaAdminError as exc:
                row["error"] = _err(exc)
                if exc.kind in _ABORT_KINDS:
                    aborted = True
            else:
                row["ok"] = True
                row["remote_id"] = remote_id
                self._record(ids, sent, op, remote_id, self._api_key())
            results.append(row)

        failed = [r for r in results if not r["ok"] and r["skipped"] is None]
        status = "aborted" if aborted else ("partial" if failed else "ok")
        last_apply = {
            "at_ms": self._now_ms(),
            "status": status,
            "fingerprint": plan.fingerprint,
            "counts": {
                "changes": len(plan.changes),
                "ok": sum(1 for r in results if r["ok"]),
                "failed": len(failed),
                "skipped": sum(1 for r in results if r["skipped"]),
            },
            "results": results,
        }

        def _merge(fresh: dict[str, Any]) -> dict[str, Any]:
            # Solo NUESTRAS claves sobre el estado fresco: lo que el rollout
            # (D2.3) haya escrito mientras esperábamos a Meta sobrevive.
            fresh = {
                **fresh,
                "ids": ids,
                "sent": sent,
                "entity_id": entity_id,
                "last_apply": last_apply,
            }
            fresh.pop("last_attempt", None)
            return fresh

        state = self._store.update(agent_id, _merge)
        return SyncOutcome(
            agent_id,
            True,
            "applied",
            status=status,
            results=results,
            plan=summary,
            state=state,
        )

    async def _execute(
        self,
        entity_id: str,
        op: SyncOp,
        connector_id: str | None,
        settings_agent_id: str | None,
        *,
        on_deleted: Callable[[], Any],
    ) -> str | None:
        a = self._admin
        s, action, body, rid = op.section, op.action, op.body, op.remote_id
        if s == "business_info":
            await a.put_business_info(entity_id, body)
            return None
        if s == "settings":
            await a.put_settings(entity_id, body, agent_id=settings_agent_id)
            return None
        if s == "faqs":
            if action == "create":
                return _id(await a.create_faq(entity_id, body))
            if action == "update":
                return _id(await a.update_faq(entity_id, str(rid), body)) or rid
            await a.delete_faq(entity_id, str(rid))
            return rid
        if s == "skills":
            if action == "create":
                return _id(await a.create_skill(entity_id, body))
            if action == "update":
                return _id(await a.update_skill(entity_id, str(rid), body)) or rid
            await a.delete_skill(entity_id, str(rid))
            return rid
        if s == "connector":
            if action == "create":
                return _id(await a.create_connector(entity_id, body))
            if action == "update":
                return _id(await a.update_connector(entity_id, str(rid), body)) or rid
            await a.delete_connector(entity_id, str(rid))
            return rid
        if s == "connector_tools":
            cid = str(connector_id)
            if action == "create":
                return _id(await a.create_connector_tool(entity_id, cid, body))
            if action == "update":
                return (
                    _id(await a.update_connector_tool(entity_id, cid, str(rid), body))
                    or rid
                )
            await a.delete_connector_tool(entity_id, cid, str(rid))
            return rid
        if s == "ui_skills":
            if action == "create":
                return _id(await a.create_ui_skill(entity_id, body))
            if action == "update":
                return _id(await a.update_ui_skill(entity_id, str(rid), body)) or rid
            if action == "replace":
                await a.delete_ui_skill(entity_id, str(rid))
                on_deleted()
                try:
                    return _id(await a.create_ui_skill(entity_id, body))
                except MbaAdminError as exc:
                    raise MbaAdminError(
                        exc.kind,
                        status=exc.status,
                        detail=f"borrada, no recreada: {exc.detail}",
                        attempts=exc.attempts,
                    )
            await a.delete_ui_skill(entity_id, str(rid))
            return rid
        raise MbaAdminError("rejected", detail=f"sección desconocida: {s}")

    @staticmethod
    def _record(
        ids: dict[str, dict[str, str]],
        sent: dict[str, dict[str, str]],
        op: SyncOp,
        remote_id: str | None,
        api_key: str,
    ) -> None:
        if op.action == "delete":
            ids.get(op.section, {}).pop(op.label, None)
            return
        if op.action in ("create", "replace") and remote_id is not None:
            ids.setdefault(op.section, {})[op.label] = str(remote_id)
        if op.section == "settings":
            phrases = op.body.get("never_say_phrases")
            if phrases is not None:
                sent.setdefault("settings", {})["never_say_phrases"] = body_hash(
                    phrases
                )
        if op.section == "connector" and api_key:
            sent.setdefault("connector_key", {})[op.label] = api_key_fingerprint(
                api_key
            )


def _id(payload: dict[str, Any]) -> str | None:
    value = payload.get("id") if isinstance(payload, dict) else None
    return str(value) if value is not None else None


def outcome_dict(outcome: SyncOutcome) -> dict[str, Any]:
    return asdict(outcome)
