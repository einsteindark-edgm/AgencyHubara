"""Dominio del plugin ``mba``: agente autorado → configuración de Meta Business Agent.

Entrada PURA: el contenido de ``agent.yaml`` y de ``skills/<title>.md`` del
agente (ver ``agents/<id>/``). Salida: ``MbaConfigDTO`` con la forma EXACTA de
los endpoints de configuración de MBA y, sobre todo, ``requests``: las llamadas
HTTP literales (método, URL, headers, body) en el orden en que se enviarían.

Sin I/O, sin vendors, sin fastapi (P-29: routers delgados, lógica acá). Lo que
no se pueda mandar tal cual (título fuera de formato, skill >20k, componente de
UI desconocido) se reporta en ``problems`` en vez de esconderse.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

import yaml

SKILL_CHAR_LIMIT = 20000
SKILL_DESCRIPTION_LIMIT = 1024
SKILL_TITLE_LIMIT = 64
_SKILL_TITLE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_FRONT_MATTER = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*\n?", re.DOTALL)

# Enum oficial de component_type (POST /{entity_id}/agent-ui-skills).
UI_COMPONENT_TYPES = frozenset(
    {
        "carousel_quick_reply",
        "carousel_url",
        "cta_url",
        "flow",
        "image",
        "interactive_list",
        "interactive_reply_buttons",
        "location",
        "location_request",
    }
)
TOOL_METHODS = ("GET", "POST")

MBA_BASE_URL = "https://api.facebook.com"
MBA_API_VERSION = "2.0.0"
CUSTOMER_PHONE_MACRO = "WHATSAPP_PHONE_NUMBER"
CONNECTOR_API_KEY_PLACEHOLDER = "<HUBARA_MBA_API_KEY>"
_MBA_HEADERS: dict[str, str] = {
    "Authorization": "Bearer <META_ACCESS_TOKEN>",
    "X-API-Version": MBA_API_VERSION,
    "Content-Type": "application/json",
}
_BI_TEXT_FIELDS = (
    "business_description",
    "payment_method",
    "delivery_and_shipping",
    "return_policy",
    "purchase_info",
)


@dataclass(frozen=True)
class AgentFiles:
    """Entrada pura: ``agent.yaml`` + ``{"skills/<title>.md": texto}``."""

    agent_yaml: str
    skills: Mapping[str, str]


@dataclass(frozen=True)
class MbaSkillDTO:
    title: str
    description: str
    skill: str
    char_count: int
    char_limit: int
    over_limit: bool
    sources: tuple[str, ...]


@dataclass(frozen=True)
class MbaFaqDTO:
    question: str
    answer: str
    source: str


@dataclass(frozen=True)
class MbaContactInfoDTO:
    email: str | None
    hours_of_operation: str | None
    address: str | None


@dataclass(frozen=True)
class MbaBusinessInfoDTO:
    business_description: str
    payment_method: str
    delivery_and_shipping: str
    return_policy: str
    purchase_info: str
    contact_info: MbaContactInfoDTO
    sources: tuple[str, ...]


@dataclass(frozen=True)
class MbaPhraseDTO:
    phrase: str
    source: str


@dataclass(frozen=True)
class MbaHandoffDTO:
    enabled: bool
    message: str | None
    message_selection: str  # DEFAULT | AGENT | CUSTOM


@dataclass(frozen=True)
class MbaFollowupDTO:
    enabled: bool
    followup_interval_in_seconds: int
    message: str | None


@dataclass(frozen=True)
class MbaSettingsDTO:
    rollout_enabled: bool
    ai_audience: str  # EVERYONE | ALLOWLISTED_ONLY
    handoff: MbaHandoffDTO
    followup: MbaFollowupDTO
    never_say_phrases: tuple[MbaPhraseDTO, ...]


@dataclass(frozen=True)
class MbaExcludedDTO:
    source: str
    reason: str


@dataclass(frozen=True)
class MbaEndpointDTO:
    section: str
    method: str
    path: str


@dataclass(frozen=True)
class MbaConnectorToolDTO:
    name: str
    description: str
    method: str
    path: str
    query_parameters: tuple[str, ...]
    body_parameters: tuple[str, ...]
    bindings: tuple[str, ...]
    write: bool
    notes: str
    source: str


@dataclass(frozen=True)
class MbaConnectorDTO:
    name: str
    description: str
    base_url: str
    auth_type: str
    auth_header: str
    requires_certificate: bool
    tools: tuple[MbaConnectorToolDTO, ...]


@dataclass(frozen=True)
class MbaUiSkillDTO:
    title: str
    component_type: str
    status: str
    instruction: str
    source: str
    kind: str  # static | dynamic
    note: str


@dataclass(frozen=True)
class MbaRequestDTO:
    """UNA llamada HTTP a Meta tal cual se enviaría (headers + body exactos)."""

    step: int
    section: str
    label: str
    method: str
    url: str
    headers: dict[str, str]
    body: dict[str, Any]
    notes: str = ""


@dataclass(frozen=True)
class MbaConfigDTO:
    agent_id: str
    display_name: str
    channel: str
    entity_id: str | None
    business_info: MbaBusinessInfoDTO
    settings: MbaSettingsDTO
    skills: tuple[MbaSkillDTO, ...] = field(default_factory=tuple)
    faqs: tuple[MbaFaqDTO, ...] = field(default_factory=tuple)
    connector: MbaConnectorDTO | None = None
    ui_skills: tuple[MbaUiSkillDTO, ...] = field(default_factory=tuple)
    allowlist: tuple[str, ...] = field(default_factory=tuple)
    excluded: tuple[MbaExcludedDTO, ...] = field(default_factory=tuple)
    endpoints: tuple[MbaEndpointDTO, ...] = field(default_factory=tuple)
    workspace: str = ""
    requests: tuple[MbaRequestDTO, ...] = field(default_factory=tuple)
    problems: tuple[str, ...] = field(default_factory=tuple)


ENDPOINTS: tuple[MbaEndpointDTO, ...] = (
    MbaEndpointDTO("business_info", "PUT", "/{entity_id}/agent_config/business_info"),
    MbaEndpointDTO("faqs", "POST", "/{entity_id}/agent_config/faq"),
    MbaEndpointDTO("skills", "POST", "/{entity_id}/agent_config/skills"),
    MbaEndpointDTO("connector", "POST", "/{entity_id}/agent_connectors"),
    MbaEndpointDTO(
        "connector_tools", "POST", "/{entity_id}/agent_connectors/{connector_id}/tools"
    ),
    MbaEndpointDTO("ui_skills", "POST", "/{entity_id}/agent-ui-skills"),
    MbaEndpointDTO("settings", "PUT", "/{entity_id}/agent_config/settings"),
    MbaEndpointDTO("allowlist", "POST", "/{entity_id}/agent_config/allowlist"),
)

_DYNAMIC_UI_NOTE = (
    "Dinámico: los datos salen de las herramientas del connector. La doc de Meta no "
    "documenta cómo el agente puebla el componente: a verificar en F0 (sandbox + allowlist)."
)


# ---------------------------------------------------------------------------
# Parseo de los archivos
# ---------------------------------------------------------------------------


def _parse_skill_file(text: str) -> tuple[dict[str, Any], str]:
    """Front-matter plano ``clave: valor`` (una línea por clave; el valor puede
    llevar dos puntos, por eso NO se parsea como YAML) + cuerpo markdown."""
    m = _FRONT_MATTER.match(text)
    if not m:
        return {}, text.strip()
    meta: dict[str, Any] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, text[m.end() :].strip()


def _build_skills(spec: dict[str, Any], files: AgentFiles, problems: list[str]) -> tuple[MbaSkillDTO, ...]:
    out: list[MbaSkillDTO] = []
    for title in spec.get("skills") or []:
        path = f"skills/{title}.md"
        text = files.skills.get(path)
        if text is None:
            problems.append(f"skill `{title}` declarada en agent.yaml sin archivo {path}")
            continue
        meta, body = _parse_skill_file(text)
        fm_title = str(meta.get("title") or title)
        description = str(meta.get("description") or "").strip()
        if fm_title != title:
            problems.append(
                f"{path}: el título del front-matter `{fm_title}` no coincide con `{title}`"
            )
        if not _SKILL_TITLE.match(fm_title) or len(fm_title) > SKILL_TITLE_LIMIT:
            problems.append(
                f"{path}: título `{fm_title}` fuera del formato de Meta "
                f"(minúsculas, dígitos y guiones, ≤{SKILL_TITLE_LIMIT})"
            )
        if not description:
            problems.append(f"{path}: falta `description` (cuándo aplica la skill)")
        elif len(description) > SKILL_DESCRIPTION_LIMIT:
            problems.append(
                f"{path}: description de {len(description)} caracteres, máximo {SKILL_DESCRIPTION_LIMIT}"
            )
        over = len(body) > SKILL_CHAR_LIMIT
        if over:
            problems.append(
                f"{path}: {len(body):,} caracteres, excede el límite de {SKILL_CHAR_LIMIT:,}".replace(",", ".")
            )
        out.append(
            MbaSkillDTO(
                title=fm_title,
                description=description,
                skill=body,
                char_count=len(body),
                char_limit=SKILL_CHAR_LIMIT,
                over_limit=over,
                sources=(path,),
            )
        )
    return tuple(out)


def _build_business_info(spec: dict[str, Any]) -> MbaBusinessInfoDTO:
    bi = spec.get("business_info") or {}
    contact = bi.get("contact_info") or {}
    return MbaBusinessInfoDTO(
        business_description=str(bi.get("business_description") or "").strip(),
        payment_method=str(bi.get("payment_method") or "").strip(),
        delivery_and_shipping=str(bi.get("delivery_and_shipping") or "").strip(),
        return_policy=str(bi.get("return_policy") or "").strip(),
        purchase_info=str(bi.get("purchase_info") or "").strip(),
        contact_info=MbaContactInfoDTO(
            email=contact.get("email") or None,
            hours_of_operation=(str(contact["hours_of_operation"]).strip() if contact.get("hours_of_operation") else None),
            address=contact.get("address") or None,
        ),
        sources=("agent.yaml",),
    )


def _build_settings(spec: dict[str, Any]) -> MbaSettingsDTO:
    st = spec.get("settings") or {}
    handoff = st.get("handoff") or {}
    followup = st.get("followup") or {}
    phrases: list[MbaPhraseDTO] = []
    seen: set[str] = set()
    for p in st.get("never_say_phrases") or []:
        p = str(p).strip()
        if p and p not in seen:
            seen.add(p)
            phrases.append(MbaPhraseDTO(phrase=p, source="agent.yaml"))
    return MbaSettingsDTO(
        rollout_enabled=bool(st.get("rollout_enabled", False)),
        ai_audience=str(st.get("ai_audience") or "ALLOWLISTED_ONLY"),
        handoff=MbaHandoffDTO(
            enabled=bool(handoff.get("enabled", True)),
            message=(str(handoff["message"]).strip() if handoff.get("message") else None),
            message_selection=str(handoff.get("message_selection") or "DEFAULT"),
        ),
        followup=MbaFollowupDTO(
            enabled=bool(followup.get("enabled", False)),
            followup_interval_in_seconds=int(followup.get("followup_interval_in_seconds") or 900),
            message=(str(followup["message"]).strip() if followup.get("message") else None),
        ),
        never_say_phrases=tuple(phrases),
    )


def _build_connector(spec: dict[str, Any], problems: list[str]) -> MbaConnectorDTO | None:
    con = spec.get("connector")
    if not con:
        return None
    tools: list[MbaConnectorToolDTO] = []
    for t in con.get("tools") or []:
        name = str(t.get("name") or "")
        method = str(t.get("method") or "GET").upper()
        if method not in TOOL_METHODS:
            problems.append(f"tool `{name}`: method `{method}` no soportado (GET o POST)")
        params = tuple(str(p) for p in (t.get("params") or {}))
        tools.append(
            MbaConnectorToolDTO(
                name=name,
                description=" ".join(str(t.get("description") or "").split()),
                method=method,
                path=f"/tools/{name}",
                query_parameters=params if method == "GET" else (),
                body_parameters=params if method == "POST" else (),
                bindings=(CUSTOMER_PHONE_MACRO,),
                write=bool(t.get("write", method == "POST")),
                notes=" ".join(str(t.get("notes") or "").split()),
                source="agent.yaml",
            )
        )
    return MbaConnectorDTO(
        name=str(con.get("name") or ""),
        description=" ".join(str(con.get("description") or "").split()),
        base_url=str(con.get("base_url") or ""),
        auth_type=str(con.get("auth_type") or "API_KEY"),
        auth_header=str(con.get("auth_header") or "X-API-Key"),
        requires_certificate=bool(con.get("requires_certificate", False)),
        tools=tuple(tools),
    )


def _build_ui_skills(spec: dict[str, Any], problems: list[str]) -> tuple[MbaUiSkillDTO, ...]:
    out: list[MbaUiSkillDTO] = []
    for u in spec.get("ui_skills") or []:
        title = str(u.get("title") or "")
        component = str(u.get("component_type") or "")
        if component not in UI_COMPONENT_TYPES:
            problems.append(
                f"ui skill `{title}`: component_type `{component}` no está en el enum de Meta "
                f"({', '.join(sorted(UI_COMPONENT_TYPES))})"
            )
        kind = str(u.get("kind") or "static")
        out.append(
            MbaUiSkillDTO(
                title=title,
                component_type=component,
                status=str(u.get("status") or "enabled"),
                instruction=" ".join(str(u.get("instruction") or "").split()),
                source="agent.yaml",
                kind=kind,
                note=str(u.get("note") or (_DYNAMIC_UI_NOTE if kind == "dynamic" else "")),
            )
        )
    return tuple(out)


# ---------------------------------------------------------------------------
# Requests literales a Meta
# ---------------------------------------------------------------------------


def _endpoint_url(section: str) -> tuple[str, str]:
    ep = next(e for e in ENDPOINTS if e.section == section)
    return ep.method, MBA_BASE_URL + ep.path


def _customer_phone_param() -> dict[str, Any]:
    return {
        "type": "string",
        "description": "Teléfono del cliente que escribe (lo inyecta Meta, E.164).",
        "required": True,
        "binding": {"kind": "macro", "macro": CUSTOMER_PHONE_MACRO},
    }


def _param_schema(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "type": str(raw.get("type") or "string"),
        "description": " ".join(str(raw.get("description") or "").split()),
        "required": bool(raw.get("required", False)),
    }
    if "items" in raw:
        items = raw["items"]
        out["items"] = items if isinstance(items, str) else json.dumps(items, ensure_ascii=False)
    if "properties" in raw:
        out["properties"] = raw["properties"]
    return out


def _tool_request_definition(spec_tool: dict[str, Any], phone_param: str) -> dict[str, Any]:
    method = str(spec_tool.get("method") or "GET").upper()
    name = str(spec_tool.get("name") or "")
    raw_params: dict[str, Any] = spec_tool.get("params") or {}
    rd: dict[str, Any] = {
        "method": method,
        "path": f"/tools/{name}",
        "path_parameters": {},
        "query_parameters": {},
        "headers": {},
    }
    if method == "GET":
        rd["query_parameters"] = {phone_param: _customer_phone_param()}
        for p, raw in raw_params.items():
            rd["query_parameters"][p] = _param_schema(raw)
    else:
        params = {phone_param: _customer_phone_param()}
        required = [phone_param]
        for p, raw in raw_params.items():
            params[p] = _param_schema(raw)
            if raw.get("required"):
                required.append(p)
        rd["body"] = {"content_type": "application/json", "params": params, "required": required}
    return rd


def _build_requests(cfg: MbaConfigDTO, spec: dict[str, Any]) -> tuple[MbaRequestDTO, ...]:
    """Las llamadas exactas a Meta, en el orden de la guía get-started.

    Conocimiento (business_info, FAQs) → skills → connector → sus tools → UI
    skills → settings (rollout apagado, al final) → allowlist. Un request por
    ítem: la API de Meta no acepta lotes.
    """
    out: list[MbaRequestDTO] = []

    def add(section: str, label: str, body: dict[str, Any], notes: str = "") -> None:
        method, url = _endpoint_url(section)
        out.append(
            MbaRequestDTO(
                step=len(out) + 1,
                section=section,
                label=label,
                method=method,
                url=url,
                headers=dict(_MBA_HEADERS),
                body=body,
                notes=notes,
            )
        )

    bi = cfg.business_info
    bi_body: dict[str, Any] = {k: getattr(bi, k) for k in _BI_TEXT_FIELDS if getattr(bi, k)}
    contact = {
        k: v
        for k, v in (
            ("email", bi.contact_info.email),
            ("hours_of_operation", bi.contact_info.hours_of_operation),
            ("address", bi.contact_info.address),
        )
        if v
    }
    if contact:
        bi_body["contact_info"] = contact
    add("business_info", "business_info", bi_body, "PUT reemplaza TODO el bloque; los campos vacíos no viajan.")

    for faq in cfg.faqs:
        add("faqs", faq.question, {"question": faq.question, "answer": faq.answer})

    for sk in cfg.skills:
        add(
            "skills",
            sk.title,
            {"title": sk.title, "description": sk.description, "skill": sk.skill},
            (
                f"Excede el límite de {SKILL_CHAR_LIMIT:,} caracteres: Meta lo rechaza tal cual."
                if sk.over_limit
                else ""
            ),
        )

    con = cfg.connector
    if con is not None:
        spec_con = spec.get("connector") or {}
        phone_param = str(spec_con.get("customer_phone_param") or "customer_phone")
        add(
            "connector",
            con.name,
            {
                "name": con.name,
                "description": con.description,
                "base_url": con.base_url,
                "auth_type": con.auth_type,
                "auth_config": {
                    "api_key": {
                        "headers": [
                            {"field_name": con.auth_header, "value": CONNECTOR_API_KEY_PLACEHOLDER, "prefix": ""}
                        ],
                        "query_params": [],
                    }
                },
                "requires_certificate": con.requires_certificate,
            },
            "La respuesta trae el `id` del connector: es el {connector_id} de las tools.",
        )
        by_name = {str(t.get("name")): t for t in (spec_con.get("tools") or [])}
        for tool in con.tools:
            add(
                "connector_tools",
                tool.name,
                {
                    "name": tool.name,
                    "description": tool.description,
                    "request_definition": _tool_request_definition(by_name[tool.name], phone_param),
                    "user_auth_required": False,
                },
                tool.notes,
            )

    for ui in cfg.ui_skills:
        add(
            "ui_skills",
            ui.title,
            {
                "title": ui.title,
                "component_type": ui.component_type,
                "status": ui.status,
                "instruction": ui.instruction,
            },
            ui.note,
        )

    st = cfg.settings
    handoff: dict[str, Any] = {"enabled": st.handoff.enabled, "message_selection": st.handoff.message_selection}
    if st.handoff.message:
        handoff["message"] = st.handoff.message
    followup: dict[str, Any] = {"enabled": st.followup.enabled}
    if st.followup.enabled:
        followup["followup_interval_in_seconds"] = st.followup.followup_interval_in_seconds
        if st.followup.message:
            followup["message"] = st.followup.message
    add(
        "settings",
        "settings",
        {
            "rollout": {"enabled": st.rollout_enabled},
            "ai_audience": st.ai_audience,
            "handoff": handoff,
            "followup": followup,
            "never_say_phrases": [p.phrase for p in st.never_say_phrases],
        },
        "Va al final: con rollout.enabled=false MBA no responde a nadie hasta que se prenda a mano. "
        "never_say_phrases reemplaza la lista completa.",
    )

    for phone in cfg.allowlist:
        add(
            "allowlist",
            phone,
            {"consumer_phone_number": phone},
            "Teléfono de prueba de F0 (E.164). Con ai_audience=ALLOWLISTED_ONLY solo ellos hablan con MBA y no hay facturación.",
        )
    return tuple(out)


# ---------------------------------------------------------------------------
# Entrada pública
# ---------------------------------------------------------------------------


def build_agent_config(files: AgentFiles, workspace: str = "") -> MbaConfigDTO:
    spec = yaml.safe_load(files.agent_yaml) or {}
    problems: list[str] = []
    cfg = MbaConfigDTO(
        agent_id=str(spec.get("id") or ""),
        display_name=str(spec.get("display_name") or spec.get("id") or ""),
        channel=str(spec.get("channel") or "whatsapp"),
        entity_id=(str(spec["entity_id"]) if spec.get("entity_id") else None),
        business_info=_build_business_info(spec),
        settings=_build_settings(spec),
        skills=_build_skills(spec, files, problems),
        faqs=tuple(
            MbaFaqDTO(question=str(f.get("question") or "").strip(), answer=str(f.get("answer") or "").strip(), source="agent.yaml")
            for f in (spec.get("faqs") or [])
        ),
        connector=_build_connector(spec, problems),
        ui_skills=_build_ui_skills(spec, problems),
        allowlist=tuple(str(p) for p in (spec.get("allowlist") or [])),
        excluded=tuple(
            MbaExcludedDTO(source=str(e.get("source") or ""), reason=str(e.get("reason") or ""))
            for e in (spec.get("not_in_mba") or [])
        ),
        endpoints=ENDPOINTS,
        workspace=workspace,
        problems=tuple(problems),
    )
    return replace(cfg, requests=_build_requests(cfg, spec))
