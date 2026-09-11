"""D2.2 — diff PURO entre la configuración autorada (los ``requests[]`` del
preview) y el estado remoto de Meta Business Agent → plan de sync.

Reglas que fija este test:
* Meta ya tiene lo mismo → plan sin cambios (idempotencia: correr dos veces = un cambio).
* Ítems por clave natural (question / title / name): faltante → create, distinto → update.
* Se BORRA solo lo que nosotros creamos (ids registrados en el estado de sync)
  y ya no está en el workspace; lo ajeno se lista como ``not_managed`` sin tocar.
* ``rollout.enabled``, ``ai_audience`` y la allowlist NUNCA viajan (D2.3).
* ``never_say_phrases`` es write-only: se compara con el hash de lo último enviado.
* Bloqueos (no se aplica nada): sin ``entity_id``, ``problems`` del preview,
  placeholders sin resolver, sin API key del connector.
"""

from __future__ import annotations

from dataclasses import replace

import json

from src.plugins.mba.domain.config import AgentFiles, build_agent_config
from src.plugins.mba.domain.sync import (
    MANAGED_SECTIONS,
    RemoteState,
    SyncPlan,
    api_key_fingerprint,
    body_hash,
    build_plan,
)

API_KEY = "hubara-key-1"

_YAML = """
id: sales
display_name: Asesor de Ventas
channel: whatsapp
entity_id: "PHONE_777"
skills: [persona]
settings:
  rollout_enabled: false
  ai_audience: ALLOWLISTED_ONLY
  handoff: {enabled: true, message_selection: CUSTOM, message: "Un colega te responde"}
  followup: {enabled: false, followup_interval_in_seconds: 900, message: null}
  never_say_phrases: [vos, "voy a averiguar"]
business_info:
  business_description: "Hubara, velas."
  payment_method: "Nequi."
  delivery_and_shipping: "Solo Colombia."
  return_policy: ""
  purchase_info: ""
  contact_info: {email: null, hours_of_operation: "America/Bogota", address: null}
faqs:
  - {question: "¿Cuánto demora?", answer: "1 a 2 días."}
  - {question: "¿Envían a Cali?", answer: "Sí."}
connector:
  name: hubara-commerce
  description: API de Hubara
  base_url: https://api.hubara.co/api/mba
  auth_type: API_KEY
  auth_header: X-API-Key
  requires_certificate: false
  customer_phone_param: customer_phone
  tools:
    - name: search_products
      method: GET
      description: Busca productos.
      params:
        q: {type: string, description: "Texto."}
      write: false
    - name: register_order
      method: POST
      description: Registra el pedido.
      params:
        ciudad: {type: string, description: "Ciudad.", required: true}
      write: true
ui_skills:
  - {title: request-shipping-details, component_type: flow, status: enabled, kind: static, instruction: "Envía el formulario.", flow_id: 951293630651590}
allowlist: ["+573001234567"]
"""
_SKILLS = {
    "skills/persona.md": "---\ntitle: persona\ndescription: Siempre.\n---\n\nEres el asesor."
}


def _cfg(yaml_text: str = _YAML, skills: dict[str, str] | None = None):
    return build_agent_config(
        AgentFiles(agent_yaml=yaml_text, skills=skills or _SKILLS)
    )


def _req(cfg, section: str, label: str) -> dict:
    return next(
        r.body for r in cfg.requests if r.section == section and r.label == label
    )


