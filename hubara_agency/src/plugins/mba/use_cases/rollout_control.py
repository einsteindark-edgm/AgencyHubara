"""D2.3 — ``RolloutControl``: allowlist, audiencia y ``rollout.enabled`` de
Meta Business Agent desde la tab.

Cada escritura pasa por la política pura (``domain/rollout_policy``) con
hechos leídos de Meta (settings del canal, allowlist, connector) y del vault
(último sync de D2.2). Las escrituras a settings son PUT PARCIALES con UN
solo campo (``ai_audience`` o ``rollout``): nunca se reenvía el resto.

Guardas comunes: ``agent_unknown`` / ``entity_id_missing`` / Meta caída →
``unavailable`` (nada se escribe; el status LEVANTA para que el endpoint
responda 503 y la tab no muestre un "apagado" inventado).

Kill switch: apagar el rollout y quitar teléfonos NUNCA se bloquean (ni por
la flag de Hubara, ni por confirmación). Todo cambio queda en
``rollout_history`` del estado de sync (últimos 100).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from src.plugins.mba.adapters.meta_admin import MbaAdminError, MbaAdminPort
from src.plugins.mba.adapters.sync_state import SyncStateStore
from src.plugins.mba.domain.config import MbaConfigDTO
from src.plugins.mba.domain.rollout_policy import (
    RolloutFacts,
    can_add_phone,
    can_enable,
    can_set_audience,
    readiness,
)

__all__ = ["RolloutControl", "RolloutOutcome", "RolloutStatus"]

_HISTORY_CAP = 100


@dataclass(frozen=True)
class RolloutStatus:
    agent_id: str
    entity_id: str | None
    rollout_enabled: bool | None
    ai_audience: str | None
    allowlist: list[dict[str, Any]]
    checks: list[dict[str, Any]]
    can_enable: bool
    everyone_allowed: bool
    last_sync: dict[str, Any] | None
    history: list[dict[str, Any]]


@dataclass(frozen=True)
class RolloutOutcome:
    agent_id: str
    applied: bool
    reason: str
    blocked: tuple[str, ...] = ()
    error: dict[str, Any] | None = None
    checks: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class _Remote:
    settings: dict[str, Any] | None
    allowlist: list[dict[str, Any]]
    connector_status: str | None


def _err(exc: MbaAdminError) -> dict[str, Any]:
    return {"kind": exc.kind, "detail": exc.detail, "status": exc.status}


class RolloutControl:
    def __init__(
        self,
        *,
        admin: MbaAdminPort,
        state_store: SyncStateStore,
        load_config: Callable[[str], MbaConfigDTO | None],
        is_enabled: Callable[[], bool],
        hubara_allowed: Callable[[str], bool],
        everyone_allowed: Callable[[], bool],
        now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        self._admin = admin
        self._store = state_store
        self._load = load_config
        self._enabled = is_enabled
        self._hubara_allowed = hubara_allowed
        self._everyone = everyone_allowed
        self._now_ms = now_ms

    # -- lectura -------------------------------------------------------------

    async def _remote(self, cfg: MbaConfigDTO) -> _Remote:
        entity_id = str(cfg.entity_id)
        settings_list = await self._admin.get_settings(entity_id)
        settings = next((s for s in settings_list if s.get("channel") == cfg.channel), settings_list[0] if settings_list else None)
        allowlist = await self._admin.list_allowlist(entity_id)
        connector_status: str | None = None
        if cfg.connector is not None:
            for con in await self._admin.list_connectors(entity_id):
                if con.get("name") == cfg.connector.name:
                    cs = con.get("connection_status")
                    connector_status = str(cs.get("status")) if isinstance(cs, dict) and cs.get("status") else "UNKNOWN"
                    break
        return _Remote(settings=settings, allowlist=allowlist, connector_status=connector_status)

    @staticmethod
    def _last_sync_ok(state: dict[str, Any]) -> bool:
        last = state.get("last_apply") or {}
        attempt = state.get("last_attempt") or {}
        return last.get("status") == "ok" or attempt.get("reason") == "nothing_to_do"

    def _facts(self, state: dict[str, Any], remote: _Remote) -> RolloutFacts:
        settings = remote.settings or {}
        rollout = settings.get("rollout") if isinstance(settings.get("rollout"), dict) else {}
        return RolloutFacts(
            flag_enabled=self._enabled(),
            rollout_enabled=bool(rollout.get("enabled")),
            ai_audience=settings.get("ai_audience"),
            allowlist=tuple((str(e.get("id")), str(e.get("consumer_phone_number") or "")) for e in remote.allowlist if e.get("id") is not None),
            hubara_allowed=self._hubara_allowed,
            last_sync_ok=self._last_sync_ok(state),
            connector_status=remote.connector_status,
            everyone_knob=self._everyone(),
        )

    async def status(self, agent_id: str) -> RolloutStatus | None:
        cfg = self._load(agent_id)
        if cfg is None:
            return None
        state = self._store.read(agent_id)
        remote = await self._remote(cfg) if cfg.entity_id else _Remote(None, [], None)
        facts = self._facts(state, remote)
        checks = [c.__dict__ for c in readiness(facts)]
        settings = remote.settings or {}
        rollout = settings.get("rollout") if isinstance(settings.get("rollout"), dict) else None
        return RolloutStatus(
            agent_id=agent_id,
            entity_id=cfg.entity_id,
            rollout_enabled=bool(rollout.get("enabled")) if rollout is not None else None,
            ai_audience=settings.get("ai_audience"),
            allowlist=[{"id": eid, "phone": phone, "in_hubara": self._hubara_allowed(phone)} for eid, phone in facts.allowlist],
            checks=checks,
            can_enable=bool(cfg.entity_id) and all(c["ok"] for c in checks),
            everyone_allowed=self._everyone(),
            last_sync=state.get("last_apply"),
            history=list(state.get("rollout_history") or [])[-20:],
        )

    # -- escritura -----------------------------------------------------------

    async def _prepare(self, agent_id: str) -> tuple[MbaConfigDTO, dict[str, Any], _Remote, RolloutFacts] | RolloutOutcome:
        cfg = self._load(agent_id)
        if cfg is None:
            return RolloutOutcome(agent_id, False, "agent_unknown")
        if not cfg.entity_id:
            return RolloutOutcome(agent_id, False, "entity_id_missing")
        state = self._store.read(agent_id)
        try:
            remote = await self._remote(cfg)
        except MbaAdminError as exc:
            return RolloutOutcome(agent_id, False, "remote_unavailable", error=_err(exc))
        return cfg, state, remote, self._facts(state, remote)

    def _record(self, agent_id: str, state: dict[str, Any], action: str, value: str, ok: bool, error: dict[str, Any] | None = None) -> None:
        entry: dict[str, Any] = {"at_ms": self._now_ms(), "action": action, "value": value, "ok": ok}
        if error is not None:
            entry["error"] = error
        history = list(state.get("rollout_history") or [])
        history.append(entry)
        state["rollout_history"] = history[-_HISTORY_CAP:]
        self._store.write(agent_id, state)

    async def _write(self, agent_id: str, state: dict[str, Any], action: str, value: str, call: Callable[[], Any]) -> RolloutOutcome:
        try:
            await call()
        except MbaAdminError as exc:
            self._record(agent_id, state, action, value, False, _err(exc))
            return RolloutOutcome(agent_id, False, exc.kind, error=_err(exc))
        self._record(agent_id, state, action, value, True)
        return RolloutOutcome(agent_id, True, "applied")

    async def add_phone(self, agent_id: str, phone: str) -> RolloutOutcome:
        prep = await self._prepare(agent_id)
        if isinstance(prep, RolloutOutcome):
            return prep
        cfg, state, _, facts = prep
        reason = can_add_phone(phone, facts)
        if reason is not None:
            return RolloutOutcome(agent_id, False, reason)
        entity_id = str(cfg.entity_id)
        return await self._write(agent_id, state, "allowlist_add", phone, lambda: self._admin.add_allowlist(entity_id, phone))

    async def remove_phone(self, agent_id: str, entry_id: str) -> RolloutOutcome:
        cfg = self._load(agent_id)
        if cfg is None:
            return RolloutOutcome(agent_id, False, "agent_unknown")
        if not cfg.entity_id:
            return RolloutOutcome(agent_id, False, "entity_id_missing")
        state = self._store.read(agent_id)
        entity_id = str(cfg.entity_id)
        return await self._write(agent_id, state, "allowlist_remove", entry_id, lambda: self._admin.remove_allowlist(entity_id, entry_id))

    async def set_audience(self, agent_id: str, audience: str, *, confirm: bool) -> RolloutOutcome:
        prep = await self._prepare(agent_id)
        if isinstance(prep, RolloutOutcome):
            return prep
        cfg, state, remote, facts = prep
        reason = can_set_audience(audience, confirm=confirm, facts=facts)
        if reason is not None:
            return RolloutOutcome(agent_id, False, reason)
        entity_id = str(cfg.entity_id)
        settings_agent_id = _settings_agent_id(remote)
        return await self._write(
            agent_id, state, "ai_audience", audience,
            lambda: self._admin.put_settings(entity_id, {"ai_audience": audience}, agent_id=settings_agent_id),
        )

    async def set_enabled(self, agent_id: str, enabled: bool, *, confirm: bool) -> RolloutOutcome:
        prep = await self._prepare(agent_id)
        if isinstance(prep, RolloutOutcome):
            return prep
        cfg, state, remote, facts = prep
        checks = [c.__dict__ for c in readiness(facts)]
        if enabled:
            blocked = can_enable(facts)
            if blocked:
                return RolloutOutcome(agent_id, False, "not_ready", blocked=blocked, checks=checks)
            if not confirm:
                return RolloutOutcome(agent_id, False, "confirmation_required", checks=checks)
        entity_id = str(cfg.entity_id)
        settings_agent_id = _settings_agent_id(remote)
        return await self._write(
            agent_id, state, "rollout_enabled", "true" if enabled else "false",
            lambda: self._admin.put_settings(entity_id, {"rollout": {"enabled": enabled}}, agent_id=settings_agent_id),
        )


def _settings_agent_id(remote: _Remote) -> str | None:
    value = (remote.settings or {}).get("agent_id")
    return str(value) if value else None
