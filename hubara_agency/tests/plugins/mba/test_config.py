"""Tests del dominio del plugin ``mba``: agente autorado (agent.yaml + skills/*.md)
→ configuración de Meta Business Agent con los requests HTTP literales.

La función bajo prueba es PURA: recibe el contenido de los archivos y devuelve el
DTO. Sin I/O. El test de integración de abajo carga el agente REAL ``sales``.
"""

from __future__ import annotations

import json
import re

from src.plugins.mba.domain.config import (
    SKILL_CHAR_LIMIT,
    AgentFiles,
    build_agent_config,
)
from src.plugins.mba.service import list_agents, load_agent

_YAML = """
id: sales
display_name: Asesor de Ventas
role: Ventas por WhatsApp
channel: whatsapp
entity_id: null
skills: [persona-y-tono, reglas-operativas]
settings:
  rollout_enabled: false
  ai_audience: ALLOWLISTED_ONLY
  handoff: {enabled: true, message_selection: CUSTOM, message: "Un colega te responde 🤍"}
  followup: {enabled: false, followup_interval_in_seconds: 900, message: null}
  never_say_phrases: [vos, vos, "voy a averiguar"]
business_info:
  business_description: "Hubara, velas."
  payment_method: "Nequi 3229041190."
  delivery_and_shipping: "Solo Colombia."
  return_policy: ""
  purchase_info: ""
  contact_info: {email: null, hours_of_operation: "America/Bogota", address: null}
faqs:
  - {question: "¿Cuánto demora?", answer: "1 a 2 días."}
connector:
  name: hubara-commerce
  description: API de Hubara
  base_url: https://<host-publico>/api/mba
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
        limit: {type: integer, description: "Máximo."}
      write: false
      notes: Lectura.
    - name: register_order
      method: POST
      description: Registra el pedido.
      params:
        items:
          type: array
          description: "Items."
          required: true
          items: {type: object, required: [handle], properties: {handle: {type: string, description: "Handle."}}}
        ciudad: {type: string, description: "Ciudad.", required: true}
      write: true
      notes: Escritura idempotente.
ui_skills:
  - {title: request-shipping-details, component_type: flow, status: enabled, kind: static, instruction: "Envía el formulario."}
  - {title: present-products, component_type: carousel_quick_reply, status: enabled, kind: dynamic, instruction: "Carrusel."}
allowlist: ["+573001112233", "+573004445566"]
not_in_mba:
  - {source: react_to_message, reason: "Sin componente equivalente."}
"""

_SKILLS = {
    "skills/persona-y-tono.md": "---\ntitle: persona-y-tono\ndescription: Aplicar siempre.\n---\n\n# Eres el asesor\n\nTexto.",
    "skills/reglas-operativas.md": "---\ntitle: reglas-operativas\ndescription: Cada turno.\n---\n\nUsa search_products y register_order.",
}


def _files(yaml_text: str = _YAML, skills: dict[str, str] | None = None) -> AgentFiles:
    return AgentFiles(
        agent_yaml=yaml_text, skills=_SKILLS if skills is None else skills
    )


_SEND_ORDER = [
    "business_info",
    "faqs",
    "skills",
    "connector",
    "connector_tools",
    "ui_skills",
    "settings",
    "allowlist",
]


def test_skills_follow_declared_order_and_carry_provenance() -> None:
    cfg = build_agent_config(
        _files(), workspace="hubara_agency/src/plugins/mba/agents/sales"
    )
    assert cfg.agent_id == "sales" and cfg.channel == "whatsapp"
    assert cfg.workspace == "hubara_agency/src/plugins/mba/agents/sales"
    assert [s.title for s in cfg.skills] == ["persona-y-tono", "reglas-operativas"]
    persona = cfg.skills[0]
    # el body es el markdown SIN el front-matter, tal cual viaja
    assert persona.skill.startswith("# Eres el asesor")
    assert persona.description == "Aplicar siempre."
    assert (
        persona.char_count == len(persona.skill)
        and persona.char_limit == SKILL_CHAR_LIMIT
    )
    assert persona.sources == ("skills/persona-y-tono.md",)
    assert cfg.problems == ()