def _remote_in_sync(cfg) -> tuple[RemoteState, dict, dict]:
    """Estado remoto IGUAL al workspace + el estado de sync que lo registró."""
    con_body = _req(cfg, "connector", "hubara-commerce")
    tools = [
        dict(_req(cfg, "connector_tools", t.name), id=f"t-{i}")
        for i, t in enumerate(cfg.connector.tools)
    ]
    settings_body = _req(cfg, "settings", "settings")
    remote = RemoteState(
        settings={
            "agent_id": "ag_1",
            "channel": "whatsapp",
            "rollout": {"enabled": False},
            "ai_audience": "ALLOWLISTED_ONLY",
            "handoff": settings_body["handoff"],
            "followup": settings_body["followup"],
        },
        business_info=dict(
            _req(cfg, "business_info", "business_info"), extra_from_meta="x"
        ),
        faqs=tuple(
            dict(_req(cfg, "faqs", f.question), id=f"f-{i}", created_at=1)
            for i, f in enumerate(cfg.faqs)
        ),
        skills=tuple(
            dict(
                _req(cfg, "skills", s.title),
                id=f"s-{i}",
                channel="whatsapp",
                status="active",
            )
            for i, s in enumerate(cfg.skills)
        ),
        connectors=(
            {
                "id": "c-1",
                **{k: v for k, v in con_body.items() if k != "auth_config"},
                "connection_status": {"status": "ACTIVE"},
            },
        ),
        tools={"c-1": tuple(tools)},
        ui_skills=tuple(
            dict(_req(cfg, "ui_skills", u.title), id=f"u-{i}")
            for i, u in enumerate(cfg.ui_skills)
        ),
    )
    ids = {
        "faqs": {f.question: f"f-{i}" for i, f in enumerate(cfg.faqs)},
        "skills": {s.title: f"s-{i}" for i, s in enumerate(cfg.skills)},
        "connector": {"hubara-commerce": "c-1"},
        "connector_tools": {
            t.name: f"t-{i}" for i, t in enumerate(cfg.connector.tools)
        },
        "ui_skills": {u.title: f"u-{i}" for i, u in enumerate(cfg.ui_skills)},
    }
    sent = {
        "settings": {
            "never_say_phrases": body_hash(settings_body["never_say_phrases"])
        },
        "connector_key": {"hubara-commerce": api_key_fingerprint(API_KEY)},
    }
    return remote, ids, sent


def _actions(plan: SyncPlan) -> list[tuple[str, str, str]]:
    return [
        (op.section, op.label, op.action)
        for op in plan.ops
        if op.action not in ("noop", "skip")
    ]


# ---------------------------------------------------------------------------


def test_when_meta_already_matches_the_plan_has_no_changes() -> None:
    cfg = _cfg()
    remote, ids, sent = _remote_in_sync(cfg)
    plan = build_plan(cfg, remote, managed_ids=ids, sent_hashes=sent, api_key=API_KEY)
    assert plan.blocked == ()
    assert _actions(plan) == []
    assert (
        plan.counts["noop"] >= 7
    )  # business_info + 2 faqs + skill + connector + 2 tools + ui + settings
    assert {op.section for op in plan.ops} >= set(MANAGED_SECTIONS)
    assert plan.fingerprint  # estable: mismo plan → mismo fingerprint
    again = build_plan(cfg, remote, managed_ids=ids, sent_hashes=sent, api_key=API_KEY)
    assert again.fingerprint == plan.fingerprint


def test_an_empty_remote_creates_everything_in_send_order_without_rollout_or_allowlist() -> (
    None
):
    cfg = _cfg()
    plan = build_plan(
        cfg, RemoteState.empty(), managed_ids={}, sent_hashes={}, api_key=API_KEY
    )
    assert plan.blocked == ()
    sections = [s for s, _, _ in _actions(plan)]
    assert sections == [
        "business_info",
        "faqs",
        "faqs",
        "skills",
        "connector",
        "connector_tools",
        "connector_tools",
        "ui_skills",
        "settings",
    ]
    settings = next(op for op in plan.ops if op.section == "settings")
    assert settings.action == "update"
    assert "rollout" not in settings.body and "ai_audience" not in settings.body
    assert settings.body["never_say_phrases"] == ["vos", "voy a averiguar"]
    allow = [op for op in plan.ops if op.section == "allowlist"]
    assert allow and all(op.action == "skip" and op.reason == "d2.3" for op in allow)
    con = next(op for op in plan.ops if op.section == "connector")
    assert con.action == "create"
    assert (
        con.body["auth_config"]["api_key"]["headers"][0]["value"] == API_KEY
    )  # placeholder resuelto
    tool = next(op for op in plan.ops if op.section == "connector_tools")
    assert (
        tool.remote_id is None
        and tool.connector_label == "hubara-commerce"
        and tool.connector_remote_id is None
    )


