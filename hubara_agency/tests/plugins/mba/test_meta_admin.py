"""D2.1 — `MbaAdminClient`: cliente HTTP de CONFIGURACIÓN de la Meta Business
Agent Cloud API (lo que D2.2 usa para llevar la tab a Meta).

Contrato leído de los OpenAPI publicados por Meta (v2.0.0, bajados 2026-09-10
de developers.facebook.com/documentation/meta-business-agent/reference/*):
base `https://api.facebook.com/{entity_id}/...`, header `X-API-Version: 2.0.0`,
errores `StandardError {title, detail, type?, status?}`.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from src.plugins.mba.adapters.meta_admin import (
    ADMIN_API_VERSION,
    FakeMbaAdmin,
    MbaAdminError,
    MbaAdminPort,
    MetaMbaAdmin,
    admin_url,
)
from src.plugins.mba.adapters.meta_api import MBA_API_BASE_URL, MbaApiError

ENTITY = "PHONE_777"
BASE = f"https://api.facebook.com/{ENTITY}"


class _Sleeper:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def _client(token: str = "tok", sleeper: _Sleeper | None = None) -> MetaMbaAdmin:
    return MetaMbaAdmin(token=lambda: token, sleep=sleeper or _Sleeper())


def _last_json(route: respx.Route) -> dict:
    return json.loads(route.calls.last.request.content)


# ---------------------------------------------------------------------------
# Contrato: URL, versión, port
# ---------------------------------------------------------------------------


def test_urls_and_version_follow_metas_openapi() -> None:
    assert ADMIN_API_VERSION == "2.0.0"
    assert (
        admin_url(MBA_API_BASE_URL, ENTITY, "agent_eligibility")
        == f"{BASE}/agent_eligibility"
    )
    assert (
        admin_url(MBA_API_BASE_URL, ENTITY, "agent_config/skills", "sk1")
        == f"{BASE}/agent_config/skills/sk1"
    )
    assert (
        admin_url(MBA_API_BASE_URL, ENTITY, "agent_connectors", "c1", "tools", "t1")
        == f"{BASE}/agent_connectors/c1/tools/t1"
    )
    assert isinstance(MetaMbaAdmin(token=lambda: "x"), MbaAdminPort)
    assert isinstance(FakeMbaAdmin(), MbaAdminPort)


def test_the_client_paths_match_the_preview_catalogue() -> None:
    """La tab (D0, `domain/config.ENDPOINTS`) y el cliente (D2.1) hablan de
    los MISMOS paths: si uno cambia, este test lo delata."""
    from src.plugins.mba.adapters.meta_admin import RESOURCE_BY_SECTION
    from src.plugins.mba.domain.config import ENDPOINTS

    assert set(RESOURCE_BY_SECTION) == {ep.section for ep in ENDPOINTS}
    for ep in ENDPOINTS:
        expected = ep.path.replace("{entity_id}", ENTITY).replace(
            "{connector_id}", "CID"
        )
        got = admin_url(
            MBA_API_BASE_URL,
            ENTITY,
            RESOURCE_BY_SECTION[ep.section].format(connector_id="CID"),
        )
        assert got == f"{MBA_API_BASE_URL}{expected}", ep.section


# ---------------------------------------------------------------------------
# Onboard
# ---------------------------------------------------------------------------


@respx.mock
async def test_eligibility_is_a_get_with_the_bearer_and_version_headers() -> None:
    route = respx.get(f"{BASE}/agent_eligibility").mock(
        return_value=httpx.Response(200, json={"is_eligible": True})
    )
    assert await _client().eligibility(ENTITY) is True
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer tok"
    assert req.headers["X-API-Version"] == ADMIN_API_VERSION
    respx.get(f"{BASE}/agent_eligibility").mock(
        return_value=httpx.Response(200, json={"is_eligible": False})
    )
    assert await _client().eligibility(ENTITY) is False


@respx.mock
async def test_onboarding_posts_an_empty_body_unless_a_catalog_is_linked() -> None:
    route = respx.post(f"{BASE}/agent_onboarding").mock(
        return_value=httpx.Response(201, json={"agent_id": "ag_1"})
    )
    assert await _client().onboard(ENTITY) == "ag_1"
    assert _last_json(route) == {}
    assert await _client().onboard(ENTITY, catalog_id="cat_9") == "ag_1"
    assert _last_json(route) == {"catalog_id": "cat_9"}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@respx.mock
async def test_settings_get_returns_the_list_and_put_sends_the_body_verbatim() -> None:
    settings = {
        "agent_id": "ag_1",
        "channel": "whatsapp",
        "rollout": {"enabled": False},
        "ai_audience": "ALLOWLISTED_ONLY",
    }
    get = respx.get(f"{BASE}/agent_config/settings").mock(
        return_value=httpx.Response(200, json=[settings])
    )
    assert await _client().get_settings(ENTITY) == [settings]
    assert not get.calls.last.request.url.params
    await _client().get_settings(ENTITY, agent_id="ag_1")
    assert get.calls.last.request.url.params["agent_id"] == "ag_1"

    put = respx.put(f"{BASE}/agent_config/settings").mock(
        return_value=httpx.Response(200, json=settings)
    )
    body = {
        "rollout": {"enabled": False},
        "ai_audience": "ALLOWLISTED_ONLY",
        "never_say_phrases": ["gratis"],
    }
    assert await _client().put_settings(ENTITY, body) == settings
    assert _last_json(put) == body
    assert not put.calls.last.request.url.params
    await _client().put_settings(ENTITY, body, agent_id="ag_1")
    assert put.calls.last.request.url.params["agent_id"] == "ag_1"


# ---------------------------------------------------------------------------
# Knowledge: business_info + FAQs
# ---------------------------------------------------------------------------


@respx.mock
async def test_business_info_get_put_and_delete() -> None:
    info = {
        "business_description": "Velas",
        "contact_info": {"email": "hola@hubara.co"},
    }
    respx.get(f"{BASE}/agent_config/business_info").mock(
        return_value=httpx.Response(200, json=info)
    )
    put = respx.put(f"{BASE}/agent_config/business_info").mock(
        return_value=httpx.Response(200, json=info)
    )
    delete = respx.delete(f"{BASE}/agent_config/business_info").mock(
        return_value=httpx.Response(204)
    )
    c = _client()
    assert await c.get_business_info(ENTITY) == info
    assert await c.put_business_info(ENTITY, info) == info
    assert _last_json(put) == info
    assert await c.delete_business_info(ENTITY) is None
    assert delete.call_count == 1


@respx.mock
async def test_faqs_are_a_crud_by_id() -> None:
    faq = {"id": "f1", "question": "¿Envían?", "answer": "Sí"}
    lst = respx.get(f"{BASE}/agent_config/faq").mock(
        return_value=httpx.Response(200, json=[faq])
    )
    create = respx.post(f"{BASE}/agent_config/faq").mock(
        return_value=httpx.Response(201, json=faq)
    )
    update = respx.put(f"{BASE}/agent_config/faq/f1").mock(
        return_value=httpx.Response(200, json=faq)
    )
    delete = respx.delete(f"{BASE}/agent_config/faq/f1").mock(
        return_value=httpx.Response(204)
    )
    c = _client()
    assert await c.list_faqs(ENTITY) == [faq]
    assert lst.call_count == 1
    assert await c.create_faq(ENTITY, {"question": "¿Envían?", "answer": "Sí"}) == faq
    assert _last_json(create) == {"question": "¿Envían?", "answer": "Sí"}
    assert (
        await c.update_faq(
            ENTITY, "f1", {"question": "¿Envían?", "answer": "Sí, a todo el país"}
        )
        == faq
    )
    assert _last_json(update)["answer"] == "Sí, a todo el país"
    await c.delete_faq(ENTITY, "f1")
    assert delete.call_count == 1


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


@respx.mock
async def test_skills_are_a_crud_by_id_with_the_agent_id_query() -> None:
    skill = {
        "id": "s1",
        "title": "saludo",
        "description": "d",
        "skill": "texto",
        "channel": "whatsapp",
        "status": "active",
    }
    lst = respx.get(f"{BASE}/agent_config/skills").mock(
        return_value=httpx.Response(200, json=[skill])
    )
    create = respx.post(f"{BASE}/agent_config/skills").mock(
        return_value=httpx.Response(201, json=skill)
    )
    update = respx.put(f"{BASE}/agent_config/skills/s1").mock(
        return_value=httpx.Response(200, json=skill)
    )
    delete = respx.delete(f"{BASE}/agent_config/skills/s1").mock(
        return_value=httpx.Response(204)
    )
    c = _client()
    assert await c.list_skills(ENTITY) == [skill]
    assert await c.list_skills(ENTITY, agent_id="ag_1") == [skill]
    assert lst.calls.last.request.url.params["agent_id"] == "ag_1"
    body = {"title": "saludo", "description": "d", "skill": "texto"}
    assert await c.create_skill(ENTITY, body, agent_id="ag_1") == skill
    assert (
        _last_json(create) == body
        and create.calls.last.request.url.params["agent_id"] == "ag_1"
    )
    assert await c.update_skill(ENTITY, "s1", body) == skill
    assert _last_json(update) == body
    await c.delete_skill(ENTITY, "s1")
    assert delete.call_count == 1


# ---------------------------------------------------------------------------
# Connectors + tools
# ---------------------------------------------------------------------------


@respx.mock
async def test_connectors_crud_and_api_key_upsert() -> None:
    con = {
        "id": "c1",
        "name": "hubara",
        "base_url": "https://x",
        "auth_type": "API_KEY",
        "connection_status": {"status": "ACTIVE"},
    }
    respx.get(f"{BASE}/agent_connectors").mock(
        return_value=httpx.Response(200, json=[con])
    )
    create = respx.post(f"{BASE}/agent_connectors").mock(
        return_value=httpx.Response(201, json=con)
    )
    update = respx.put(f"{BASE}/agent_connectors/c1").mock(
        return_value=httpx.Response(200, json=con)
    )
    key = respx.post(f"{BASE}/agent_connectors/c1/upsertApiKey").mock(
        return_value=httpx.Response(200, json=con)
    )
    delete = respx.delete(f"{BASE}/agent_connectors/c1").mock(
        return_value=httpx.Response(204)
    )
    c = _client()
    assert await c.list_connectors(ENTITY) == [con]
    body = {
        "name": "hubara",
        "description": "d",
        "base_url": "https://x",
        "auth_type": "API_KEY",
    }
    assert await c.create_connector(ENTITY, body) == con
    assert _last_json(create) == body
    assert await c.update_connector(ENTITY, "c1", body) == con
    assert _last_json(update) == body
    cfg = {"headers": [{"field_name": "X-API-Key", "value": "k", "prefix": ""}]}
    assert await c.upsert_connector_api_key(ENTITY, "c1", cfg) == con
    assert _last_json(key) == {"api_key_config": cfg}
    await c.delete_connector(ENTITY, "c1")
    assert delete.call_count == 1


@respx.mock
async def test_connector_tools_live_under_their_connector() -> None:
    tool = {
        "id": "t1",
        "name": "check_order_status",
        "description": "d",
        "request_definition": {"method": "GET", "path": "/x"},
        "user_auth_required": False,
    }
    respx.get(f"{BASE}/agent_connectors/c1/tools").mock(
        return_value=httpx.Response(200, json=[tool])
    )
    create = respx.post(f"{BASE}/agent_connectors/c1/tools").mock(
        return_value=httpx.Response(201, json=tool)
    )
    update = respx.put(f"{BASE}/agent_connectors/c1/tools/t1").mock(
        return_value=httpx.Response(200, json=tool)
    )
    delete = respx.delete(f"{BASE}/agent_connectors/c1/tools/t1").mock(
        return_value=httpx.Response(204)
    )
    c = _client()
    assert await c.list_connector_tools(ENTITY, "c1") == [tool]
    body = {k: v for k, v in tool.items() if k != "id"}
    assert await c.create_connector_tool(ENTITY, "c1", body) == tool
    assert _last_json(create) == body
    assert await c.update_connector_tool(ENTITY, "c1", "t1", body) == tool
    assert _last_json(update) == body
    await c.delete_connector_tool(ENTITY, "c1", "t1")
    assert delete.call_count == 1


# ---------------------------------------------------------------------------
# UI skills (paginadas)
# ---------------------------------------------------------------------------


@respx.mock
async def test_ui_skills_list_follows_the_cursor_until_the_last_page() -> None:
    a = {
        "id": "u1",
        "title": "catalogo",
        "component_type": "carousel_url",
        "status": "enabled",
        "instruction": "i",
    }
    b = {
        "id": "u2",
        "title": "flow",
        "component_type": "flow",
        "status": "enabled",
        "instruction": "i",
    }
    route = respx.get(f"{BASE}/agent-ui-skills").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "data": [a],
                    "paging": {
                        "cursors": {"before": "x", "after": "CUR"},
                        "next": "https://api.facebook.com/next",
                    },
                },
            ),
            httpx.Response(
                200,
                json={
                    "data": [b],
                    "paging": {"cursors": {"before": "CUR", "after": "END"}},
                },
            ),
        ]
    )
    assert await _client().list_ui_skills(ENTITY) == [a, b]
    assert route.call_count == 2
    assert "after" not in route.calls[0].request.url.params
    assert route.calls[1].request.url.params["after"] == "CUR"


@respx.mock
async def test_ui_skills_create_update_and_delete() -> None:
    ui = {
        "id": "u1",
        "title": "catalogo",
        "component_type": "carousel_url",
        "status": "enabled",
        "instruction": "i",
    }
    create = respx.post(f"{BASE}/agent-ui-skills").mock(
        return_value=httpx.Response(201, json=ui)
    )
    update = respx.put(f"{BASE}/agent-ui-skills/u1").mock(
        return_value=httpx.Response(200, json=ui)
    )
    delete = respx.delete(f"{BASE}/agent-ui-skills/u1").mock(
        return_value=httpx.Response(204)
    )
    c = _client()
    body = {k: v for k, v in ui.items() if k != "id"}
    assert await c.create_ui_skill(ENTITY, body) == ui
    assert _last_json(create) == body
    assert await c.update_ui_skill(ENTITY, "u1", {"status": "disabled"}) == ui
    assert _last_json(update) == {"status": "disabled"}
    await c.delete_ui_skill(ENTITY, "u1")
    assert delete.call_count == 1


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------


@respx.mock
async def test_allowlist_list_add_and_remove_by_entry_id() -> None:
    entry = {"id": "e1", "consumer_phone_number": "+573001234567"}
    respx.get(f"{BASE}/agent_config/allowlist").mock(
        return_value=httpx.Response(200, json=[entry])
    )
    add = respx.post(f"{BASE}/agent_config/allowlist").mock(
        return_value=httpx.Response(201, json=entry)
    )
    delete = respx.delete(f"{BASE}/agent_config/allowlist/e1").mock(
        return_value=httpx.Response(204)
    )
    c = _client()
    assert await c.list_allowlist(ENTITY) == [entry]
    assert await c.add_allowlist(ENTITY, "+573001234567") == entry
    assert _last_json(add) == {"consumer_phone_number": "+573001234567"}
    await c.remove_allowlist(ENTITY, "e1")
    assert delete.call_count == 1


# ---------------------------------------------------------------------------
# agent_test
# ---------------------------------------------------------------------------


@respx.mock
async def test_agent_test_sends_the_message_and_threads_the_conversation() -> None:
    reply = {"message_id": "m1", "agent_response": "Hola", "conversation_id": "conv1"}
    route = respx.post(f"{BASE}/agent_test").mock(
        return_value=httpx.Response(200, json=reply)
    )
    c = _client()
    assert await c.agent_test(ENTITY, "hola") == reply
    assert _last_json(route) == {"user_msg": "hola"}
    await c.agent_test(ENTITY, "¿y envíos?", conversation_id="conv1")
    assert _last_json(route) == {"user_msg": "¿y envíos?", "conversation_id": "conv1"}


# ---------------------------------------------------------------------------
# Errores y reintentos (360dialog: "cualquier endpoint puede devolver 4xx/500")
# ---------------------------------------------------------------------------


@respx.mock
async def test_without_a_token_nothing_is_called() -> None:
    route = respx.get(f"{BASE}/agent_eligibility").mock(
        return_value=httpx.Response(200, json={"is_eligible": True})
    )
    for token in ("", "PLACEHOLDER_set_out_of_band"):
        with pytest.raises(MbaAdminError) as exc:
            await _client(token=token).eligibility(ENTITY)
        assert exc.value.kind == "not_configured" and isinstance(exc.value, MbaApiError)
    assert route.call_count == 0


@respx.mock
async def test_a_4xx_is_rejected_with_metas_standard_error_detail() -> None:
    respx.post(f"{BASE}/agent_config/skills").mock(
        return_value=httpx.Response(
            400,
            json={
                "title": "Bad Request",
                "detail": "skill exceeds 20000 chars",
                "status": 400,
            },
        )
    )
    with pytest.raises(MbaAdminError) as exc:
        await _client(token="SECRET").create_skill(
            ENTITY, {"title": "x", "description": "d", "skill": "s"}
        )
    assert exc.value.kind == "rejected" and exc.value.status == 400
    assert exc.value.detail == "skill exceeds 20000 chars"
    assert "SECRET" not in str(exc.value)


@respx.mock
async def test_a_404_is_rejected_and_carries_the_status_for_the_caller() -> None:
    respx.delete(f"{BASE}/agent_config/skills/gone").mock(
        return_value=httpx.Response(
            404, json={"title": "Not Found", "detail": "no such skill"}
        )
    )
    with pytest.raises(MbaAdminError) as exc:
        await _client().delete_skill(ENTITY, "gone")
    assert exc.value.kind == "rejected" and exc.value.status == 404


@respx.mock
async def test_intermittent_5xx_and_429_are_retried_with_backoff() -> None:
    sleeper = _Sleeper()
    route = respx.get(f"{BASE}/agent_config/skills").mock(
        side_effect=[
            httpx.Response(500, json={"title": "Server Error", "detail": "boom"}),
            httpx.Response(429, json={"title": "Too Many Requests", "detail": "rate"}),
            httpx.Response(200, json=[]),
        ]
    )
    assert await _client(sleeper=sleeper).list_skills(ENTITY) == []
    assert route.call_count == 3 and sleeper.delays == [0.5, 1.0]


@respx.mock
async def test_persistent_5xx_is_unavailable_after_the_attempts() -> None:
    route = respx.put(f"{BASE}/agent_config/settings").mock(
        return_value=httpx.Response(
            503, json={"title": "Unavailable", "detail": "down"}
        )
    )
    with pytest.raises(MbaAdminError) as exc:
        await _client().put_settings(ENTITY, {"rollout": {"enabled": False}})
    assert (
        exc.value.kind == "unavailable"
        and exc.value.status == 503
        and exc.value.attempts == 3
    )
    assert route.call_count == 3


@respx.mock
async def test_transport_errors_are_retried_on_idempotent_calls() -> None:
    """GET / PUT / DELETE son idempotentes: repetirlos no duplica nada."""
    route = respx.put(f"{BASE}/agent_config/business_info").mock(
        side_effect=[
            httpx.ReadTimeout("slow"),
            httpx.Response(200, json={"business_description": "Velas"}),
        ]
    )
    assert await _client().put_business_info(
        ENTITY, {"business_description": "Velas"}
    ) == {"business_description": "Velas"}
    assert route.call_count == 2


@respx.mock
async def test_a_transport_error_on_a_create_is_ambiguous_and_not_retried() -> None:
    """Un POST que crea (skill, FAQ, connector, tool, UI skill, allowlist) NO
    se reintenta a ciegas: Meta pudo haberlo creado (L-1) y un segundo POST
    duplicaría el recurso. D2.2 lo resuelve releyendo (GET + diff por título)."""
    route = respx.post(f"{BASE}/agent_config/faq").mock(
        side_effect=httpx.ReadTimeout("slow")
    )
    with pytest.raises(MbaAdminError) as exc:
        await _client().create_faq(ENTITY, {"question": "q", "answer": "a"})
    assert exc.value.kind == "ambiguous" and route.call_count == 1


@respx.mock
async def test_a_204_is_fine_but_a_2xx_that_is_not_json_or_has_the_wrong_shape_is_an_error() -> (
    None
):
    """Un 200 con HTML (proxy, login, cambio de forma) NO puede leerse como
    "no hay skills": D2.2 re-crearía todo. Solo el 204 sin cuerpo es ``{}``."""
    respx.delete(f"{BASE}/agent_config/faq/f1").mock(return_value=httpx.Response(204))
    respx.get(f"{BASE}/agent_eligibility").mock(
        return_value=httpx.Response(200, text="<html>login</html>")
    )
    respx.get(f"{BASE}/agent_config/skills").mock(
        return_value=httpx.Response(200, json={"weird": True})
    )
    respx.get(f"{BASE}/agent_config/business_info").mock(
        return_value=httpx.Response(200, json=[1, 2])
    )
    respx.get(f"{BASE}/agent_config/faq").mock(
        return_value=httpx.Response(200, json={"is_eligible": True})
    )
    c = _client()
    assert await c.delete_faq(ENTITY, "f1") is None
    for call in (
        c.eligibility(ENTITY),
        c.list_skills(ENTITY),
        c.get_business_info(ENTITY),
        c.list_faqs(ENTITY),
    ):
        with pytest.raises(MbaAdminError) as exc:
            await call
        assert exc.value.kind == "rejected" and exc.value.status == 200


@respx.mock
async def test_settings_get_accepts_the_bare_list_and_the_data_envelope() -> None:
    settings = {
        "agent_id": "ag_1",
        "channel": "whatsapp",
        "rollout": {"enabled": False},
    }
    respx.get(f"{BASE}/agent_config/settings").mock(
        side_effect=[
            httpx.Response(200, json=[settings]),
            httpx.Response(200, json={"data": [settings]}),
            httpx.Response(200, json=settings),
        ]
    )
    c = _client()
    assert await c.get_settings(ENTITY) == [settings]
    assert await c.get_settings(ENTITY) == [settings]
    assert await c.get_settings(ENTITY, agent_id="ag_1") == [settings]


@respx.mock
async def test_a_delete_that_hits_404_after_a_retry_counts_as_done() -> None:
    """El primer DELETE pudo borrar y el timeout tapó la respuesta: el
    reintento ve 404. Sin reintento previo, el 404 sí es un error del caller."""
    respx.delete(f"{BASE}/agent_config/skills/s1").mock(
        side_effect=[
            httpx.ReadTimeout("slow"),
            httpx.Response(404, json={"title": "Not Found", "detail": "no such skill"}),
        ]
    )
    assert await _client().delete_skill(ENTITY, "s1") is None
    respx.delete(f"{BASE}/agent_config/skills/s2").mock(
        return_value=httpx.Response(
            404, json={"title": "Not Found", "detail": "no such skill"}
        )
    )
    with pytest.raises(MbaAdminError) as exc:
        await _client().delete_skill(ENTITY, "s2")
    assert exc.value.status == 404 and exc.value.attempts == 1


@respx.mock
async def test_ui_skills_paging_stops_on_a_repeated_cursor_and_fails_past_the_page_cap() -> (
    None
):
    from src.plugins.mba.adapters import meta_admin

    a = {
        "id": "u1",
        "title": "a",
        "component_type": "image",
        "status": "enabled",
        "instruction": "i",
    }
    same = httpx.Response(
        200,
        json={
            "data": [a],
            "paging": {
                "cursors": {"after": "CUR"},
                "next": "https://api.facebook.com/n",
            },
        },
    )
    route = respx.get(f"{BASE}/agent-ui-skills").mock(return_value=same)
    # cursor repetido: la segunda página pide after=CUR y vuelve el mismo cursor → corta
    assert await _client().list_ui_skills(ENTITY) == [a, a]
    assert route.call_count == 2

    calls = iter(range(10_000))
    route.mock(
        side_effect=lambda req: httpx.Response(
            200,
            json={
                "data": [a],
                "paging": {
                    "cursors": {"after": f"C{next(calls)}"},
                    "next": "https://api.facebook.com/n",
                },
            },
        )
    )
    with pytest.raises(MbaAdminError) as exc:
        await _client().list_ui_skills(ENTITY)
    assert exc.value.kind == "unavailable" and "pagina" in exc.value.detail
    assert route.call_count == 2 + meta_admin._MAX_PAGES


# ---------------------------------------------------------------------------
# Fake
# ---------------------------------------------------------------------------


async def test_the_fake_records_calls_and_can_fail_on_demand() -> None:
    fake = FakeMbaAdmin()
    created = await fake.create_skill(
        ENTITY, {"title": "saludo", "description": "d", "skill": "s"}, agent_id="ag_1"
    )
    assert created == {
        "id": "skill-1",
        "title": "saludo",
        "description": "d",
        "skill": "s",
        "channel": "whatsapp",
        "status": "active",
    }
    assert await fake.list_skills(ENTITY) == [created]
    await fake.delete_skill(ENTITY, "skill-1")
    assert await fake.list_skills(ENTITY) == []
    assert fake.calls[0] == (
        "create_skill",
        ENTITY,
        {"title": "saludo", "description": "d", "skill": "s"},
        "ag_1",
    )
    fake.fail_with = MbaAdminError("unavailable", status=500, detail="x", attempts=3)
    with pytest.raises(MbaAdminError):
        await fake.eligibility(ENTITY)


async def test_the_fake_settings_behave_like_metas_partial_update() -> None:
    """PUT settings es PARCIAL (lo que se omite conserva su valor) y
    ``never_say_phrases`` es write-only: no vuelve en la respuesta ni en el GET."""
    fake = FakeMbaAdmin()
    first = await fake.put_settings(
        ENTITY,
        {
            "rollout": {"enabled": False},
            "ai_audience": "ALLOWLISTED_ONLY",
            "never_say_phrases": ["gratis"],
        },
    )
    assert (
        "never_say_phrases" not in first and first["ai_audience"] == "ALLOWLISTED_ONLY"
    )
    second = await fake.put_settings(
        ENTITY, {"handoff": {"enabled": True, "message_selection": "DEFAULT"}}
    )
    assert second["ai_audience"] == "ALLOWLISTED_ONLY" and second["rollout"] == {
        "enabled": False
    }
    assert second["handoff"] == {"enabled": True, "message_selection": "DEFAULT"}
    assert await fake.get_settings(ENTITY) == [second]
    assert fake.never_say_phrases[ENTITY] == ["gratis"]


async def test_the_fake_rejects_duplicates_where_meta_answers_409() -> None:
    fake = FakeMbaAdmin()
    await fake.create_faq(ENTITY, {"question": "¿Envían?", "answer": "Sí"})
    with pytest.raises(MbaAdminError) as exc:
        await fake.create_faq(ENTITY, {"question": "¿Envían?", "answer": "Otra"})
    assert exc.value.kind == "rejected" and exc.value.status == 409
    await fake.create_connector(
        ENTITY,
        {
            "name": "hubara",
            "description": "d",
            "base_url": "https://x",
            "auth_type": "API_KEY",
        },
    )
    with pytest.raises(MbaAdminError) as exc:
        await fake.create_connector(
            ENTITY,
            {
                "name": "hubara",
                "description": "d2",
                "base_url": "https://y",
                "auth_type": "NONE",
            },
        )
    assert exc.value.status == 409