def test_missing_file_wrong_title_and_over_limit_are_reported_not_hidden() -> None:
    skills = {
        "skills/persona-y-tono.md": "---\ntitle: Persona Y Tono\ndescription: d\n---\n\n"
        + "x" * (SKILL_CHAR_LIMIT + 1),
    }
    cfg = build_agent_config(_files(skills=skills))
    joined = "\n".join(cfg.problems)
    assert "reglas-operativas" in joined  # declarada en agent.yaml, sin archivo
    assert "Persona Y Tono" in joined  # título fuera del formato de Meta
    assert "20.000" in joined or "20000" in joined  # excede el límite
    persona = next(s for s in cfg.skills if s.sources == ("skills/persona-y-tono.md",))
    assert persona.over_limit is True


def test_ui_skill_component_type_outside_meta_enum_is_a_problem() -> None:
    bad = _YAML.replace("component_type: flow", "component_type: form")
    cfg = build_agent_config(_files(bad))
    assert any("form" in p and "component_type" in p for p in cfg.problems)


def test_requests_follow_meta_schemas_in_send_order() -> None:
    cfg = build_agent_config(_files())
    reqs = cfg.requests
    assert [r.step for r in reqs] == list(range(1, len(reqs) + 1))
    seen: list[str] = []
    for r in reqs:
        assert r.url.startswith("https://api.facebook.com/{entity_id}/")
        assert r.headers["X-API-Version"] == "2.0.0"
        if not seen or seen[-1] != r.section:
            seen.append(r.section)
    assert seen == _SEND_ORDER

    skill = next(
        r for r in reqs if r.section == "skills" and r.label == "persona-y-tono"
    )
    assert skill.body == {
        "title": "persona-y-tono",
        "description": "Aplicar siempre.",
        "skill": cfg.skills[0].skill,
    }

    bi = next(r for r in reqs if r.section == "business_info")
    assert bi.method == "PUT"
    assert set(bi.body) == {
        "business_description",
        "payment_method",
        "delivery_and_shipping",
        "contact_info",
    }
    assert bi.body["contact_info"] == {"hours_of_operation": "America/Bogota"}

    faq = next(r for r in reqs if r.section == "faqs")
    assert faq.body == {"question": "¿Cuánto demora?", "answer": "1 a 2 días."}

    st = next(r for r in reqs if r.section == "settings")
    assert st.body["rollout"] == {"enabled": False}
    assert st.body["followup"] == {"enabled": False}
    assert st.body["handoff"] == {
        "enabled": True,
        "message_selection": "CUSTOM",
        "message": "Un colega te responde 🤍",
    }
    assert st.body["never_say_phrases"] == [
        "vos",
        "voy a averiguar",
    ]  # sin duplicados, en orden

    con = next(r for r in reqs if r.section == "connector")
    assert con.body["auth_config"]["api_key"]["headers"] == [
        {"field_name": "X-API-Key", "value": "<HUBARA_MBA_API_KEY>", "prefix": ""}
    ]

    tools = {r.label: r for r in reqs if r.section == "connector_tools"}
    search = tools["search_products"].body["request_definition"]
    assert search["method"] == "GET" and search["path"] == "/tools/search_products"
    assert search["query_parameters"]["customer_phone"]["binding"] == {
        "kind": "macro",
        "macro": "WHATSAPP_PHONE_NUMBER",
    }
    assert search["query_parameters"]["limit"] == {
        "type": "integer",
        "description": "Máximo.",
        "required": False,
    }
    assert "body" not in search
    reg = tools["register_order"].body["request_definition"]
    assert reg["method"] == "POST" and reg["query_parameters"] == {}
    params = reg["body"]["params"]
    assert params["customer_phone"]["binding"]["macro"] == "WHATSAPP_PHONE_NUMBER"
    # arrays: el schema de los elementos viaja como JSON string (formato de la doc de Meta)
    assert params["items"]["type"] == "array"
    assert json.loads(params["items"]["items"])["required"] == ["handle"]
    assert reg["body"]["required"] == ["customer_phone", "items", "ciudad"]

    ui = next(
        r for r in reqs if r.section == "ui_skills" and r.label == "present-products"
    )
    assert ui.body == {
        "title": "present-products",
        "component_type": "carousel_quick_reply",
        "status": "enabled",
        "instruction": "Carrusel.",
    }

    allow = [r for r in reqs if r.section == "allowlist"]
    assert [r.body for r in allow] == [
        {"consumer_phone_number": "+573001112233"},
        {"consumer_phone_number": "+573004445566"},
    ]