def test_changed_items_are_updated_by_their_natural_key_and_new_ones_created() -> None:
    cfg = _cfg()
    remote, ids, sent = _remote_in_sync(cfg)
    changed = _cfg(
        _YAML.replace('answer: "1 a 2 días."', 'answer: "2 a 3 días."').replace(
            '- {question: "¿Envían a Cali?", answer: "Sí."}',
            '- {question: "¿Envían a Cali?", answer: "Sí."}\n  - {question: "¿Garantía?", answer: "48 h."}',
        ),
        {
            "skills/persona.md": "---\ntitle: persona\ndescription: Siempre.\n---\n\nEres el asesor PREMIUM."
        },
    )
    plan = build_plan(
        changed, remote, managed_ids=ids, sent_hashes=sent, api_key=API_KEY
    )
    assert _actions(plan) == [
        ("faqs", "¿Cuánto demora?", "update"),
        ("faqs", "¿Garantía?", "create"),
        ("skills", "persona", "update"),
    ]
    upd = next(op for op in plan.ops if op.label == "¿Cuánto demora?")
    assert upd.remote_id == "f-0" and upd.body == {
        "question": "¿Cuánto demora?",
        "answer": "2 a 3 días.",
    }


def test_only_items_we_created_are_deleted_and_foreign_ones_are_reported_untouched() -> (
    None
):
    cfg = _cfg()
    remote, ids, sent = _remote_in_sync(cfg)
    remote = RemoteState(
        **{
            **remote.__dict__,
            "skills": remote.skills
            + (
                {
                    "id": "s-foreign",
                    "title": "hecha-a-mano",
                    "description": "",
                    "skill": "x",
                },
            ),
            "faqs": remote.faqs,
        }
    )
    without_cali = _cfg(
        _YAML.replace('  - {question: "¿Envían a Cali?", answer: "Sí."}\n', "")
    )
    plan = build_plan(
        without_cali, remote, managed_ids=ids, sent_hashes=sent, api_key=API_KEY
    )
    assert _actions(plan) == [("faqs", "¿Envían a Cali?", "delete")]
    delete = next(op for op in plan.ops if op.action == "delete")
    assert delete.remote_id == "f-1" and delete.reason == "removed_from_workspace"
    foreign = next(op for op in plan.ops if op.label == "hecha-a-mano")
    assert (
        foreign.action == "noop"
        and foreign.reason == "not_managed"
        and foreign.remote_id == "s-foreign"
    )


def test_settings_resend_when_the_phrases_changed_even_if_meta_looks_equal() -> None:
    """``never_say_phrases`` no se puede leer de Meta: el hash de lo último
    enviado decide. Y ``rollout`` / ``ai_audience`` nunca viajan aunque difieran."""
    cfg = _cfg()
    remote, ids, sent = _remote_in_sync(cfg)
    plan = build_plan(
        cfg,
        remote,
        managed_ids=ids,
        sent_hashes={"connector_key": sent["connector_key"]},
        api_key=API_KEY,
    )
    assert _actions(plan) == [("settings", "settings", "update")]
    remote_on = RemoteState(
        **{
            **remote.__dict__,
            "settings": {
                **remote.settings,
                "rollout": {"enabled": True},
                "ai_audience": "EVERYONE",
            },
        }
    )
    plan = build_plan(
        cfg, remote_on, managed_ids=ids, sent_hashes=sent, api_key=API_KEY
    )
    assert _actions(plan) == []
    remote_handoff = RemoteState(
        **{
            **remote.__dict__,
            "settings": {
                **remote.settings,
                "handoff": {"enabled": False, "message_selection": "DEFAULT"},
            },
        }
    )
    plan = build_plan(
        cfg, remote_handoff, managed_ids=ids, sent_hashes=sent, api_key=API_KEY
    )
    assert _actions(plan) == [("settings", "settings", "update")]


