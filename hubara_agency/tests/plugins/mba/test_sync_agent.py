"""D2.2 — ``SyncAgent``: lleva la configuración autorada a Meta (plan de solo
lectura + apply con estado en el vault) sobre el ``FakeMbaAdmin`` de D2.1, que
replica las semánticas de Meta (ids, 409, PUT settings parcial, frases write-only).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from src.plugins.mba.adapters.meta_admin import FakeMbaAdmin, MbaAdminError
from src.plugins.mba.adapters.sync_state import SyncStateStore
from src.plugins.mba.domain.config import AgentFiles, MbaConfigDTO, build_agent_config
from src.plugins.mba.use_cases.sync_agent import SyncAgent
from tests.plugins.mba.test_sync_diff import _SKILLS, _YAML, API_KEY

ENTITY = "PHONE_777"


def _cfg(yaml_text: str = _YAML, skills: dict[str, str] | None = None) -> MbaConfigDTO:
    return build_agent_config(AgentFiles(agent_yaml=yaml_text, skills=skills or _SKILLS))


class _Configs:
    def __init__(self, cfg: MbaConfigDTO | None) -> None:
        self.cfg = cfg

    def __call__(self, agent_id: str) -> MbaConfigDTO | None:
        return self.cfg if agent_id == "sales" else None


def _agent(tmp_path: Path, *, cfg: MbaConfigDTO | None = None, fake: FakeMbaAdmin | None = None, enabled: bool = True, api_key: str = API_KEY):
    fake = fake or FakeMbaAdmin()
    store = SyncStateStore(tmp_path)
    uc = SyncAgent(
        admin=fake,
        state_store=store,
        load_config=_Configs(cfg or _cfg()),
        api_key=lambda: api_key,
        is_enabled=lambda: enabled,
        now_ms=lambda: 1_700_000_000_000,
    )
    return uc, fake, store


# ---------------------------------------------------------------------------
# Estado en el vault
# ---------------------------------------------------------------------------


def test_the_state_store_writes_one_json_per_agent_under_the_vault(tmp_path: Path) -> None:
    store = SyncStateStore(tmp_path)
    assert store.read("sales") == {}
    store.write("sales", {"ids": {"skills": {"persona": "s-1"}}})
    path = tmp_path / "_mba" / "sync" / "sales.json"
    assert path.is_file() and json.loads(path.read_text())["ids"]["skills"]["persona"] == "s-1"
    assert store.read("sales")["ids"]["skills"] == {"persona": "s-1"}
    with pytest.raises(ValueError):
        store.read("../etc")
    path.write_text("{corrupt")
    assert store.read("sales") == {}


# ---------------------------------------------------------------------------
# Plan (solo lectura)
# ---------------------------------------------------------------------------


async def test_plan_reads_meta_without_writing_anything(tmp_path: Path) -> None:
    uc, fake, store = _agent(tmp_path)
    plan = await uc.plan("sales")
    assert plan is not None and plan.blocked == () and len(plan.changes) == 9
    assert all(op in ("eligibility", "get_settings", "get_business_info", "list_faqs", "list_skills", "list_connectors", "list_connector_tools", "list_ui_skills") for op, *_ in fake.calls)
    assert store.read("sales") == {}
    assert await uc.plan("nope") is None


async def test_plan_reports_a_remote_outage_instead_of_an_empty_diff(tmp_path: Path) -> None:
    """Meta caída NO puede verse como "Meta está vacía" (crearía duplicados)."""
    uc, fake, _ = _agent(tmp_path)
    fake.fail_with = MbaAdminError("unavailable", status=503, detail="down", attempts=3)
    with pytest.raises(MbaAdminError):
        await uc.plan("sales")


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


async def test_first_apply_creates_everything_and_the_second_run_changes_nothing(tmp_path: Path) -> None:
    uc, fake, store = _agent(tmp_path)
    fake.settings[ENTITY] = {"agent_id": "ag_1", "channel": "whatsapp", "rollout": {"enabled": True}, "ai_audience": "EVERYONE"}
    out = await uc.apply("sales")
    assert out.applied is True and out.reason == "applied" and out.status == "ok"
    assert [r["action"] for r in out.results] == ["update", "create", "create", "create", "create", "create", "create", "create", "update"]
    assert all(r["ok"] for r in out.results)
    # Meta quedó con lo del workspace…
    assert [s["title"] for s in fake.skills[ENTITY]] == ["persona"]
    assert [t["name"] for t in fake.tools[(ENTITY, "connector-1")]] == ["search_products", "register_order"]
    assert fake.connectors[ENTITY][0]["auth_config"]["api_key"]["headers"][0]["value"] == API_KEY
    # …y rollout / ai_audience NO se tocaron (PUT parcial sin esos campos)
    assert fake.settings[ENTITY]["rollout"] == {"enabled": True} and fake.settings[ENTITY]["ai_audience"] == "EVERYONE"
    assert fake.never_say_phrases[ENTITY] == ["vos", "voy a averiguar"]
    # estado persistido: ids de lo creado + hashes de lo enviado
    state = store.read("sales")
    assert state["ids"]["skills"] == {"persona": "skill-1"} and state["ids"]["connector"] == {"hubara-commerce": "connector-1"}
    assert state["ids"]["connector_tools"] == {"search_products": "tool-1", "register_order": "tool-2"}
    assert state["sent"]["settings"]["never_say_phrases"]
    assert state["last_apply"]["status"] == "ok" and state["last_apply"]["at_ms"] == 1_700_000_000_000
    assert API_KEY not in json.dumps(state)

    again = await uc.apply("sales")
    assert again.applied is False and again.reason == "nothing_to_do"
    assert [s["title"] for s in fake.skills[ENTITY]] == ["persona"]


async def test_apply_updates_by_key_deletes_only_what_we_created_and_adopts_matching_foreign_items(tmp_path: Path) -> None:
    uc, fake, store = _agent(tmp_path)
    await uc.apply("sales")
    # alguien creó una skill a mano y una FAQ nuestra ya no está en el workspace
    await fake.create_skill(ENTITY, {"title": "hecha-a-mano", "description": "", "skill": "x"})
    fake.calls.clear()
    changed = _cfg(
        _YAML.replace('  - {question: "¿Envían a Cali?", answer: "Sí."}\n', ""),
        {"skills/persona.md": "---\ntitle: persona\ndescription: Siempre.\n---\n\nEres el asesor PREMIUM."},
    )
    uc2, _, _ = _agent(tmp_path, cfg=changed, fake=fake)
    out = await uc2.apply("sales")
    assert out.status == "ok"
    assert [(r["section"], r["label"], r["action"]) for r in out.results] == [
        ("faqs", "¿Envían a Cali?", "delete"),
        ("skills", "persona", "update"),
    ]
    assert [s["title"] for s in fake.skills[ENTITY]] == ["persona", "hecha-a-mano"]  # lo ajeno sigue
    assert [f["question"] for f in fake.faqs[ENTITY]] == ["¿Cuánto demora?"]
    assert "¿Envían a Cali?" not in store.read("sales")["ids"]["faqs"]


async def test_apply_is_refused_when_the_plan_is_blocked_disabled_or_stale(tmp_path: Path) -> None:
    with_placeholder = _cfg(_YAML.replace('instruction: "Envía el formulario."', 'instruction: "Flow <FLOW_ID>."'))
    uc, fake, store = _agent(tmp_path, cfg=with_placeholder)
    out = await uc.apply("sales")
    assert out.applied is False and out.reason == "blocked" and "placeholder:<FLOW_ID>" in out.blocked
    assert not [c for c in fake.calls if c[0].startswith(("create", "update", "put", "delete"))]
    assert store.read("sales")["last_attempt"]["reason"] == "blocked"

    uc, fake, _ = _agent(tmp_path, enabled=False)
    out = await uc.apply("sales")
    assert out.reason == "mba_disabled" and fake.calls == []

    uc, fake, _ = _agent(tmp_path)
    out = await uc.apply("sales", fingerprint="stale-fingerprint")
    assert out.reason == "plan_changed" and out.plan["fingerprint"] != "stale-fingerprint"
    assert not [c for c in fake.calls if c[0].startswith(("create", "update", "put", "delete"))]

    uc, fake, _ = _agent(tmp_path)
    assert (await uc.apply("nope")).reason == "agent_unknown"


async def test_a_rejected_item_is_recorded_and_the_rest_continues_but_an_outage_aborts(tmp_path: Path) -> None:
    class _Admin(FakeMbaAdmin):
        async def create_skill(self, entity_id, body, *, agent_id=None):
            raise MbaAdminError("rejected", status=400, detail="skill blocked by policy")

    uc, fake, store = _agent(tmp_path, fake=_Admin())
    out = await uc.apply("sales")
    assert out.applied is True and out.status == "partial"
    failed = [r for r in out.results if not r["ok"]]
    assert [(r["section"], r["error"]["kind"], r["error"]["detail"]) for r in failed] == [("skills", "rejected", "skill blocked by policy")]
    assert fake.connectors[ENTITY] and fake.faqs[ENTITY]  # lo demás sí se aplicó
    assert store.read("sales")["last_apply"]["status"] == "partial"

    class _Flaky(FakeMbaAdmin):
        async def create_connector(self, entity_id, body):
            raise MbaAdminError("unavailable", status=503, detail="down", attempts=3)

    uc, fake, store = _agent(tmp_path / "fresh-vault", fake=_Flaky())
    out = await uc.apply("sales")
    assert out.applied is True and out.status == "aborted"
    actions = [(r["section"], r["ok"], r.get("skipped")) for r in out.results]
    assert ("connector", False, None) in actions
    assert all(r["skipped"] == "aborted" for r in out.results if r["section"] in ("connector_tools", "ui_skills", "settings"))
    # lo creado antes del corte quedó registrado: el próximo run no lo duplica
    state = store.read("sales")
    assert state["ids"]["skills"] == {"persona": "skill-1"} and "connector" not in state["ids"]


async def test_a_second_apply_while_one_is_running_is_refused(tmp_path: Path) -> None:
    gate = asyncio.Event()

    class _Slow(FakeMbaAdmin):
        async def put_business_info(self, entity_id, body):
            await gate.wait()
            return await super().put_business_info(entity_id, body)

    uc, _, _ = _agent(tmp_path, fake=_Slow())
    first = asyncio.create_task(uc.apply("sales"))
    await asyncio.sleep(0.05)
    second = await uc.apply("sales")
    assert second.reason == "sync_in_progress"
    gate.set()
    assert (await first).reason == "applied"


async def test_a_remote_outage_before_applying_writes_nothing(tmp_path: Path) -> None:
    uc, fake, store = _agent(tmp_path)
    fake.fail_with = MbaAdminError("unavailable", status=503, detail="down", attempts=3)
    out = await uc.apply("sales")
    assert out.applied is False and out.reason == "remote_unavailable" and out.error == {"kind": "unavailable", "detail": "down", "status": 503}
    assert store.read("sales") == {}
