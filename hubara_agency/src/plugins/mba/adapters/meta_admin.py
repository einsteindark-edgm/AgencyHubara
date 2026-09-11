"""D2.1 — ``MbaAdminClient``: configuración de Meta Business Agent por API.

Contrato (OpenAPI publicados por Meta, v2.0.0, bajados 2026-09-10 de
``developers.facebook.com/documentation/meta-business-agent/reference/*``)::

    {MBA_API_BASE_URL}/{entity_id}/<recurso>[/<id>]
    X-API-Version: 2.0.0  ·  Authorization: Bearer <META_MBA_TOKEN>

    GET    agent_eligibility                      → {is_eligible}
    POST   agent_onboarding {catalog_id?}         → {agent_id}
    GET    agent_config/settings[?agent_id]       → [settings]   (singleton por canal)
    PUT    agent_config/settings[?agent_id]       → settings     (never_say_phrases reemplaza la lista)
    GET/PUT/DELETE agent_config/business_info
    GET/POST agent_config/faq · PUT/DELETE agent_config/faq/{faq_id}
    GET/POST agent_config/skills[?agent_id] · PUT/DELETE agent_config/skills/{skill_id}
    GET/POST agent_connectors · PUT/DELETE agent_connectors/{id} · POST agent_connectors/{id}/upsertApiKey
    GET/POST agent_connectors/{id}/tools · PUT/DELETE agent_connectors/{id}/tools/{tool_id}
    GET (paginado: paging.cursors.after) / POST agent-ui-skills · PUT/DELETE agent-ui-skills/{id}
        (el PUT solo acepta title / status / instruction: NO reenviar component_type ni flow_id)
    GET/POST agent_config/allowlist · DELETE agent_config/allowlist/{entry_id}
    POST   agent_test {user_msg, conversation_id?} → {agent_response, conversation_id, ...}

``entity_id`` = ``phone_number_id`` del número onboardeado. Errores en el
``StandardError`` de Meta (``{title, detail}``) → ``MbaAdminError`` con las
clases cerradas de ``meta_api`` (``not_configured`` / ``rejected`` /
``unavailable`` / ``ambiguous``). Un 2xx con cuerpo que no es JSON o con
otra forma (HTML de un proxy, un objeto donde iba una lista) también es
``rejected`` con ``status=200``: leerlo como "vacío" haría que el sync de
D2.2 recreara todo.

Semánticas de Meta que el diff de D2.2 tiene que respetar: ``PUT settings``
es PARCIAL (lo omitido conserva su valor) y ``never_say_phrases`` es
write-only (no vuelve en el GET ni en la respuesta del PUT); ``FAQ`` y
``connector`` responden 409 al duplicar ``question`` / ``name``.

Los cuerpos viajan tal cual los arma el dominio (``domain/config.py`` ya
produce los requests literales del preview): este adapter NO reinterpreta
campos, solo pone la URL, los headers y la política de reintentos. Las
respuestas vuelven como dicts/listas crudos para que D2.2 haga el diff.

Idempotencia: GET / PUT / DELETE se reintentan también ante errores de red;
los POST que CREAN (skill, FAQ, connector, tool, UI skill, allowlist,
onboarding) no: un timeout es ``ambiguous`` (Meta pudo haberlo creado; un
segundo POST duplicaría el recurso) y el caller relee antes de insistir.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from itertools import count
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

from src.plugins.mba.adapters.meta_api import (
    DEFAULT_TIMEOUT_S,
    MBA_API_BASE_URL,
    MbaApiError,
    mba_token,
    request_json,
)

__all__ = [
    "ADMIN_API_VERSION",
    "RESOURCE_BY_SECTION",
    "FakeMbaAdmin",
    "MbaAdminError",
    "MbaAdminPort",
    "MetaMbaAdmin",
    "admin_url",
]

ADMIN_API_VERSION = "2.0.0"
#: Tope de páginas al listar UI skills (Meta pagina por cursor; un ``next``
#: que nunca se agota no puede colgar el sync).
_MAX_PAGES = 50

#: Sección del preview (``domain/config.ENDPOINTS``) → recurso bajo
#: ``/{entity_id}/``. Test de drift en ``test_meta_admin.py``.
RESOURCE_BY_SECTION: dict[str, str] = {
    "business_info": "agent_config/business_info",
    "faqs": "agent_config/faq",
    "skills": "agent_config/skills",
    "connector": "agent_connectors",
    "connector_tools": "agent_connectors/{connector_id}/tools",
    "ui_skills": "agent-ui-skills",
    "settings": "agent_config/settings",
    "allowlist": "agent_config/allowlist",
}


class MbaAdminError(MbaApiError):
    pass


CONNECTORS_UNAVAILABLE = "connectors_unavailable"


def connectors_unavailable(exc: MbaAdminError) -> bool:
    """Meta gatea ``agent_connectors`` después del onboarding: 400 "Connectors
    are not available for this entity yet. Finish onboarding…". No es una
    caída ni un rechazo de nuestro body: el resto de la config sigue viva."""
    return (
        exc.kind == "rejected"
        and exc.status == 400
        and "not available" in (exc.detail or "").lower()
    )


def admin_url(base_url: str, entity_id: str, *segments: str) -> str:
    return "/".join(
        [base_url.rstrip("/"), entity_id, *(str(s).strip("/") for s in segments)]
    )


@runtime_checkable
class MbaAdminPort(Protocol):
    async def eligibility(self, entity_id: str) -> bool: ...

    async def onboard(
        self, entity_id: str, *, catalog_id: str | None = None
    ) -> str: ...

    async def get_settings(
        self, entity_id: str, *, agent_id: str | None = None
    ) -> list[dict[str, Any]]: ...

    async def put_settings(
        self, entity_id: str, body: dict[str, Any], *, agent_id: str | None = None
    ) -> dict[str, Any]: ...

    async def get_business_info(self, entity_id: str) -> dict[str, Any]: ...

    async def put_business_info(
        self, entity_id: str, body: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def delete_business_info(self, entity_id: str) -> None: ...

    async def list_faqs(self, entity_id: str) -> list[dict[str, Any]]: ...

    async def create_faq(
        self, entity_id: str, body: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def update_faq(
        self, entity_id: str, faq_id: str, body: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def delete_faq(self, entity_id: str, faq_id: str) -> None: ...

    async def list_skills(
        self, entity_id: str, *, agent_id: str | None = None
    ) -> list[dict[str, Any]]: ...

    async def create_skill(
        self, entity_id: str, body: dict[str, Any], *, agent_id: str | None = None
    ) -> dict[str, Any]: ...

    async def update_skill(
        self, entity_id: str, skill_id: str, body: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def delete_skill(self, entity_id: str, skill_id: str) -> None: ...

    async def list_connectors(self, entity_id: str) -> list[dict[str, Any]]: ...

    async def create_connector(
        self, entity_id: str, body: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def update_connector(
        self, entity_id: str, connector_id: str, body: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def delete_connector(self, entity_id: str, connector_id: str) -> None: ...

    async def upsert_connector_api_key(
        self, entity_id: str, connector_id: str, api_key_config: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def list_connector_tools(
        self, entity_id: str, connector_id: str
    ) -> list[dict[str, Any]]: ...

    async def create_connector_tool(
        self, entity_id: str, connector_id: str, body: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def update_connector_tool(
        self, entity_id: str, connector_id: str, tool_id: str, body: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def delete_connector_tool(
        self, entity_id: str, connector_id: str, tool_id: str
    ) -> None: ...

    async def list_ui_skills(self, entity_id: str) -> list[dict[str, Any]]: ...

    async def create_ui_skill(
        self, entity_id: str, body: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def update_ui_skill(
        self, entity_id: str, ui_skill_id: str, body: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def delete_ui_skill(self, entity_id: str, ui_skill_id: str) -> None: ...

    async def list_allowlist(self, entity_id: str) -> list[dict[str, Any]]: ...

    async def add_allowlist(
        self, entity_id: str, consumer_phone_number: str
    ) -> dict[str, Any]: ...

    async def remove_allowlist(self, entity_id: str, entry_id: str) -> None: ...

    async def agent_test(
        self, entity_id: str, user_msg: str, *, conversation_id: str | None = None
    ) -> dict[str, Any]: ...


def _unexpected(what: str) -> MbaAdminError:
    return MbaAdminError(
        "rejected", status=200, detail=f"respuesta 2xx con forma inesperada: {what}"
    )


def _as_dict(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise _unexpected("se esperaba un objeto")
    return payload


def _as_list(payload: Any) -> list[dict[str, Any]]:
    """Lista plana (skills, FAQs, connectors, tools, allowlist, settings) o
    envuelta en ``{data: [...]}`` (UI skills). Cualquier otra forma levanta."""
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        payload = payload["data"]
    if not isinstance(payload, list) or not all(isinstance(x, dict) for x in payload):
        raise _unexpected("se esperaba una lista")
    return payload


def _looks_like_settings(payload: Any) -> bool:
    return isinstance(payload, dict) and "agent_id" in payload and "data" not in payload


class MetaMbaAdmin:
    """Vendor real (httpx, import perezoso). Levanta ``MbaAdminError``."""

    def __init__(
        self,
        *,
        token: Callable[[], str] = mba_token,
        base_url: str = MBA_API_BASE_URL,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._token = token
        self._base_url = base_url
        self._timeout_s = timeout_s
        self._sleep = sleep

    async def _call(
        self,
        method: str,
        *segments: str,
        body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        creates: bool = False,
    ) -> Any:
        return await request_json(
            method,
            admin_url(self._base_url, segments[0], *segments[1:]),
            api_version=ADMIN_API_VERSION,
            token=self._token(),
            body=body,
            params=params,
            timeout_s=self._timeout_s,
            sleep=self._sleep,
            error_cls=MbaAdminError,
            retry_on_transport_error=not creates,
            strict_json=True,
        )

    async def _delete(self, *segments: str) -> None:
        """Un DELETE reintentado tras un error de red puede ver 404 porque el
        primer intento sí borró: eso cuenta como hecho. Un 404 al primer
        intento sí es del caller (id equivocado)."""
        try:
            await self._call("DELETE", *segments)
        except MbaAdminError as exc:
            if exc.kind == "rejected" and exc.status == 404 and exc.attempts > 1:
                return
            raise

    # -- onboard -----------------------------------------------------------

    async def eligibility(self, entity_id: str) -> bool:
        payload = _as_dict(await self._call("GET", entity_id, "agent_eligibility"))
        if not isinstance(payload.get("is_eligible"), bool):
            raise _unexpected("falta is_eligible")
        return payload["is_eligible"]

    async def onboard(self, entity_id: str, *, catalog_id: str | None = None) -> str:
        body = {"catalog_id": catalog_id} if catalog_id else {}
        return str(
            _as_dict(
                await self._call(
                    "POST", entity_id, "agent_onboarding", body=body, creates=True
                )
            ).get("agent_id")
            or ""
        )

    # -- settings ----------------------------------------------------------

    async def get_settings(
        self, entity_id: str, *, agent_id: str | None = None
    ) -> list[dict[str, Any]]:
        params = {"agent_id": agent_id} if agent_id else None
        payload = await self._call(
            "GET", entity_id, "agent_config/settings", params=params
        )
        # Meta documenta una lista; con ``?agent_id`` puede venir el objeto solo.
        return [payload] if _looks_like_settings(payload) else _as_list(payload)

    async def put_settings(
        self, entity_id: str, body: dict[str, Any], *, agent_id: str | None = None
    ) -> dict[str, Any]:
        params = {"agent_id": agent_id} if agent_id else None
        return _as_dict(
            await self._call(
                "PUT", entity_id, "agent_config/settings", body=body, params=params
            )
        )

    # -- knowledge ---------------------------------------------------------

    async def get_business_info(self, entity_id: str) -> dict[str, Any]:
        return _as_dict(
            await self._call("GET", entity_id, "agent_config/business_info")
        )

    async def put_business_info(
        self, entity_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return _as_dict(
            await self._call("PUT", entity_id, "agent_config/business_info", body=body)
        )

    async def delete_business_info(self, entity_id: str) -> None:
        await self._delete(entity_id, "agent_config/business_info")

    async def list_faqs(self, entity_id: str) -> list[dict[str, Any]]:
        return _as_list(await self._call("GET", entity_id, "agent_config/faq"))

    async def create_faq(self, entity_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return _as_dict(
            await self._call(
                "POST", entity_id, "agent_config/faq", body=body, creates=True
            )
        )

    async def update_faq(
        self, entity_id: str, faq_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return _as_dict(
            await self._call("PUT", entity_id, "agent_config/faq", faq_id, body=body)
        )

    async def delete_faq(self, entity_id: str, faq_id: str) -> None:
        await self._delete(entity_id, "agent_config/faq", faq_id)

    # -- skills ------------------------------------------------------------

    async def list_skills(
        self, entity_id: str, *, agent_id: str | None = None
    ) -> list[dict[str, Any]]:
        params = {"agent_id": agent_id} if agent_id else None
        return _as_list(
            await self._call("GET", entity_id, "agent_config/skills", params=params)
        )

    async def create_skill(
        self, entity_id: str, body: dict[str, Any], *, agent_id: str | None = None
    ) -> dict[str, Any]:
        params = {"agent_id": agent_id} if agent_id else None
        return _as_dict(
            await self._call(
                "POST",
                entity_id,
                "agent_config/skills",
                body=body,
                params=params,
                creates=True,
            )
        )

    async def update_skill(
        self, entity_id: str, skill_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return _as_dict(
            await self._call(
                "PUT", entity_id, "agent_config/skills", skill_id, body=body
            )
        )

    async def delete_skill(self, entity_id: str, skill_id: str) -> None:
        await self._delete(entity_id, "agent_config/skills", skill_id)

    # -- connectors + tools ------------------------------------------------

    async def list_connectors(self, entity_id: str) -> list[dict[str, Any]]:
        return _as_list(await self._call("GET", entity_id, "agent_connectors"))

    async def create_connector(
        self, entity_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return _as_dict(
            await self._call(
                "POST", entity_id, "agent_connectors", body=body, creates=True
            )
        )

    async def update_connector(
        self, entity_id: str, connector_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return _as_dict(
            await self._call(
                "PUT", entity_id, "agent_connectors", connector_id, body=body
            )
        )

    async def delete_connector(self, entity_id: str, connector_id: str) -> None:
        await self._delete(entity_id, "agent_connectors", connector_id)

    async def upsert_connector_api_key(
        self, entity_id: str, connector_id: str, api_key_config: dict[str, Any]
    ) -> dict[str, Any]:
        # Upsert: idempotente por definición → sí se reintenta ante red.
        return _as_dict(
            await self._call(
                "POST",
                entity_id,
                "agent_connectors",
                connector_id,
                "upsertApiKey",
                body={"api_key_config": api_key_config},
            )
        )

    async def list_connector_tools(
        self, entity_id: str, connector_id: str
    ) -> list[dict[str, Any]]:
        return _as_list(
            await self._call(
                "GET", entity_id, "agent_connectors", connector_id, "tools"
            )
        )

    async def create_connector_tool(
        self, entity_id: str, connector_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return _as_dict(
            await self._call(
                "POST",
                entity_id,
                "agent_connectors",
                connector_id,
                "tools",
                body=body,
                creates=True,
            )
        )

    async def update_connector_tool(
        self, entity_id: str, connector_id: str, tool_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return _as_dict(
            await self._call(
                "PUT",
                entity_id,
                "agent_connectors",
                connector_id,
                "tools",
                tool_id,
                body=body,
            )
        )

    async def delete_connector_tool(
        self, entity_id: str, connector_id: str, tool_id: str
    ) -> None:
        await self._delete(
            entity_id, "agent_connectors", connector_id, "tools", tool_id
        )

    # -- UI skills ---------------------------------------------------------

    async def list_ui_skills(self, entity_id: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        after: str | None = None
        for _ in range(_MAX_PAGES):
            params = {"after": after} if after else None
            page = _as_dict(
                await self._call("GET", entity_id, "agent-ui-skills", params=params)
            )
            out.extend(_as_list(page))
            paging = page.get("paging") if isinstance(page.get("paging"), dict) else {}
            cursors = (
                paging.get("cursors") if isinstance(paging.get("cursors"), dict) else {}
            )
            cursor = cursors.get("after")
            if (
                not paging.get("next")
                or not isinstance(cursor, str)
                or not cursor
                or cursor == after
            ):
                return out
            after = cursor
        raise MbaAdminError(
            "unavailable",
            detail=f"paginación de UI skills sin fin ({_MAX_PAGES} páginas)",
            attempts=_MAX_PAGES,
        )

    async def create_ui_skill(
        self, entity_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return _as_dict(
            await self._call(
                "POST", entity_id, "agent-ui-skills", body=body, creates=True
            )
        )

    async def update_ui_skill(
        self, entity_id: str, ui_skill_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return _as_dict(
            await self._call(
                "PUT", entity_id, "agent-ui-skills", ui_skill_id, body=body
            )
        )

    async def delete_ui_skill(self, entity_id: str, ui_skill_id: str) -> None:
        await self._delete(entity_id, "agent-ui-skills", ui_skill_id)

    # -- allowlist ---------------------------------------------------------

    async def list_allowlist(self, entity_id: str) -> list[dict[str, Any]]:
        return _as_list(await self._call("GET", entity_id, "agent_config/allowlist"))

    async def add_allowlist(
        self, entity_id: str, consumer_phone_number: str
    ) -> dict[str, Any]:
        body = {"consumer_phone_number": consumer_phone_number}
        return _as_dict(
            await self._call(
                "POST", entity_id, "agent_config/allowlist", body=body, creates=True
            )
        )

    async def remove_allowlist(self, entity_id: str, entry_id: str) -> None:
        await self._delete(entity_id, "agent_config/allowlist", entry_id)

    # -- operate -----------------------------------------------------------

    async def agent_test(
        self, entity_id: str, user_msg: str, *, conversation_id: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"user_msg": user_msg}
        if conversation_id:
            body["conversation_id"] = conversation_id
        # No toca hilos vivos ni factura: repetirlo ante red es inocuo.
        return _as_dict(await self._call("POST", entity_id, "agent_test", body=body))


@dataclass
class FakeMbaAdmin:
    """Estado en memoria por ``entity_id`` + registro de llamadas
    ``(operación, entity_id, *args)``; ``fail_with`` simula a Meta."""

    calls: list[tuple[Any, ...]] = field(default_factory=list)
    fail_with: MbaAdminError | None = None
    #: falla SOLO esa operación (p.ej. ``{"list_connectors": err}``).
    fail_ops: dict[str, MbaAdminError] = field(default_factory=dict)
    eligible: bool = True
    agent_id: str = "agent-fake"
    settings: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: write-only en Meta: se guarda aparte para que un test pueda mirarlo.
    never_say_phrases: dict[str, list[str]] = field(default_factory=dict)
    business_info: dict[str, dict[str, Any]] = field(default_factory=dict)
    faqs: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    skills: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    connectors: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    tools: dict[tuple[str, str], list[dict[str, Any]]] = field(default_factory=dict)
    ui_skills: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    allowlist: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    test_replies: list[dict[str, Any]] = field(default_factory=list)
    _ids: dict[str, Any] = field(default_factory=dict)

    def _record(self, op: str, entity_id: str, *args: Any) -> None:
        self.calls.append((op, entity_id, *args))
        if self.fail_with is not None:
            raise self.fail_with
        if op in self.fail_ops:
            raise self.fail_ops[op]

    def _new_id(self, prefix: str) -> str:
        counter = self._ids.setdefault(prefix, count(1))
        return f"{prefix}-{next(counter)}"

    def _create(
        self,
        bucket: list[dict[str, Any]],
        prefix: str,
        body: dict[str, Any],
        *,
        unique: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        if unique and any(x.get(unique) == body.get(unique) for x in bucket):
            raise MbaAdminError(
                "rejected",
                status=409,
                detail=f"{unique} duplicado: {body.get(unique)!r}",
            )
        item = {"id": self._new_id(prefix), **body, **extra}
        bucket.append(item)
        return item

    @staticmethod
    def _update(
        bucket: list[dict[str, Any]], item_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        for i, item in enumerate(bucket):
            if item.get("id") == item_id:
                bucket[i] = {**item, **body}
                return bucket[i]
        raise MbaAdminError("rejected", status=404, detail=f"{item_id} no existe")

    @staticmethod
    def _delete(bucket: list[dict[str, Any]], item_id: str) -> None:
        before = len(bucket)
        bucket[:] = [x for x in bucket if x.get("id") != item_id]
        if len(bucket) == before:
            raise MbaAdminError("rejected", status=404, detail=f"{item_id} no existe")

    async def eligibility(self, entity_id: str) -> bool:
        self._record("eligibility", entity_id)
        return self.eligible

    async def onboard(self, entity_id: str, *, catalog_id: str | None = None) -> str:
        self._record("onboard", entity_id, catalog_id)
        return self.agent_id

    async def get_settings(
        self, entity_id: str, *, agent_id: str | None = None
    ) -> list[dict[str, Any]]:
        self._record("get_settings", entity_id, agent_id)
        return [self.settings[entity_id]] if entity_id in self.settings else []

    async def put_settings(
        self, entity_id: str, body: dict[str, Any], *, agent_id: str | None = None
    ) -> dict[str, Any]:
        self._record("put_settings", entity_id, body, agent_id)
        body = dict(body)
        if (
            "never_say_phrases" in body
        ):  # write-only: no vuelve en GET ni en la respuesta
            self.never_say_phrases[entity_id] = list(
                body.pop("never_say_phrases") or []
            )
        prev = self.settings.get(entity_id) or {
            "agent_id": agent_id or self.agent_id,
            "channel": "whatsapp",
        }
        self.settings[entity_id] = {**prev, **body}  # PUT parcial, como Meta
        return dict(self.settings[entity_id])

    async def get_business_info(self, entity_id: str) -> dict[str, Any]:
        self._record("get_business_info", entity_id)
        return dict(self.business_info.get(entity_id, {}))

    async def put_business_info(
        self, entity_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        self._record("put_business_info", entity_id, body)
        self.business_info[entity_id] = dict(body)
        return dict(body)

    async def delete_business_info(self, entity_id: str) -> None:
        self._record("delete_business_info", entity_id)
        self.business_info.pop(entity_id, None)

    async def list_faqs(self, entity_id: str) -> list[dict[str, Any]]:
        self._record("list_faqs", entity_id)
        return list(self.faqs.get(entity_id, []))

    async def create_faq(self, entity_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self._record("create_faq", entity_id, body)
        return self._create(
            self.faqs.setdefault(entity_id, []), "faq", body, unique="question"
        )

    async def update_faq(
        self, entity_id: str, faq_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        self._record("update_faq", entity_id, faq_id, body)
        return self._update(self.faqs.setdefault(entity_id, []), faq_id, body)

    async def delete_faq(self, entity_id: str, faq_id: str) -> None:
        self._record("delete_faq", entity_id, faq_id)
        self._delete(self.faqs.setdefault(entity_id, []), faq_id)

    async def list_skills(
        self, entity_id: str, *, agent_id: str | None = None
    ) -> list[dict[str, Any]]:
        self._record("list_skills", entity_id, agent_id)
        return list(self.skills.get(entity_id, []))

    async def create_skill(
        self, entity_id: str, body: dict[str, Any], *, agent_id: str | None = None
    ) -> dict[str, Any]:
        self._record("create_skill", entity_id, body, agent_id)
        return self._create(
            self.skills.setdefault(entity_id, []),
            "skill",
            body,
            channel="whatsapp",
            status="active",
        )

    async def update_skill(
        self, entity_id: str, skill_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        self._record("update_skill", entity_id, skill_id, body)
        return self._update(self.skills.setdefault(entity_id, []), skill_id, body)

    async def delete_skill(self, entity_id: str, skill_id: str) -> None:
        self._record("delete_skill", entity_id, skill_id)
        self._delete(self.skills.setdefault(entity_id, []), skill_id)

    async def list_connectors(self, entity_id: str) -> list[dict[str, Any]]:
        self._record("list_connectors", entity_id)
        return list(self.connectors.get(entity_id, []))

    async def create_connector(
        self, entity_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        self._record("create_connector", entity_id, body)
        return self._create(
            self.connectors.setdefault(entity_id, []), "connector", body, unique="name"
        )

    async def update_connector(
        self, entity_id: str, connector_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        self._record("update_connector", entity_id, connector_id, body)
        return self._update(
            self.connectors.setdefault(entity_id, []), connector_id, body
        )

    async def delete_connector(self, entity_id: str, connector_id: str) -> None:
        self._record("delete_connector", entity_id, connector_id)
        self._delete(self.connectors.setdefault(entity_id, []), connector_id)
        self.tools.pop((entity_id, connector_id), None)

    async def upsert_connector_api_key(
        self, entity_id: str, connector_id: str, api_key_config: dict[str, Any]
    ) -> dict[str, Any]:
        self._record(
            "upsert_connector_api_key", entity_id, connector_id, api_key_config
        )
        return self._update(
            self.connectors.setdefault(entity_id, []),
            connector_id,
            {"auth_config": {"api_key": api_key_config}},
        )

    async def list_connector_tools(
        self, entity_id: str, connector_id: str
    ) -> list[dict[str, Any]]:
        self._record("list_connector_tools", entity_id, connector_id)
        return list(self.tools.get((entity_id, connector_id), []))

    async def create_connector_tool(
        self, entity_id: str, connector_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        self._record("create_connector_tool", entity_id, connector_id, body)
        return self._create(
            self.tools.setdefault((entity_id, connector_id), []), "tool", body
        )

    async def update_connector_tool(
        self, entity_id: str, connector_id: str, tool_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        self._record("update_connector_tool", entity_id, connector_id, tool_id, body)
        return self._update(
            self.tools.setdefault((entity_id, connector_id), []), tool_id, body
        )

    async def delete_connector_tool(
        self, entity_id: str, connector_id: str, tool_id: str
    ) -> None:
        self._record("delete_connector_tool", entity_id, connector_id, tool_id)
        self._delete(self.tools.setdefault((entity_id, connector_id), []), tool_id)

    async def list_ui_skills(self, entity_id: str) -> list[dict[str, Any]]:
        self._record("list_ui_skills", entity_id)
        return list(self.ui_skills.get(entity_id, []))

    async def create_ui_skill(
        self, entity_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        self._record("create_ui_skill", entity_id, body)
        return self._create(self.ui_skills.setdefault(entity_id, []), "ui", body)

    async def update_ui_skill(
        self, entity_id: str, ui_skill_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        self._record("update_ui_skill", entity_id, ui_skill_id, body)
        return self._update(self.ui_skills.setdefault(entity_id, []), ui_skill_id, body)

    async def delete_ui_skill(self, entity_id: str, ui_skill_id: str) -> None:
        self._record("delete_ui_skill", entity_id, ui_skill_id)
        self._delete(self.ui_skills.setdefault(entity_id, []), ui_skill_id)

    async def list_allowlist(self, entity_id: str) -> list[dict[str, Any]]:
        self._record("list_allowlist", entity_id)
        return list(self.allowlist.get(entity_id, []))

    async def add_allowlist(
        self, entity_id: str, consumer_phone_number: str
    ) -> dict[str, Any]:
        self._record("add_allowlist", entity_id, consumer_phone_number)
        return self._create(
            self.allowlist.setdefault(entity_id, []),
            "entry",
            {"consumer_phone_number": consumer_phone_number},
        )

    async def remove_allowlist(self, entity_id: str, entry_id: str) -> None:
        self._record("remove_allowlist", entity_id, entry_id)
        self._delete(self.allowlist.setdefault(entity_id, []), entry_id)

    async def agent_test(
        self, entity_id: str, user_msg: str, *, conversation_id: str | None = None
    ) -> dict[str, Any]:
        self._record("agent_test", entity_id, user_msg, conversation_id)
        if self.test_replies:
            return self.test_replies.pop(0)
        return {
            "message_id": self._new_id("msg"),
            "agent_response": f"eco: {user_msg}",
            "conversation_id": conversation_id or self._new_id("conv"),
        }