def test_a_ui_skill_whose_component_type_changed_is_replaced_not_updated() -> None:
    """``PUT agent-ui-skills/{id}`` solo acepta title/status/instruction."""
    cfg = _cfg()
    remote, ids, sent = _remote_in_sync(cfg)
    changed = _cfg(
        _YAML.replace("component_type: flow", "component_type: cta_url").replace(
            ", flow_id: 951293630651590", ""
        )
    )
    plan = build_plan(
        changed, remote, managed_ids=ids, sent_hashes=sent, api_key=API_KEY
    )
    assert _actions(plan) == [("ui_skills", "request-shipping-details", "replace")]
    op = next(o for o in plan.ops if o.action == "replace")
    assert op.remote_id == "u-0" and op.body["component_type"] == "cta_url"
    changed = _cfg(
        _YAML.replace(
            'instruction: "Envía el formulario."', 'instruction: "Manda el formulario."'
        )
    )
    plan = build_plan(
        changed, remote, managed_ids=ids, sent_hashes=sent, api_key=API_KEY
    )
    op = next(o for o in plan.ops if o.section == "ui_skills")
    assert op.action == "update" and set(op.body) == {"title", "status", "instruction"}


def test_business_info_compares_only_the_fields_we_send() -> None:
    cfg = _cfg()
    remote, ids, sent = _remote_in_sync(cfg)
    plan = build_plan(cfg, remote, managed_ids=ids, sent_hashes=sent, api_key=API_KEY)
    assert next(o for o in plan.ops if o.section == "business_info").action == "noop"
    drift = RemoteState(
        **{
            **remote.__dict__,
            "business_info": {**remote.business_info, "payment_method": "Efectivo."},
        }
    )
    plan = build_plan(cfg, drift, managed_ids=ids, sent_hashes=sent, api_key=API_KEY)
    assert _actions(plan) == [("business_info", "business_info", "update")]


def test_the_plan_is_blocked_without_entity_id_with_problems_placeholders_or_without_the_api_key() -> (
    None
):
    cfg = _cfg()
    remote, ids, sent = _remote_in_sync(cfg)
    no_entity = _cfg(_YAML.replace('entity_id: "PHONE_777"', "entity_id: null"))
    plan = build_plan(
        no_entity, remote, managed_ids=ids, sent_hashes=sent, api_key=API_KEY
    )
    assert "entity_id_missing" in plan.blocked

    too_long = _cfg(
        skills={
            "skills/persona.md": "---\ntitle: persona\ndescription: Siempre.\n---\n\n"
            + "x" * 20_001
        }
    )
    plan = build_plan(
        too_long, remote, managed_ids=ids, sent_hashes=sent, api_key=API_KEY
    )
    assert any(p.startswith("problem:") and "20" in p for p in plan.blocked)

    with_placeholder = _cfg(
        _YAML.replace(
            'instruction: "Envía el formulario."',
            'instruction: "Flow <FLOW_ID> pantalla SHIPPING."',
        )
    )
    plan = build_plan(
        with_placeholder, remote, managed_ids=ids, sent_hashes=sent, api_key=API_KEY
    )
    assert "placeholder:<FLOW_ID>" in plan.blocked
    assert (
        all(op.action in ("noop", "skip") for op in plan.ops) or plan.blocked
    )  # bloqueado: nada se aplica

    # kebab (`<host-publico>`, el bug previo) también frena; `<cliente>` (una palabra) sigue siendo copy legítimo
    kebab = _cfg(
        _YAML.replace(
            'instruction: "Envía el formulario."',
            'instruction: "Base https://<host-publico>/x para <cliente>."',
        )
    )
    plan = build_plan(kebab, remote, managed_ids=ids, sent_hashes=sent, api_key=API_KEY)
    assert "placeholder:<host-publico>" in plan.blocked and not any(
        "<cliente>" in b for b in plan.blocked
    )

    plan = build_plan(cfg, remote, managed_ids=ids, sent_hashes=sent, api_key="")
    assert "connector_api_key_missing" in plan.blocked
    plan = build_plan(
        cfg,
        remote,
        managed_ids=ids,
        sent_hashes=sent,
        api_key="PLACEHOLDER_set_out_of_band",
    )
    assert "connector_api_key_missing" in plan.blocked


