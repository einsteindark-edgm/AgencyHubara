"""D2.2 — diff PURO: configuración autorada (los ``requests[]`` del preview)
vs. estado remoto de Meta Business Agent → ``SyncPlan``.

Sin I/O: recibe el ``MbaConfigDTO`` (D0), el estado remoto ya leído (D2.1) y
el estado de sync anterior (qué ids creamos, qué hash enviamos), y devuelve
la lista ordenada de operaciones con el cuerpo literal de cada una.

Reglas:
* Ítems por clave natural — ``question`` (FAQ), ``title`` (skill, UI skill),
  ``name`` (connector, tool). Faltante → ``create``; distinto → ``update``;
  igual → ``noop``. Singletons (business_info, settings) → ``update``/``noop``.
* Se borra SOLO lo que nosotros creamos (``managed_ids``) y ya no está en el
  workspace (``removed_from_workspace``); lo ajeno queda ``noop``/``not_managed``.
* ``rollout.enabled`` y ``ai_audience`` NUNCA viajan; la allowlist se lista
  como ``skip``/``d2.3``. Encender MBA es una decisión aparte (D2.3 / D4.5).
* ``never_say_phrases`` es write-only en Meta: se compara con el hash de lo
  último enviado (``sent_hashes``); ``PUT settings`` es parcial, así que el
  cuerpo lleva solo handoff / followup / never_say_phrases.
* Una UI skill cuyo ``component_type`` cambió se ``replace`` (delete + create):
  el PUT de Meta solo acepta title / status / instruction.
* ``business_info``: el preview omite los campos vacíos, pero el sync manda
  el bloque COMPLETO (vacíos como ``""``; ``contact_info`` solo con claves no
  nulas) para que vaciar un campo en el workspace también vacíe Meta. Se
  compara normalizando vacío ≡ ausente.
* Connector: Meta no devuelve la API key, así que el fingerprint (hash con
  prefijo, truncado) de la última key enviada decide si hay que reenviar el
  connector (``api_key_rotated``).
* Bloqueos (``blocked`` no vacío = no se aplica NADA): sin ``entity_id``,
  ``problems`` del preview (D0.1: skills fuera de límite, etc.), placeholders
  sin resolver (convención del ``agent.yaml``: ``<MAYUSCULAS_CON_GUION_BAJO>``,
  p.ej. ``<FLOW_ID>``; un ``<cliente>`` en el markdown de una skill NO lo es),
  sin API key del connector.
* Dos ítems remotos con la misma clave (Meta solo rechaza duplicados de FAQ y
  connector): el primero manda, el resto se lista ``duplicate_remote``.
* Nada secreto en ``fingerprint`` ni en ``summary()``: la API key del
  connector se enmascara.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping

from src.plugins.mba.domain.config import CONNECTOR_API_KEY_PLACEHOLDER, MbaConfigDTO
from src.sdk.runtime import is_placeholder

__all__ = [
    "MANAGED_SECTIONS",
    "RemoteState",
    "SyncOp",
    "SyncPlan",
    "api_key_fingerprint",
    "body_hash",
    "build_plan",
]

MANAGED_SECTIONS: tuple[str, ...] = (
    "business_info",
    "faqs",
    "skills",
    "connector",
    "connector_tools",
    "ui_skills",
    "settings",
)
_NATURAL_KEY = {
    "faqs": "question",
    "skills": "title",
    "connector": "name",
    "connector_tools": "name",
    "ui_skills": "title",
}
_COMPARE_FIELDS = {
    "faqs": ("question", "answer"),
    "skills": ("title", "description", "skill"),
    "connector": ("name", "description", "base_url", "auth_type"),
    "connector_tools": (
        "name",
        "description",
        "request_definition",
        "user_auth_required",
    ),
}
_UI_UPDATE_FIELDS = ("title", "status", "instruction")
_SETTINGS_FIELDS = ("handoff", "followup")
_NEVER_SEND = ("rollout", "ai_audience")
# `<VAR>` (mayúsculas) y `<host-publico>` (kebab): el guion evita el falso positivo `<cliente>`.
_PLACEHOLDER = re.compile(r"<[A-Z][A-Z0-9_]{2,}>|<[a-z]+(?:-[a-z]+)+>")
_BI_TEXT_FIELDS = (
    "business_description",
    "payment_method",
    "delivery_and_shipping",
    "return_policy",
    "purchase_info",
)
_BI_CONTACT_FIELDS = ("email", "hours_of_operation", "address")
_MASK = "***"


@dataclass(frozen=True)
class RemoteState:
    """Lo que Meta tiene hoy para el ``entity_id`` (leído con D2.1)."""

    settings: dict[str, Any] | None
    business_info: dict[str, Any]
    faqs: tuple[dict[str, Any], ...]
    skills: tuple[dict[str, Any], ...]
    connectors: tuple[dict[str, Any], ...]
    tools: Mapping[str, tuple[dict[str, Any], ...]]  # por id de connector
    ui_skills: tuple[dict[str, Any], ...]

    @classmethod
    def empty(cls) -> RemoteState:
        return cls(
            settings=None,
            business_info={},
            faqs=(),
            skills=(),
            connectors=(),
            tools={},
            ui_skills=(),
        )


@dataclass(frozen=True)
class SyncOp:
    section: str
    label: str
    action: str  # create | update | replace | delete | noop | skip
    body: dict[str, Any] = field(default_factory=dict)
    remote_id: str | None = None
    reason: str = ""
    connector_label: str | None = None
    connector_remote_id: str | None = None


@dataclass(frozen=True)
class SyncPlan:
    agent_id: str
    entity_id: str | None
    ops: tuple[SyncOp, ...]
    blocked: tuple[str, ...]
    fingerprint: str

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for op in self.ops:
            out[op.action] = out.get(op.action, 0) + 1
        return out

    @property
    def changes(self) -> tuple[SyncOp, ...]:
        return tuple(op for op in self.ops if op.action not in ("noop", "skip"))

    def summary(self) -> dict[str, Any]:
        """Serializable y sin secretos (para el endpoint y el estado de sync)."""
        return {
            "agent_id": self.agent_id,
            "entity_id": self.entity_id,
            "fingerprint": self.fingerprint,
            "blocked": list(self.blocked),
            "counts": self.counts,
            "ops": [asdict(_redacted(op)) for op in self.ops],
        }


def body_hash(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def api_key_fingerprint(api_key: str) -> str:
    """Huella de la key del connector para saber si cambió, sin guardar la
    key ni su sha256 plano: hash con prefijo propio, truncado."""
    return hashlib.sha256(
        b"mba-connector-api-key:" + api_key.encode("utf-8")
    ).hexdigest()[:16]


def _redacted(op: SyncOp) -> SyncOp:
    if op.section != "connector" or not op.body:
        return op
    body = json.loads(json.dumps(op.body))
    api_key = (
        body.get("auth_config", {}).get("api_key", {})
        if isinstance(body.get("auth_config"), dict)
        else {}
    )
    for where in ("headers", "query_params", "body_params"):
        for entry in api_key.get(where, []) or []:
            if isinstance(entry, dict) and "value" in entry:
                entry["value"] = _MASK
    return SyncOp(**{**asdict(op), "body": body})


def _same(a: Any, b: Any) -> bool:
    return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(
        b, sort_keys=True, ensure_ascii=False
    )


def _project(remote: Mapping[str, Any], body: Mapping[str, Any]) -> dict[str, Any]:
    """El remoto restringido a las claves que enviamos (Meta agrega ``id``,
    ``created_at``…, que no cuentan como diferencia)."""
    out: dict[str, Any] = {}
    for k, v in body.items():
        rv = remote.get(k)
        out[k] = _project(rv, v) if isinstance(v, dict) and isinstance(rv, dict) else rv
    return out


def _strings(obj: Any) -> Iterable[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v)


def _business_info_body(cfg: MbaConfigDTO) -> dict[str, Any]:
    bi = cfg.business_info
    body: dict[str, Any] = {f: (getattr(bi, f) or "") for f in _BI_TEXT_FIELDS}
    contact = {
        f: getattr(bi.contact_info, f)
        for f in _BI_CONTACT_FIELDS
        if getattr(bi.contact_info, f)
    }
    if contact:
        body["contact_info"] = contact
    return body


def _normalized_business_info(raw: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {f: (raw.get(f) or "") for f in _BI_TEXT_FIELDS}
    contact_raw = (
        raw.get("contact_info") if isinstance(raw.get("contact_info"), Mapping) else {}
    )
    contact = {f: contact_raw.get(f) for f in _BI_CONTACT_FIELDS if contact_raw.get(f)}
    if contact:
        out["contact_info"] = contact
    return out


def _requests_by_section(
    cfg: MbaConfigDTO,
) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    out: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for r in cfg.requests:
        out.setdefault(r.section, []).append((r.label, r.body))
    return out


def _blockers(
    cfg: MbaConfigDTO,
    by_section: Mapping[str, list[tuple[str, dict[str, Any]]]],
    api_key: str,
) -> tuple[str, ...]:
    blocked: list[str] = []
    if not cfg.entity_id:
        blocked.append("entity_id_missing")
    blocked.extend(f"problem:{p}" for p in cfg.problems)
    found: set[str] = set()
    for section in MANAGED_SECTIONS:
        for _, body in by_section.get(section, []):
            for s in _strings(body):
                found.update(
                    m
                    for m in _PLACEHOLDER.findall(s)
                    if m != CONNECTOR_API_KEY_PLACEHOLDER
                )
    blocked.extend(f"placeholder:{m}" for m in sorted(found))
    if cfg.connector is not None and (not api_key or is_placeholder(api_key)):
        blocked.append("connector_api_key_missing")
    return tuple(blocked)


def _resolve_api_key(body: dict[str, Any], api_key: str) -> dict[str, Any]:
    body = json.loads(json.dumps(body))
    api = (
        body.get("auth_config", {}).get("api_key", {})
        if isinstance(body.get("auth_config"), dict)
        else {}
    )
    for where in ("headers", "query_params", "body_params"):
        for entry in api.get(where, []) or []:
            if (
                isinstance(entry, dict)
                and entry.get("value") == CONNECTOR_API_KEY_PLACEHOLDER
            ):
                entry["value"] = api_key
    return body


def _collection(
    section: str,
    wanted: list[tuple[str, dict[str, Any]]],
    remote_items: Iterable[dict[str, Any]],
    managed: Mapping[str, str],
    *,
    connector_label: str | None = None,
    connector_remote_id: str | None = None,
) -> list[SyncOp]:
    key = _NATURAL_KEY[section]
    fields = _COMPARE_FIELDS[section]
    remote_by_key, duplicates = _index_remote(remote_items, key)
    extra = dict(
        connector_label=connector_label, connector_remote_id=connector_remote_id
    )
    ops: list[SyncOp] = []
    for label, body in wanted:
        remote = remote_by_key.pop(label, None)
        if remote is None:
            ops.append(SyncOp(section, label, "create", body, **extra))
            continue
        rid = str(remote.get("id")) if remote.get("id") is not None else None
        differs = any(
            not _same(body.get(f), remote.get(f)) for f in fields if f in body
        )
        ops.append(
            SyncOp(
                section,
                label,
                "update" if differs else "noop",
                body if differs else {},
                rid,
                "" if differs else "unchanged",
                **extra,
            )
        )
    managed_ids = {str(v) for v in managed.values()}
    for label, remote in remote_by_key.items():
        rid = str(remote.get("id")) if remote.get("id") is not None else None
        if rid is not None and rid in managed_ids:
            ops.append(
                SyncOp(
                    section, label, "delete", {}, rid, "removed_from_workspace", **extra
                )
            )
        else:
            ops.append(SyncOp(section, label, "noop", {}, rid, "not_managed", **extra))
    ops.extend(
        SyncOp(section, label, "noop", {}, rid, "duplicate_remote", **extra)
        for label, rid in duplicates
    )
    return ops


def _index_remote(
    remote_items: Iterable[dict[str, Any]], key: str
) -> tuple[dict[str, dict[str, Any]], list[tuple[str, str | None]]]:
    by_key: dict[str, dict[str, Any]] = {}
    duplicates: list[tuple[str, str | None]] = []
    for r in remote_items:
        if r.get(key) is None:
            continue
        label = str(r.get(key))
        if label in by_key:
            duplicates.append(
                (label, str(r.get("id")) if r.get("id") is not None else None)
            )
        else:
            by_key[label] = r
    return by_key, duplicates


def _ui_skills(
    wanted: list[tuple[str, dict[str, Any]]],
    remote_items: Iterable[dict[str, Any]],
    managed: Mapping[str, str],
) -> list[SyncOp]:
    remote_by_key, duplicates = _index_remote(remote_items, "title")
    ops: list[SyncOp] = []
    for label, body in wanted:
        remote = remote_by_key.pop(label, None)
        if remote is None:
            ops.append(SyncOp("ui_skills", label, "create", body))
            continue
        rid = str(remote.get("id")) if remote.get("id") is not None else None
        if not _same(body.get("component_type"), remote.get("component_type")):
            ops.append(
                SyncOp(
                    "ui_skills", label, "replace", body, rid, "component_type_changed"
                )
            )
            continue
        update = {f: body[f] for f in _UI_UPDATE_FIELDS if f in body}
        if any(not _same(update[f], remote.get(f)) for f in update):
            ops.append(SyncOp("ui_skills", label, "update", update, rid))
        else:
            ops.append(SyncOp("ui_skills", label, "noop", {}, rid, "unchanged"))
    managed_ids = {str(v) for v in managed.values()}
    for label, remote in remote_by_key.items():
        rid = str(remote.get("id")) if remote.get("id") is not None else None
        if rid is not None and rid in managed_ids:
            ops.append(
                SyncOp("ui_skills", label, "delete", {}, rid, "removed_from_workspace")
            )
        else:
            ops.append(SyncOp("ui_skills", label, "noop", {}, rid, "not_managed"))
    ops.extend(
        SyncOp("ui_skills", label, "noop", {}, rid, "duplicate_remote")
        for label, rid in duplicates
    )
    return ops


def build_plan(
    cfg: MbaConfigDTO,
    remote: RemoteState,
    *,
    managed_ids: Mapping[str, Mapping[str, str]],
    sent_hashes: Mapping[str, Mapping[str, str]],
    api_key: str,
) -> SyncPlan:
    by_section = _requests_by_section(cfg)
    blocked = _blockers(cfg, by_section, api_key)
    ops: list[SyncOp] = []

    # business_info (singleton): bloque completo, vacío ≡ ausente
    for label, _ in by_section.get("business_info", []):
        body = _business_info_body(cfg)
        if _same(
            _normalized_business_info(remote.business_info),
            _normalized_business_info(body),
        ):
            ops.append(SyncOp("business_info", label, "noop", reason="unchanged"))
        else:
            ops.append(SyncOp("business_info", label, "update", body))

    ops.extend(
        _collection(
            "faqs", by_section.get("faqs", []), remote.faqs, managed_ids.get("faqs", {})
        )
    )
    ops.extend(
        _collection(
            "skills",
            by_section.get("skills", []),
            remote.skills,
            managed_ids.get("skills", {}),
        )
    )

    # connector (+ tools debajo del connector remoto que corresponda)
    connectors = [
        (label, _resolve_api_key(body, api_key))
        for label, body in by_section.get("connector", [])
    ]
    con_ops = _collection(
        "connector", connectors, remote.connectors, managed_ids.get("connector", {})
    )
    key_fp = api_key_fingerprint(api_key) if api_key else ""
    sent_keys = sent_hashes.get("connector_key", {})
    con_ops = [
        SyncOp(
            "connector",
            op.label,
            "update",
            dict(connectors)[op.label],
            op.remote_id,
            "api_key_rotated",
        )
        if op.action == "noop"
        and op.reason == "unchanged"
        and sent_keys.get(op.label) != key_fp
        else op
        for op in con_ops
    ]
    ops.extend(con_ops)
    for label, _ in connectors:
        con_op = next(op for op in con_ops if op.label == label)
        remote_tools = (
            remote.tools.get(con_op.remote_id, ()) if con_op.remote_id else ()
        )
        ops.extend(
            _collection(
                "connector_tools",
                by_section.get("connector_tools", []),
                remote_tools,
                managed_ids.get("connector_tools", {}),
                connector_label=label,
                connector_remote_id=con_op.remote_id,
            )
        )

    ops.extend(
        _ui_skills(
            by_section.get("ui_skills", []),
            remote.ui_skills,
            managed_ids.get("ui_skills", {}),
        )
    )

    # settings: PUT parcial, sin rollout ni ai_audience; frases por hash
    for label, body in by_section.get("settings", []):
        send = {k: v for k, v in body.items() if k not in _NEVER_SEND}
        phrases = send.get("never_say_phrases")
        phrases_changed = phrases is not None and sent_hashes.get("settings", {}).get(
            "never_say_phrases"
        ) != body_hash(phrases)
        remote_settings = remote.settings or {}
        fields_changed = remote.settings is None or any(
            not _same(
                send.get(f),
                _project(remote_settings, {f: send[f]}).get(f)
                if isinstance(send.get(f), dict)
                else remote_settings.get(f),
            )
            for f in _SETTINGS_FIELDS
            if f in send
        )
        if fields_changed or phrases_changed:
            ops.append(
                SyncOp(
                    "settings",
                    label,
                    "update",
                    send,
                    reason="phrases_changed"
                    if phrases_changed and not fields_changed
                    else "",
                )
            )
        else:
            ops.append(SyncOp("settings", label, "noop", reason="unchanged"))

    for label, _ in by_section.get("allowlist", []):
        ops.append(SyncOp("allowlist", label, "skip", reason="d2.3"))

    fingerprint = body_hash(
        [asdict(_redacted(op)) for op in ops] + [list(blocked), cfg.entity_id]
    )
    return SyncPlan(
        agent_id=cfg.agent_id,
        entity_id=cfg.entity_id,
        ops=tuple(ops),
        blocked=blocked,
        fingerprint=fingerprint,
    )