def test_connector_and_ui_metadata_survive_for_the_dashboard() -> None:
    cfg = build_agent_config(_files())
    assert cfg.connector is not None and cfg.connector.name == "hubara-commerce"
    reg = next(t for t in cfg.connector.tools if t.name == "register_order")
    assert (
        reg.write is True
        and reg.method == "POST"
        and reg.path == "/tools/register_order"
    )
    assert reg.body_parameters == ("items", "ciudad") and reg.query_parameters == ()
    ui = {u.title: u for u in cfg.ui_skills}
    assert (
        ui["present-products"].kind == "dynamic"
        and ui["request-shipping-details"].kind == "static"
    )
    assert cfg.excluded[0].source == "react_to_message"
    assert {e.section for e in cfg.endpoints} >= set(_SEND_ORDER)


# ── El agente REAL ─────────────────────────────────────────────────────

_HUBARA_ONLY = re.compile(
    r"load_skill|SOUL\.md|IDENTITY\.md|AGENTS\.md|TOOLS\.md|USER\.md|SCRIPT\.md|\[SISTEMA|"
    r"\[DATOS DEL PEDIDO|reasoning_content|tool_result|intro_text|Active Skill|system prompt|"
    r"present_products|present_variant_picker|present_product_detail|present_product_gallery|"
    r"present_order_confirmation|send_quick_replies|send_cta_url|send_contact_card|react_to_message|"
    r"exoclaw|Temporal|DeepSeek|memory/|COMPRA_EXITOSA|CONFIRMADO_SIN_DATOS|CONFIRMADO_PAGO_PENDIENTE|"
    r"ghost|⛔|\\n\\n"
)


def _param_names(node: object) -> set[str]:
    """Claves de `params`/`properties` (recursivo; `items` viaja como JSON string)."""
    out: set[str] = set()
    if isinstance(node, str):
        try:
            return _param_names(json.loads(node))
        except ValueError:
            return out
    if isinstance(node, dict):
        for key in ("params", "properties", "query_parameters"):
            if isinstance(node.get(key), dict):
                out |= set(node[key])
        for v in node.values():
            out |= _param_names(v)
    return out