def test_the_plan_never_carries_the_api_key_in_its_fingerprint_or_summary() -> None:
    cfg = _cfg()
    plan = build_plan(
        cfg, RemoteState.empty(), managed_ids={}, sent_hashes={}, api_key=API_KEY
    )
    assert API_KEY not in plan.fingerprint
    summary = json.dumps(plan.summary(), ensure_ascii=False)
    assert API_KEY not in summary and "<HUBARA_MBA_API_KEY>" not in summary
    assert summary.count("create") >= 8


# ---------------------------------------------------------------------------
# Revisión independiente (M-1, M-3, M-4, L-4, L-8)
# ---------------------------------------------------------------------------


def test_clearing_a_business_info_field_in_the_workspace_reaches_meta() -> None:
    """M-1: el preview omite los campos vacíos, pero el sync manda el bloque
    COMPLETO (vacíos como "") para que Meta converja con el workspace."""
    cfg = _cfg()
    remote, ids, sent = _remote_in_sync(cfg)
    remote_with_policy = RemoteState(
        **{
            **remote.__dict__,
            "business_info": {
                **remote.business_info,
                "return_policy": "Garantía vieja.",
            },
        }
    )
    plan = build_plan(
        cfg, remote_with_policy, managed_ids=ids, sent_hashes=sent, api_key=API_KEY
    )
    assert _actions(plan) == [("business_info", "business_info", "update")]
    op = next(o for o in plan.ops if o.section == "business_info")
    assert op.body["return_policy"] == "" and op.body["purchase_info"] == ""
    assert op.body["contact_info"] == {
        "hours_of_operation": "America/Bogota"
    }  # los null no viajan
    # y un remoto con los mismos valores (vacíos ausentes o "") es noop
    same = RemoteState(
        **{
            **remote.__dict__,
            "business_info": {
                **remote.business_info,
                "return_policy": "",
                "contact_info": {"hours_of_operation": "America/Bogota", "email": None},
            },
        }
    )
    plan = build_plan(cfg, same, managed_ids=ids, sent_hashes=sent, api_key=API_KEY)
    assert _actions(plan) == []


def test_rotating_the_connector_api_key_updates_the_connector() -> None:
    """M-3: Meta no devuelve la key; el fingerprint de lo último enviado decide."""
    cfg = _cfg()
    remote, ids, sent = _remote_in_sync(cfg)
    plan = build_plan(
        cfg, remote, managed_ids=ids, sent_hashes=sent, api_key="rotated-key-2"
    )
    assert _actions(plan) == [("connector", "hubara-commerce", "update")]
    op = next(o for o in plan.ops if o.section == "connector")
    assert (
        op.reason == "api_key_rotated"
        and op.body["auth_config"]["api_key"]["headers"][0]["value"] == "rotated-key-2"
    )
    assert (
        "rotated-key-2" not in json.dumps(plan.summary())
        and "rotated-key-2" not in plan.fingerprint
    )


def test_a_foreign_item_that_matches_by_key_is_updated_but_never_deleted() -> None:
    """M-4: adoptar por update NO da permiso de borrar: solo se borra lo creado por nosotros."""
    cfg = _cfg()
    remote, ids, sent = _remote_in_sync(cfg)
    foreign_faq = {
        "id": "f-foreign",
        "question": "¿Envían a Cali?",
        "answer": "Depende.",
    }
    remote = RemoteState(**{**remote.__dict__, "faqs": (remote.faqs[0], foreign_faq)})
    ids = {**ids, "faqs": {"¿Cuánto demora?": "f-0"}}
    plan = build_plan(cfg, remote, managed_ids=ids, sent_hashes=sent, api_key=API_KEY)
    assert _actions(plan) == [("faqs", "¿Envían a Cali?", "update")]
    without = _cfg(
        _YAML.replace('  - {question: "¿Envían a Cali?", answer: "Sí."}\n', "")
    )
    plan = build_plan(
        without, remote, managed_ids=ids, sent_hashes=sent, api_key=API_KEY
    )
    assert _actions(plan) == []
    assert (
        next(o for o in plan.ops if o.label == "¿Envían a Cali?").reason
        == "not_managed"
    )


def test_only_uppercase_placeholders_block_and_remote_duplicates_are_reported() -> None:
    """L-4: `<cliente>` en el markdown de una skill no es un placeholder.
    L-8: dos ítems remotos con la misma clave: el segundo se lista, no se pisa."""
    cfg = _cfg(
        skills={
            "skills/persona.md": "---\ntitle: persona\ndescription: Siempre.\n---\n\nSaluda por su nombre: <cliente> y <Nombre>."
        }
    )
    remote, ids, sent = _remote_in_sync(cfg)
    plan = build_plan(
        cfg, RemoteState.empty(), managed_ids={}, sent_hashes={}, api_key=API_KEY
    )
    assert plan.blocked == ()
    dup = {"id": "s-dup", "title": "persona", "description": "otra", "skill": "otra"}
    remote = RemoteState(**{**remote.__dict__, "skills": remote.skills + (dup,)})
    plan = build_plan(cfg, remote, managed_ids=ids, sent_hashes=sent, api_key=API_KEY)
    dups = [o for o in plan.ops if o.remote_id == "s-dup"]
    assert dups and dups[0].action == "noop" and dups[0].reason == "duplicate_remote"


def test_a_flow_ui_skill_whose_flow_id_changed_is_replaced_and_updates_never_carry_flow_id() -> (
    None
):
    """Meta: `flow_id` es obligatorio al crear un `flow` y el PUT no lo acepta →
    cambiarlo es borrar y recrear; una edición de texto no debe mandarlo."""
    cfg = _cfg()
    remote = RemoteState.empty()
    remote = replace(
        remote,
        ui_skills=(
            {
                "id": "ui-1",
                "title": "request-shipping-details",
                "component_type": "flow",
                "status": "enabled",
                "instruction": "Envía el formulario.",
                "flow_id": 111,
            },
        ),
    )
    ids = {"ui_skills": {"request-shipping-details": "ui-1"}}
    plan = build_plan(cfg, remote, managed_ids=ids, sent_hashes={}, api_key=API_KEY)
    op = next(
        o
        for o in plan.ops
        if o.section == "ui_skills" and o.label == "request-shipping-details"
    )
    assert (op.action, op.reason) == ("replace", "flow_id_changed") and op.body[
        "flow_id"
    ] == 951293630651590

    remote2 = replace(
        remote,
        ui_skills=(
            {**remote.ui_skills[0], "flow_id": 951293630651590, "instruction": "viejo"},
        ),
    )
    plan2 = build_plan(cfg, remote2, managed_ids=ids, sent_hashes={}, api_key=API_KEY)
    op2 = next(
        o
        for o in plan2.ops
        if o.section == "ui_skills" and o.label == "request-shipping-details"
    )
    assert op2.action == "update" and "flow_id" not in op2.body


def test_followup_off_matches_metas_null_so_settings_do_not_update_forever() -> None:
    """Visto en prod: Meta guarda el followup apagado como ``null``; nosotros
    mandamos ``{"enabled": false}``. Eso no es un cambio: sin este caso el plan
    hacía un PUT de settings en CADA sync."""
    cfg = _cfg()
    remote, ids, sent = _remote_in_sync(cfg)
    as_meta = RemoteState(
        **{**remote.__dict__, "settings": {**remote.settings, "followup": None}}
    )
    plan = build_plan(cfg, as_meta, managed_ids=ids, sent_hashes=sent, api_key=API_KEY)
    assert _actions(plan) == []
    # pero si NOSOTROS encendemos el followup, sí viaja
    on = _cfg(_YAML.replace("followup: {enabled: false,", "followup: {enabled: true,"))
    plan_on = build_plan(
        on, as_meta, managed_ids=ids, sent_hashes=sent, api_key=API_KEY
    )
    assert _actions(plan_on) == [("settings", "settings", "update")]