def test_real_sales_agent_is_clean_and_only_names_tools_mba_has(monkeypatch) -> None:
    for k, v in _FULL_ENV.items():  # el entorno del tenant (SSM) resuelve los `${...}`
        monkeypatch.setenv(k, v)
    assert [a.id for a in list_agents()] == ["sales"]
    cfg = load_agent("sales")
    assert cfg is not None
    assert cfg.problems == ()
    assert len(cfg.skills) == 9 and all(not s.over_limit for s in cfg.skills)
    assert cfg.connector is not None and len(cfg.connector.tools) == 9
    assert len(cfg.ui_skills) == 9 and len(cfg.faqs) >= 8
    declared = {t.name for t in cfg.connector.tools}
    # snake_case legítimo = las 9 tools + los parámetros que Meta ve en su request_definition
    known = set(declared)
    for r in cfg.requests:
        if r.section == "connector_tools":
            known |= _param_names(r.body["request_definition"])
    texts = {s.title: s.skill + "\n" + s.description for s in cfg.skills}
    for title, text in texts.items():
        hit = _HUBARA_ONLY.search(text)
        assert (
            hit is None
        ), f"{title}: referencia a algo que MBA no tiene: {hit.group(0)!r}"
        for name in set(re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", text)):
            assert (
                name in known
            ), f"{title}: menciona `{name}`, que no es una tool ni un parámetro de MBA"
    # y las 9 tools se explican en algún skill
    mentioned = set(re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", "\n".join(texts.values())))
    assert declared <= mentioned
    # los requests reales: 1 business_info + N faqs + 9 skills + 1 connector + 9 tools + 9 ui + 1 settings + N allowlist
    assert len(cfg.requests) == 1 + len(cfg.faqs) + 9 + 1 + 9 + 9 + 1 + len(
        cfg.allowlist
    )
    assert cfg.workspace == "hubara_agency/src/plugins/mba/agents/sales"


# ---------------------------------------------------------------------------
# D3.1 — valores por tenant (`${VAR}`) resueltos desde el entorno que Terraform
# materializa en SSM. Sin valor → placeholder `<VAR>` (la guarda del sync lo
# frena) + problem visible en la sección. Nunca un valor a mano en git.
# ---------------------------------------------------------------------------

_ENV_YAML = """
id: sales
entity_id: "${WHATSAPP_PHONE_NUMBER_ID}"
skills: []
settings:
  rollout_enabled: false
  ai_audience: ALLOWLISTED_ONLY
business_info:
  business_description: "Hubara, velas."
connector:
  name: hubara-commerce
  description: API de Hubara
  base_url: ${HUBARA_PUBLIC_API_URL}/api/mba
  auth_type: API_KEY
  auth_header: X-API-Key
  requires_certificate: false
  customer_phone_param: customer_phone
  tools: []
ui_skills:
  - {title: request-shipping-details, component_type: flow, status: enabled, kind: static, instruction: "Flow id ${META_FLOW_ID_SHIPPING} y wa.me/${MBA_ADVISOR_PHONE}."}
allowlist: "${MBA_CUSTOMER_ALLOWLIST}"
"""

_FULL_ENV = {
    "WHATSAPP_PHONE_NUMBER_ID": "1234091093112024",
    "HUBARA_PUBLIC_API_URL": "https://api.example.test",
    "META_FLOW_ID_SHIPPING": "951293630651590",
    "MBA_ADVISOR_PHONE": "573001234567",  # dígitos: va dentro de un enlace wa.me
    "MBA_CUSTOMER_ALLOWLIST": "+573001234567, +573009876543",
}
_TENANT_VARS = sorted(_FULL_ENV)


def test_tenant_values_come_from_the_environment_not_from_git() -> None:
    cfg = build_agent_config(AgentFiles(agent_yaml=_ENV_YAML, skills={}), env=_FULL_ENV)
    assert cfg.problems == ()
    assert cfg.entity_id == "1234091093112024"
    assert (
        cfg.connector is not None
        and cfg.connector.base_url == "https://api.example.test/api/mba"
    )
    assert (
        cfg.ui_skills[0].instruction == "Flow id 951293630651590 y wa.me/573001234567."
    )
    # la lista cerrada es UNA sola (MBA_CUSTOMER_ALLOWLIST, CSV E.164): la misma que gobierna el connector
    assert cfg.allowlist == ("+573001234567", "+573009876543")
    assert [r.body for r in cfg.requests if r.section == "allowlist"] == [
        {"consumer_phone_number": "+573001234567"},
        {"consumer_phone_number": "+573009876543"},
    ]


def test_unset_or_placeholder_values_stay_blocking_and_are_reported() -> None:
    # SSM sin valor real = el placeholder de Terraform; ausente = igual de inválido.
    cfg = build_agent_config(
        AgentFiles(agent_yaml=_ENV_YAML, skills={}),
        env={
            "META_FLOW_ID_SHIPPING": "PLACEHOLDER_set_out_of_band",
            "MBA_ADVISOR_PHONE": "  ",
        },
    )
    assert (
        cfg.entity_id is None
    )  # sin número onboardeado NO se llama a Meta (entity_id_missing)
    assert (
        cfg.connector is not None
        and cfg.connector.base_url == "<HUBARA_PUBLIC_API_URL>/api/mba"
    )
    assert (
        cfg.ui_skills[0].instruction
        == "Flow id <META_FLOW_ID_SHIPPING> y wa.me/<MBA_ADVISOR_PHONE>."
    )
    assert cfg.allowlist == ("<MBA_CUSTOMER_ALLOWLIST>",)
    # un problem por variable, ordenado, nombrando la variable (es lo que el operador busca en SSM)
    assert len(cfg.problems) == len(_TENANT_VARS)
    for var, problem in zip(_TENANT_VARS, cfg.problems, strict=True):
        assert f"${{{var}}}" in problem


def test_without_env_nothing_is_resolved_and_literal_yaml_is_untouched() -> None:
    plain = build_agent_config(AgentFiles(agent_yaml=_YAML, skills=_SKILLS))
    assert plain.problems == () and plain.allowlist == (
        "+573001112233",
        "+573004445566",
    )
    unresolved = build_agent_config(AgentFiles(agent_yaml=_ENV_YAML, skills={}))
    assert (
        len(unresolved.problems) == len(_TENANT_VARS) and unresolved.entity_id is None
    )


def test_real_sales_agent_resolves_every_tenant_value_from_the_environment(
    monkeypatch,
) -> None:
    for k, v in _FULL_ENV.items():
        monkeypatch.setenv(k, v)
    cfg = load_agent("sales")
    assert cfg is not None
    assert cfg.problems == ()
    assert cfg.entity_id == "1234091093112024"
    assert (
        cfg.connector is not None
        and cfg.connector.base_url == "https://api.example.test/api/mba"
    )
    assert cfg.allowlist == ("+573001234567", "+573009876543")
    # `<VAR>` (lo que frena el sync) y `<host-publico>` (kebab, que NO lo frenaba): ninguno debe quedar
    leftovers = {
        m
        for r in cfg.requests
        for m in re.findall(
            r"<[A-Z][A-Z0-9_]{2,}>|<[a-z]+(?:-[a-z]+)+>",
            json.dumps(r.body, ensure_ascii=False),
        )
        if m != "<HUBARA_MBA_API_KEY>"
    }
    assert leftovers == set(), f"placeholders a mano en agent.yaml: {sorted(leftovers)}"


def test_real_sales_agent_without_environment_names_what_the_operator_must_set(
    monkeypatch,
) -> None:
    for k in _FULL_ENV:
        monkeypatch.delenv(k, raising=False)
    cfg = load_agent("sales")
    assert cfg is not None
    assert cfg.entity_id is None
    assert [
        v for v in _TENANT_VARS if any(f"${{{v}}}" in p for p in cfg.problems)
    ] == _TENANT_VARS


def test_values_that_would_break_the_yaml_are_refused_not_injected() -> None:
    hostile = {
        **_FULL_ENV,
        "HUBARA_PUBLIC_API_URL": 'https://x"\nallowlist: ["+573000000001"]\n#',
    }
    cfg = build_agent_config(AgentFiles(agent_yaml=_ENV_YAML, skills={}), env=hostile)
    assert (
        cfg.connector is not None
        and cfg.connector.base_url == "<HUBARA_PUBLIC_API_URL>/api/mba"
    )
    assert cfg.allowlist == (
        "+573001234567",
        "+573009876543",
    )  # la estructura NO cambió
    assert (
        len(cfg.problems) == 1
        and "${HUBARA_PUBLIC_API_URL}" in cfg.problems[0]
        and "caracteres" in cfg.problems[0]
    )


def test_env_value_never_returns_the_ssm_placeholder() -> None:
    from src.plugins.mba.domain.config import env_value

    assert (
        env_value(
            {"WHATSAPP_PHONE_NUMBER_ID": "PLACEHOLDER_set_out_of_band"},
            "WHATSAPP_PHONE_NUMBER_ID",
        )
        == ""
    )
    assert env_value({}, "WHATSAPP_PHONE_NUMBER_ID") == ""
    assert (
        env_value({"WHATSAPP_PHONE_NUMBER_ID": " 1234 "}, "WHATSAPP_PHONE_NUMBER_ID")
        == "1234"
    )
