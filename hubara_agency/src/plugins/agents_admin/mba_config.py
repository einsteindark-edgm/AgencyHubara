"""Normalización *workspace del agente → configuración Meta Business Agent*.

Función PURA (R-DET, sin I/O): recibe los archivos del workspace ya leídos y
devuelve un DTO con la forma EXACTA de los endpoints de configuración de MBA
(``/agent_config/skills``, ``business_info``, ``faq``, ``settings``), con
trazabilidad archivo → campo. Es la fuente única de "qué le mandaríamos a MBA".

Referencia de contrato (doc oficial, leída 2026-09-02):
- skills: ``title`` ≤64 (``[a-z0-9-]``, sin guion al borde), ``description``
  ≤1024, ``skill`` ≤20000. "Consolidar en UN skill con secuencia explícita en
  vez de varios skills en conflicto".
- business_info: ``business_description``, ``payment_method``,
  ``delivery_and_shipping``, ``return_policy``, ``purchase_info``,
  ``contact_info{email,hours_of_operation,address}``.
- faq: ``question`` + ``answer`` (una por llamada).
- settings: ``rollout.enabled``, ``ai_audience``, ``handoff{enabled,message,
  message_selection}``, ``followup{enabled,followup_interval_in_seconds,
  message}``, ``never_say_phrases[]``.

Reglas de extracción (precisión sobre cobertura: lo que entra acá se manda tal
cual a Meta, así que ante la duda se EXCLUYE y se muestra como excluido):
- FAQ = fila de una tabla de 2 columnas cuyo header sea Objeción/Pregunta →
  Respuesta. Filas que resuelven con una tool (``escalate_to_human(...)``) NO
  son FAQ: van a ``excluded`` (son handoff/connector, desarrollo 2).
- never_say = celdas de una columna PROHIBIDO + frases entrecomilladas en
  líneas ENCABEZADAS por una prohibición (NUNCA / NO / Sin / 🚫 / PROHIBIDO),
  nunca preguntas (los ejemplos de uso correcto suelen ser preguntas).
- business_info = bullets de USER.md y de los skills "de conocimiento"
  (los que tienen secciones de marca/envíos/pagos/garantía), clasificados por
  palabra clave del bullet y, si no hay, por la sección que lo contiene.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace
from typing import Any, Iterator, Mapping

SKILL_CHAR_LIMIT = 20000
SKILL_DESCRIPTION_LIMIT = 1024
SKILL_TITLE_LIMIT = 64
NEVER_SAY_MAX_LEN = 60

# Orden canónico del funnel (espeja ``use_cases/funnel_stage.py`` de sales).
# Etapas desconocidas van después, en orden alfabético.
_STAGE_ORDER: tuple[str, ...] = (
    "descubrimiento",
    "variantes",
    "datos_envio",
    "cierre",
    "postcierre",
)
_STAGE_PREFIX = "etapa_"

_FAQ_QUESTION_HEADER = re.compile(r"objeci|pregunta|situaci", re.IGNORECASE)
_FAQ_ANSWER_HEADER = re.compile(r"respuesta", re.IGNORECASE)
_PROHIBITED_HEADER = re.compile(r"prohibid", re.IGNORECASE)
_TOOL_CALL = re.compile(r"`[a-z_]+\(")
# Instrucción para el LLM (tool snake_case invocada): no es conocimiento del negocio.
_TOOL_INSTRUCTION = re.compile(r"\b[a-z]+(?:_[a-z]+)+\(")
# Referencia cruzada interna, p.ej. (ver `SOUL.md` → "Puntuación natural").
_CROSS_REF_NOTE = re.compile(r"\([^()]*`[^()]*\)")
_PROHIBITION_LEAD = re.compile(r"^(?:nunca\b|no\b|sin\b|prohibid|🚫)", re.IGNORECASE)
_LEAD_STRIP = re.compile(r"^[\s\-\*•]*(?:🚫\s*)?(?:\*\*)?")
_DOUBLE_QUOTED = re.compile(r'"([^"\n]{2,%d})"' % NEVER_SAY_MAX_LEN)
_BACKTICK_QUOTED = re.compile(r"`([^`\n]{2,%d})`" % NEVER_SAY_MAX_LEN)
_ITALIC_QUOTED = re.compile(r'\*"([^"\n]+)"\*')
_PAREN_NOTE_WITH_CODE = re.compile(r"\s*\(`[^`]*`[^)]*\)")
_PARENTHETICAL = re.compile(r"\s*\([^)]*\)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_BULLET = re.compile(r"^(\s*)[-*•]\s+(.*)$")

# business_info: campo ← regex sobre heading o sobre el bullet. El orden
# importa: el primero que matchea gana.
_BI_FIELD_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("hours_of_operation", re.compile(r"horario|zona horaria", re.IGNORECASE)),
    ("return_policy", re.compile(r"garant[ií]a|devoluci|cambios", re.IGNORECASE)),
    ("payment_method", re.compile(r"pago|precio|\bcop\b", re.IGNORECASE)),
    ("delivery_and_shipping", re.compile(r"env[ií]o", re.IGNORECASE)),
    ("purchase_info", re.compile(r"descuento|pol[ií]ticas adicionales|c[oó]mo comprar", re.IGNORECASE)),
    (
        "business_description",
        re.compile(r"identidad de marca|tenant|organizaci[oó]n|sobre la marca|qui[eé]nes somos", re.IGNORECASE),
    ),
)
_BI_FIELDS = tuple(name for name, _ in _BI_FIELD_PATTERNS)


@dataclass(frozen=True)
class WorkspaceSkill:
    """Un ``skills/<name>/SKILL.md`` ya parseado (front-matter separado)."""

    name: str
    description: str
    always: bool
    body: str


@dataclass(frozen=True)
class WorkspaceSources:
    """Entrada pura: archivos bootstrap (``IDENTITY.md`` …) + skills."""

    files: Mapping[str, str]
    skills: tuple[WorkspaceSkill, ...] = ()


@dataclass(frozen=True)
class MbaSkillDTO:
    """Espeja ``POST /{entity_id}/agent_config/skills`` + metadata de UI."""

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
class MbaRequestDTO:
    """UNA llamada HTTP a Meta tal cual se enviaría (headers + body exactos).

    ``body`` es el JSON literal del request (sin metadata de UI); lo único que
    no es literal son los placeholders ``{entity_id}`` / ``{connector_id}`` en
    la URL y los secretos (token, api key), que se resuelven al enviar.
    """

    step: int
    section: str
    label: str
    method: str
    url: str
    headers: dict[str, str]
    body: dict[str, Any]
    notes: str = ""


@dataclass(frozen=True)
class MbaConnectorToolDTO:
    """Espeja ``POST /{entity_id}/agent_connectors/{id}/tools`` (request_definition)."""

    name: str
    description: str
    method: str  # GET | POST
    path: str
    query_parameters: tuple[str, ...]
    body_parameters: tuple[str, ...]
    bindings: tuple[str, ...]  # macros de Meta inyectadas (WHATSAPP_PHONE_NUMBER…)
    write: bool
    notes: str
    source: str


@dataclass(frozen=True)
class MbaConnectorDTO:
    """Espeja ``POST /{entity_id}/agent_connectors``."""

    name: str
    description: str
    base_url: str
    auth_type: str  # OAUTH2_CLIENT_CREDENTIALS | API_KEY | NONE
    auth_header: str
    requires_certificate: bool
    tools: tuple[MbaConnectorToolDTO, ...]


@dataclass(frozen=True)
class MbaUiSkillDTO:
    """Espeja ``POST /{entity_id}/agent-ui-skills`` + metadata de UI.

    ``kind``: ``static`` cuando la ``instruction`` puede contener TODO lo que el
    componente necesita (URL fija, botones fijos, flow_id); ``dynamic`` cuando
    los datos salen del catálogo o de un connector (carrusel de productos,
    resumen del pedido) — la doc de Meta no documenta cómo se poblan, así que
    se marcan "a verificar en F0" en vez de darse por resueltos.
    """

    title: str
    component_type: str
    status: str  # enabled | disabled
    instruction: str
    from_tool: str
    source: str
    kind: str  # static | dynamic
    note: str


@dataclass(frozen=True)
class MbaToolTreatmentDTO:
    """Qué hacemos con cada tool LLM del agente al pasar a MBA."""

    llm_tool: str
    when: str
    treatment: str  # connector_tool | ui_skill | native_handoff | internal | unmapped
    detail: str
    endpoint: str | None  # endpoint de Meta al que viaja; None = no viaja


@dataclass(frozen=True)
class MbaConfigDTO:
    agent_id: str
    channel: str
    business_info: MbaBusinessInfoDTO
    settings: MbaSettingsDTO
    skills: tuple[MbaSkillDTO, ...] = field(default_factory=tuple)
    faqs: tuple[MbaFaqDTO, ...] = field(default_factory=tuple)
    connector: MbaConnectorDTO | None = None
    ui_skills: tuple[MbaUiSkillDTO, ...] = field(default_factory=tuple)
    tool_treatments: tuple[MbaToolTreatmentDTO, ...] = field(default_factory=tuple)
    excluded: tuple[MbaExcludedDTO, ...] = field(default_factory=tuple)
    endpoints: tuple[MbaEndpointDTO, ...] = field(default_factory=tuple)
    # Ruta (relativa al repo) del workspace del que salió todo: las ``sources``
    # de cada sección son relativas a este directorio.
    workspace: str = ""
    # Las llamadas HTTP exactas a Meta, en orden de envío (1..N).
    requests: tuple[MbaRequestDTO, ...] = field(default_factory=tuple)


ENDPOINTS: tuple[MbaEndpointDTO, ...] = (
    MbaEndpointDTO("skills", "POST", "/{entity_id}/agent_config/skills"),
    MbaEndpointDTO("business_info", "PUT", "/{entity_id}/agent_config/business_info"),
    MbaEndpointDTO("faqs", "POST", "/{entity_id}/agent_config/faq"),
    MbaEndpointDTO("settings", "PUT", "/{entity_id}/agent_config/settings"),
    MbaEndpointDTO("connector", "POST", "/{entity_id}/agent_connectors"),
    MbaEndpointDTO(
        "connector_tools", "POST", "/{entity_id}/agent_connectors/{connector_id}/tools"
    ),
    MbaEndpointDTO("ui_skills", "POST", "/{entity_id}/agent-ui-skills"),  # noqa: E501
    MbaEndpointDTO("allowlist", "POST", "/{entity_id}/agent_config/allowlist"),
)

# Connector de Hubara (desarrollo 2): la API pública que MBA invoca. El host
# real se define al desplegar; acá se muestra el contrato que se registraría.
CONNECTOR_BASE_URL_PLACEHOLDER = "https://<host-publico>/api/mba"
CONNECTOR_AUTH_HEADER = "X-API-Key"
CUSTOMER_PHONE_MACRO = "WHATSAPP_PHONE_NUMBER"

# Prefijos de tools LLM → tratamiento en MBA (reglas por nombre, sin importar
# código de otros plugins). El orden importa: el primero que matchea gana.
_READ_TOOL_PREFIXES = ("search_", "get_", "list_", "check_", "verify_")
_WRITE_TOOL_PREFIXES = ("register_", "create_", "update_", "cancel_")
_UI_TOOL_PREFIXES = ("present_", "send_", "request_", "react_")
# Tools de ESTADO de Hubara (tags, memoria del pedido, escalación): también
# viajan como connector tools de escritura — es la única forma de que MBA le
# avise a Hubara el resultado del funnel (INTERESADO → remarketing, HUMANO →
# bandeja, datos confirmados → orden).
_STATE_TOOL_MARKERS = ("escalate", "_tag", "_slot")
_INTERNAL_TOOL_MARKERS = ("load_skill",)
# Sub-cadena del nombre de la tool → (component_type de Meta, kind).
# kind=dynamic: el componente necesita datos del catálogo / connector.
_UI_COMPONENT_BY_KEY: tuple[tuple[str, str | None, str], ...] = (
    ("variant_picker", "interactive_list", "dynamic"),
    ("quick_replies", "interactive_reply_buttons", "static"),
    ("order_confirmation", "interactive_reply_buttons", "dynamic"),
    ("shipping_details", "flow", "static"),
    ("product_gallery", "image", "dynamic"),
    ("product_detail", "image", "dynamic"),
    ("products", "carousel_quick_reply", "dynamic"),
    ("cta_url", "cta_url", "static"),
    ("contact_card", "cta_url", "static"),
    ("location", "location_request", "static"),
    ("react_to", None, "static"),
)
_DYNAMIC_UI_NOTE = (
    "Dinámico: títulos, fotos y precios salen del catálogo o del connector. La doc "
    "de Meta no documenta cómo el agente puebla el componente con datos dinámicos: "
    "a verificar en F0 (sandbox + allowlist)."
)
_STATE_TOOL_NOTES: tuple[tuple[str, str], ...] = (
    (
        "escalate",
        "Escritura: Hubara marca route=humano + tag HUMANO y envía el mensaje de "
        "handoff, con lo que toma el hilo (MBA deja de responder en ese chat). MBA "
        "además escala por su cuenta ante baja confianza o pedido explícito.",
    ),
    (
        "_tag",
        "Escritura: lo que MBA manda es una PROPUESTA; Hubara la reconcilia con sus "
        "reglas deterministas antes de aplicarla (una orden registrada gana → "
        "CONFIRMADO_PAGO_PENDIENTE). La etiqueta aplicada dispara la maquinaria de "
        "Hubara (INTERESADO → remarketing, RECHAZO → sin remarketing). Idempotente "
        "por (sesión, etiqueta).",
    ),
    (
        "_slot",
        "Escritura: memoria determinista del pedido en Hubara, para que "
        "register_order y el humano vean lo que el cliente confirmó. Idempotente "
        "(sobrescribe el slot).",
    ),
)
# Etiquetas que MBA PUEDE proponer (requieren juicio semántico sobre el
# mensaje). Las derivables de pedidos/silencio (CONFIRMADO_*, COMPRA_EXITOSA)
# las determina Hubara con sus reglas deterministas y el watchdog.
PROPOSABLE_TAGS: tuple[str, ...] = ("INTERESADO", "RECHAZO")
_TAG_BULLET = re.compile(r"^`([A-Z_]+)`\s*:\s*(.*)$")
_ENDPOINT_CONNECTOR_TOOLS = "/{entity_id}/agent_connectors/{connector_id}/tools"
_ENDPOINT_UI_SKILLS = "/{entity_id}/agent-ui-skills"
_TOOL_TABLE_HEADER = re.compile(r"\btool\b", re.IGNORECASE)
_TOOL_NAME = re.compile(r"`([a-z][a-z0-9_]*)")
_TOOL_PARAM = re.compile(r"`([a-z][a-z0-9_]*)=([^`]*)`")
_TOOL_CALL_PARAM = re.compile(r"\(([a-z][a-z0-9_]*)=")

_STATIC_EXCLUSIONS: tuple[MbaExcludedDTO, ...] = (
    MbaExcludedDTO(
        "memory/MEMORY.md · memory/HISTORY.md",
        "Memoria dinámica del runtime; MBA mantiene su propio contexto por hilo.",
    ),
    MbaExcludedDTO(
        "Trigger de ghosting ([SISTEMA] tras silencio del cliente)",
        "No viaja: MBA no tiene ghosting ni episodios. El watchdog de Hubara etiqueta "
        "INTERESADO o CONFIRMADO_SIN_DATOS por silencio usando las señales del connector "
        "(búsquedas, slots, orden registrada).",
    ),
    MbaExcludedDTO(
        "plugin_context (hora de Bogotá · DATOS DEL PEDIDO)",
        "Contexto inyectado por turno; MBA no lo recibe. El saludo por hora queda "
        "en la skill de persona; el pedido se consulta vía connector.",
    ),
)


# ---------------------------------------------------------------------------
# Helpers de markdown (puros)
# ---------------------------------------------------------------------------


def _slug(text: str) -> str:
    """``Notas_Olfativas`` → ``notas-olfativas`` (contrato de título MBA)."""
    ascii_text = (
        unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug[:SKILL_TITLE_LIMIT].strip("-")


def _skill_source(skill: WorkspaceSkill) -> str:
    return f"skills/{skill.name}/SKILL.md"


def _clean_inline(text: str) -> str:
    """Quita negritas y backticks de markdown; colapsa espacios."""
    text = text.replace("**", "").replace("`", "")
    return re.sub(r"[ \t]+", " ", text).strip()


def _strip_quotes(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return text[1:-1].strip()
    return text


def _split_cells(line: str) -> list[str]:
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [c.strip() for c in inner.split("|")]


def _is_separator_row(line: str) -> bool:
    return bool(re.match(r"^\s*\|?\s*:?-{2,}", line)) and set(line.strip()) <= set("|-: ")


def _tables(md: str) -> Iterator[tuple[list[str], list[list[str]]]]:
    """Yields ``(header_cells, rows)`` por cada tabla markdown."""
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("|") and i + 1 < len(lines) and _is_separator_row(lines[i + 1]):
            header = _split_cells(lines[i])
            rows: list[list[str]] = []
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append(_split_cells(lines[i]))
                i += 1
            yield header, rows
        else:
            i += 1


def _sections(md: str) -> Iterator[tuple[str, list[str]]]:
    """Yields ``(heading, lines)``; el preámbulo antes del primer H2+ va con heading ''."""
    heading = ""
    buf: list[str] = []
    for line in md.splitlines():
        m = _HEADING.match(line)
        if m and len(m.group(1)) >= 2:
            yield heading, buf
            heading, buf = m.group(2), []
        elif m:
            continue  # H1: título del archivo, no es sección
        else:
            buf.append(line)
    yield heading, buf


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


def _make_skill(
    title: str, description: str, parts: list[tuple[str, str]]
) -> MbaSkillDTO:
    """``parts`` = [(source, body), …] concatenados en ese orden."""
    body = "\n\n".join(p.strip() for _, p in parts if p.strip())
    return MbaSkillDTO(
        title=_slug(title),
        description=description[:SKILL_DESCRIPTION_LIMIT],
        skill=body,
        char_count=len(body),
        char_limit=SKILL_CHAR_LIMIT,
        over_limit=len(body) > SKILL_CHAR_LIMIT,
        sources=tuple(src for src, _ in parts),
    )


def _stage_rank(skill: WorkspaceSkill) -> tuple[int, str]:
    stage = skill.name[len(_STAGE_PREFIX):]
    try:
        return (_STAGE_ORDER.index(stage), stage)
    except ValueError:
        return (len(_STAGE_ORDER), stage)


def _is_knowledge_skill(skill: WorkspaceSkill) -> bool:
    """Un skill "de conocimiento" es on-demand (ni ``always`` ni etapa del
    funnel: esos son guion para el LLM) y tiene secciones de marca / envíos /
    pagos / garantía."""
    if skill.always or skill.name.startswith(_STAGE_PREFIX):
        return False
    for heading, _ in _sections(skill.body):
        if heading and _bi_field_for(heading) is not None:
            return True
    return False


def _build_skills(sources: WorkspaceSources) -> tuple[MbaSkillDTO, ...]:
    files = sources.files
    out: list[MbaSkillDTO] = []

    persona_parts = [
        (name, files[name]) for name in ("IDENTITY.md", "SOUL.md") if name in files
    ]
    if persona_parts:
        out.append(
            _make_skill(
                "persona-y-tono",
                "Aplicar en toda conversación: quién es el asesor, dialecto colombiano, "
                "tono y estilo de escritura para WhatsApp.",
                persona_parts,
            )
        )

    if "AGENTS.md" in files:
        out.append(
            _make_skill(
                "reglas-operativas",
                "Aplicar en cada turno: estructura de la respuesta, promesas prohibidas, "
                "escalación a humano y etiqueta del canal.",
                [("AGENTS.md", files["AGENTS.md"])],
            )
        )

    stages = sorted(
        (s for s in sources.skills if s.name.startswith(_STAGE_PREFIX)), key=_stage_rank
    )
    for core in (s for s in sources.skills if s.always):
        out.append(
            _make_skill(f"guion-{core.name}", core.description, [(_skill_source(core), core.body)])
        )
    if stages:
        # MBA no recibe el "estado del pedido" que Hubara inyecta por turno: la
        # descripción le dice cómo elegir la etapa a partir de lo ya confirmado.
        out.append(
            _make_skill(
                "guion-etapas",
                "Aplicar según el estado del pedido, en este orden: descubrimiento (sin "
                "producto elegido) → variantes (producto sin aroma/color) → datos de "
                "envío (variantes completas) → cierre (datos completos, falta registrar) "
                "→ post-cierre (pedido registrado). Elige la etapa que corresponde a lo "
                "que el cliente ya confirmó en esta conversación.",
                [(_skill_source(st), st.body) for st in stages],
            )
        )

    if "USER.md" in files:
        out.append(
            _make_skill(
                "contexto-del-negocio",
                "Aplicar siempre: datos del negocio, zona horaria y saludo según la hora "
                "de Colombia, tratamiento por defecto del cliente y hechos que puedes asumir.",
                [("USER.md", files["USER.md"])],
            )
        )

    for extra in sources.skills:
        if extra.always or extra.name.startswith(_STAGE_PREFIX) or _is_knowledge_skill(extra):
            continue
        out.append(
            _make_skill(
                f"conocimiento-{extra.name}",
                extra.description,
                [(_skill_source(extra), extra.body)],
            )
        )

    return tuple(out)


# ---------------------------------------------------------------------------
# FAQs
# ---------------------------------------------------------------------------


def _faq_answer(cell: str) -> str:
    """Solo lo que el cliente leería: la cita si la hay, sin notas internas."""
    stripped = cell.strip()
    if stripped.startswith('"'):
        end = stripped.find('"', 1)
        if end > 0:
            return stripped[1:end].strip()
    cleaned = _PAREN_NOTE_WITH_CODE.sub("", stripped)
    return _clean_inline(_strip_quotes(cleaned))


def _build_faqs(
    sources: WorkspaceSources,
) -> tuple[tuple[MbaFaqDTO, ...], tuple[MbaExcludedDTO, ...]]:
    faqs: list[MbaFaqDTO] = []
    excluded: list[MbaExcludedDTO] = []
    docs = [(name, body) for name, body in sources.files.items() if name != "TOOLS.md"]
    docs += [(_skill_source(s), s.body) for s in sources.skills]
    for source, md in docs:
        for header, rows in _tables(md):
            if len(header) != 2:
                continue
            if not (_FAQ_QUESTION_HEADER.search(header[0]) and _FAQ_ANSWER_HEADER.search(header[1])):
                continue
            for row in rows:
                if len(row) != 2:
                    continue
                question = _clean_inline(_strip_quotes(row[0]))
                answer_cell = row[1].strip()
                resolves_with_tool = answer_cell.startswith("`") or (
                    _TOOL_CALL.search(answer_cell) and not answer_cell.startswith('"')
                )
                if resolves_with_tool:
                    excluded.append(
                        MbaExcludedDTO(
                            f"{source}#faq:{question}",
                            "Se resuelve con una tool (handoff / connector), no con texto: "
                            "desarrollo 2.",
                        )
                    )
                    continue
                answer = _faq_answer(answer_cell)
                if question and answer:
                    faqs.append(MbaFaqDTO(question=question, answer=answer, source=source))
    return tuple(faqs), tuple(excluded)


# ---------------------------------------------------------------------------
# never_say_phrases
# ---------------------------------------------------------------------------


def _phrase_ok(phrase: str) -> bool:
    p = phrase.strip()
    if not (2 <= len(p) <= NEVER_SAY_MAX_LEN):
        return False
    if p.startswith("¿") or p.endswith("?"):
        return False
    if any(ch in p for ch in "`*\\\n"):
        return False
    return True


def _backtick_phrase_ok(phrase: str) -> bool:
    p = phrase.strip()
    return _phrase_ok(p) and (" " in p or p[-1] in ":!,.?")


def _first_sentence(line: str) -> str:
    """Corta en el primer ``. `` fuera de comillas: las frases prohibidas van en
    la oración encabezada por la prohibición, no en la explicación posterior."""
    in_quote = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_quote = not in_quote
        elif ch == "." and not in_quote and (i + 1 == len(line) or line[i + 1] == " "):
            return line[: i + 1]
    return line


def _phrases_from_line(line: str) -> list[str]:
    scope = _first_sentence(_CROSS_REF_NOTE.sub("", line))
    out = [q for q in _DOUBLE_QUOTED.findall(scope) if _phrase_ok(q)]
    out += [q for q in _BACKTICK_QUOTED.findall(scope) if _backtick_phrase_ok(q)]
    return out


def _prohibition_led(line: str) -> bool:
    return bool(_PROHIBITION_LEAD.match(_LEAD_STRIP.sub("", line)))


def _phrases_from_doc(md: str) -> list[str]:
    phrases: list[str] = []
    def cell_phrases(cell: str) -> list[str]:
        out = []
        for part in cell.split(" / "):
            text = _clean_inline(_PARENTHETICAL.sub("", part))
            if _phrase_ok(text):
                out.append(text)
        return out

    for header, rows in _tables(md):
        cols = [i for i, h in enumerate(header) if _PROHIBITED_HEADER.search(h)]
        if not cols:
            continue
        for row in rows:
            allowed = {
                p.lower()
                for i, c in enumerate(row)
                if i not in cols
                for p in cell_phrases(c)
            }
            for i in cols:
                if i >= len(row):
                    continue
                # "dale" prohibido y "dale" permitido en la misma fila: no es prohibición
                phrases.extend(p for p in cell_phrases(row[i]) if p.lower() not in allowed)
    block_active = False
    for line in md.splitlines():
        if not line.strip():
            block_active = False
            continue
        led = _prohibition_led(line)
        continues_block = block_active and bool(_BULLET.match(line))
        if led or continues_block:
            phrases.extend(_phrases_from_line(line))
        block_active = led and line.rstrip().endswith(":") or continues_block
    return phrases


def _build_never_say(sources: WorkspaceSources) -> tuple[MbaPhraseDTO, ...]:
    docs = [(name, body) for name, body in sources.files.items() if name != "TOOLS.md"]
    docs += [(_skill_source(s), s.body) for s in sources.skills]
    seen: set[str] = set()
    out: list[MbaPhraseDTO] = []
    for source, md in docs:
        for phrase in _phrases_from_doc(md):
            key = phrase.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(MbaPhraseDTO(phrase=phrase, source=source))
    return tuple(out)


# ---------------------------------------------------------------------------
# settings.handoff
# ---------------------------------------------------------------------------


def _handoff_message(files: Mapping[str, str]) -> str | None:
    agents_md = files.get("AGENTS.md", "")
    for heading, lines in _sections(agents_md):
        if re.search(r"escalaci", heading, re.IGNORECASE):
            m = _ITALIC_QUOTED.search("\n".join(lines))
            if m:
                return m.group(1).strip()
    return None


# ---------------------------------------------------------------------------
# business_info
# ---------------------------------------------------------------------------


def _bi_field_for(text: str) -> str | None:
    for name, pattern in _BI_FIELD_PATTERNS:
        if pattern.search(text):
            return name
    return None


def _bullets_with_fields(md: str) -> Iterator[tuple[str, str, str]]:
    """Yields ``(heading, field, texto limpio)`` por cada bullet clasificable."""
    for heading, lines in _sections(md):
        section_field = _bi_field_for(heading) if heading else None
        for line in lines:
            m = _BULLET.match(line)
            if not m or _TOOL_INSTRUCTION.search(m.group(2)):
                continue
            text = _clean_inline(m.group(2))
            if not text:
                continue
            fld = _bi_field_for(text) or section_field
            if fld is None:
                continue
            yield heading, fld, text


def business_info_consumed_sections(sources: WorkspaceSources) -> dict[str, set[str]]:
    """Por fuente, los headings cuyos bullets fueron a ``business_info``.

    Lo que un skill de conocimiento tiene FUERA de esas secciones (párrafos de
    instrucción como "Regla absoluta") no es conocimiento del negocio: va a la
    skill ``uso-de-tools``.
    """
    consumed: dict[str, set[str]] = {}
    for source, md in _knowledge_docs(sources):
        for heading, _, _ in _bullets_with_fields(md):
            consumed.setdefault(source, set()).add(heading)
    return consumed


def _knowledge_docs(sources: WorkspaceSources) -> list[tuple[str, str]]:
    docs = [("USER.md", sources.files["USER.md"])] if "USER.md" in sources.files else []
    docs += [(_skill_source(s), s.body) for s in sources.skills if _is_knowledge_skill(s)]
    return docs


def _build_business_info(sources: WorkspaceSources) -> MbaBusinessInfoDTO:
    buckets: dict[str, list[str]] = {name: [] for name in _BI_FIELDS}
    used: list[str] = []
    for source, md in _knowledge_docs(sources):
        touched = False
        for _, fld, text in _bullets_with_fields(md):
            buckets[fld].append(text)
            touched = True
        if touched:
            used.append(source)

    def join(name: str) -> str:
        return "\n".join(buckets[name])

    hours = join("hours_of_operation")
    return MbaBusinessInfoDTO(
        business_description=join("business_description"),
        payment_method=join("payment_method"),
        delivery_and_shipping=join("delivery_and_shipping"),
        return_policy=join("return_policy"),
        purchase_info=join("purchase_info"),
        contact_info=MbaContactInfoDTO(
            email=None, hours_of_operation=hours or None, address=None
        ),
        sources=tuple(used),
    )


# ---------------------------------------------------------------------------
# Connector tools / UI skills (TOOLS.md → tratamiento por tool)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ToolRow:
    name: str
    when: str
    params: tuple[str, ...]


def _tool_rows(tools_md: str) -> list[_ToolRow]:
    """Lee la tabla ``Tool | Cuándo | …`` y une las filas de una misma tool."""
    rows: dict[str, _ToolRow] = {}
    for header, table in _tables(tools_md):
        if len(header) < 2 or not _TOOL_TABLE_HEADER.search(header[0]):
            continue
        for row in table:
            if len(row) < 2:
                continue
            m = _TOOL_NAME.search(row[0])
            if not m:
                continue
            name = m.group(1)
            when = _clean_inline(_PARENTHETICAL.sub("", row[1]))
            params = tuple(
                p
                for cell in row
                for p, value in _TOOL_PARAM.findall(cell)
                if value.strip().lower() not in ("true", "false")
            )
            # `search_products(category=...)` en la 1ª columna también declara un param
            params += tuple(_TOOL_CALL_PARAM.findall(row[0]))
            prev = rows.get(name)
            if prev is None:
                rows[name] = _ToolRow(name=name, when=when, params=params)
            else:
                merged = prev.when if not when or when in prev.when else f"{prev.when} · {when}"
                rows[name] = _ToolRow(name=name, when=merged, params=prev.params + params)
    out: list[_ToolRow] = []
    for r in rows.values():
        seen: list[str] = []
        for p in r.params:
            if p not in seen:
                seen.append(p)
        out.append(_ToolRow(name=r.name, when=r.when, params=tuple(seen)))
    return out


def _ui_component_for(name: str) -> tuple[str | None, str]:
    for key, component, kind in _UI_COMPONENT_BY_KEY:
        if key in name:
            return component, kind
    return None, "static"


def _state_tool_notes(name: str) -> str:
    for marker, notes in _STATE_TOOL_NOTES:
        if marker in name:
            return notes
    return ""


def _build_tools(
    sources: WorkspaceSources,
) -> tuple[
    MbaConnectorDTO | None,
    tuple[MbaUiSkillDTO, ...],
    tuple[MbaToolTreatmentDTO, ...],
    tuple[MbaExcludedDTO, ...],
]:
    tools_md = sources.files.get("TOOLS.md")
    if tools_md is None:
        return None, (), (), ()
    rows = _tool_rows(tools_md)
    if not rows:
        unparsed = MbaExcludedDTO(
            "TOOLS.md",
            "Sin tabla 'Tool | Cuándo' parseable: sus tools no se pudieron mapear a "
            "connector tools / UI skills.",
        )
        return None, (), (), (unparsed,)

    connector_tools: list[MbaConnectorToolDTO] = []
    ui_skills: list[MbaUiSkillDTO] = []
    treatments: list[MbaToolTreatmentDTO] = []
    excluded: list[MbaExcludedDTO] = []
    src = "TOOLS.md"

    for row in rows:
        name = row.name
        is_state = any(marker in name for marker in _STATE_TOOL_MARKERS)
        if any(marker in name for marker in _INTERNAL_TOOL_MARKERS):
            treatments.append(
                MbaToolTreatmentDTO(
                    name, row.when, "internal",
                    "Mecanismo de carga de skills on-demand: en MBA lo reemplaza Knowledge "
                    "(files / websites).",
                    None,
                )
            )
            excluded.append(
                MbaExcludedDTO(f"{src}#tool:{name}", "Tool interna de Hubara; reemplazada por Knowledge.")
            )
        elif name.startswith(_UI_TOOL_PREFIXES):
            component, kind = _ui_component_for(name)
            if component is None:
                treatments.append(
                    MbaToolTreatmentDTO(
                        name, row.when, "unmapped",
                        "Sin componente UI equivalente en MBA. A verificar en F0: si una "
                        "reacción enviada por Hubara toma el hilo (cualquier mensaje nuestro lo hace).",
                        None,
                    )
                )
                excluded.append(
                    MbaExcludedDTO(
                        f"{src}#tool:{name}",
                        "Sin componente de UI skill equivalente en MBA; enviarlo desde Hubara "
                        "podría tomar el hilo. Verificar en F0.",
                    )
                )
                continue
            ui_skills.append(
                MbaUiSkillDTO(
                    title=_slug(name),
                    component_type=component,
                    status="enabled",
                    instruction=row.when,
                    from_tool=name,
                    source=src,
                    kind=kind,
                    note=_DYNAMIC_UI_NOTE if kind == "dynamic" else "",
                )
            )
            treatments.append(
                MbaToolTreatmentDTO(
                    name, row.when, "ui_skill",
                    f"UI skill nativa `{component}` ({'dinámica, a verificar en F0' if kind == 'dynamic' else 'estática'}).",
                    _ENDPOINT_UI_SKILLS,
                )
            )
        elif is_state or name.startswith(_READ_TOOL_PREFIXES) or name.startswith(_WRITE_TOOL_PREFIXES):
            write = is_state or name.startswith(_WRITE_TOOL_PREFIXES)
            uses_body = write or name.startswith("verify_")
            params = row.params
            description = row.when or name
            detail_extra = ""
            if "_tag" in name:
                params = params or ("tag", "motivo")
                sep = "" if description.endswith((".", "!", "?")) else "."
                description += f"{sep} Valores permitidos: {', '.join(PROPOSABLE_TAGS)}."
                detail_extra = (
                    f" MBA propone {'/'.join(PROPOSABLE_TAGS)}; CONFIRMADO_* y el "
                    "silencio los deriva Hubara."
                )
            if is_state:
                notes = _state_tool_notes(name)
            elif write:
                notes = (
                    "Escritura: el endpoint debe ser idempotente (fingerprint + pre-check) "
                    "porque MBA no documenta reintentos ni deduplicación."
                )
            else:
                notes = "Lectura: responde desde la fuente de verdad (Medusa / vault)."
            if not params:
                notes += " Parámetros: definir desde el schema real de la tool (desarrollo 2)."
            connector_tools.append(
                MbaConnectorToolDTO(
                    name=name,
                    description=description,
                    method="POST" if uses_body else "GET",
                    path=f"/tools/{name}",
                    query_parameters=() if uses_body else params,
                    body_parameters=params if uses_body else (),
                    bindings=(CUSTOMER_PHONE_MACRO,),
                    write=write,
                    notes=notes,
                    source=src,
                )
            )
            treatments.append(
                MbaToolTreatmentDTO(
                    name, row.when, "connector_tool",
                    f"{'POST' if uses_body else 'GET'} {CONNECTOR_BASE_URL_PLACEHOLDER}/tools/{name}"
                    + detail_extra,
                    _ENDPOINT_CONNECTOR_TOOLS,
                )
            )
        else:
            treatments.append(
                MbaToolTreatmentDTO(name, row.when, "unmapped", "Sin regla de mapeo: revisar.", None)
            )

    connector = MbaConnectorDTO(
        name="hubara-commerce",
        description=(
            "API de Hubara: catálogo, verificación y registro de pedidos y estado de "
            "envío, siempre para el cliente que está escribiendo."
        ),
        base_url=CONNECTOR_BASE_URL_PLACEHOLDER,
        auth_type="API_KEY",
        auth_header=CONNECTOR_AUTH_HEADER,
        requires_certificate=False,
        tools=tuple(connector_tools),
    )
    return connector, tuple(ui_skills), tuple(treatments), tuple(excluded)


def _escalation_skill(files: Mapping[str, str]) -> MbaSkillDTO | None:
    """La tabla 'Cuándo escalar a humano' de TOOLS.md como skill de MBA."""
    tools_md = files.get("TOOLS.md", "")
    for heading, lines in _sections(tools_md):
        if not re.search(r"escalar", heading, re.IGNORECASE):
            continue
        body_lines = [
            _clean_inline(line) for line in lines if line.strip() and not line.lstrip().startswith("|")
        ]
        for header, rows in _tables("\n".join(lines)):
            if len(header) < 2:
                continue
            body_lines += [
                f"- {_clean_inline(row[0])} → {_clean_inline(row[1])}" for row in rows if len(row) >= 2
            ]
        if not body_lines:
            return None
        return _make_skill(
            "escalacion-a-humano",
            "Aplicar cuando el caso cae en un trigger de negocio que requiere una persona "
            "del equipo: deriva al humano en vez de resolver.",
            [("TOOLS.md", "# Cuándo derivar a un humano\n\n" + "\n".join(body_lines))],
        )
    return None


# Secciones de TOOLS.md que YA viajan por otro lado (tabla de tools → connector /
# UI skills; etiquetas y escalación → sus skills; "Skills" → Knowledge).
_TOOLS_CONSUMED_HEADINGS = re.compile(r"tool|etiqueta|escalar|^skills$|lo que no va", re.IGNORECASE)


def _section_text_without_tables(heading: str, lines: list[str]) -> str:
    body = [ln for ln in lines if not ln.lstrip().startswith("|")]
    text = "\n".join(body).strip()
    if not text:
        return ""
    return f"## {heading}\n\n{text}" if heading else text


def _tool_usage_skill(sources: WorkspaceSources) -> MbaSkillDTO | None:
    """Las reglas de uso de tools que NO son tablas: secciones sueltas de
    TOOLS.md (principios, anti-alucinación, estilo…) + párrafos de instrucción
    de los skills de conocimiento fuera de sus secciones de business_info."""
    parts: list[tuple[str, str]] = []
    tools_md = sources.files.get("TOOLS.md")
    if tools_md is not None:
        chunks = [
            _section_text_without_tables(h, ln)
            for h, ln in _sections(tools_md)
            if not (h and _TOOLS_CONSUMED_HEADINGS.search(h))
        ]
        chunks = [c for c in chunks if c]
        if chunks:
            parts.append(("TOOLS.md", "\n\n".join(chunks)))
    consumed = business_info_consumed_sections(sources)
    for source, md in _knowledge_docs(sources):
        if source == "USER.md":
            continue  # USER.md viaja entero en contexto-del-negocio
        chunks = [
            _section_text_without_tables(h, ln)
            for h, ln in _sections(md)
            if h not in consumed.get(source, set())
        ]
        chunks = [c for c in chunks if c]
        if chunks:
            parts.append((source, "\n\n".join(chunks)))
    if not parts:
        return None
    return _make_skill(
        "uso-de-tools",
        "Aplicar cada vez que uses una tool o menciones productos, precios o políticas: "
        "reglas anti-alucinación (solo lo que devolvió la tool), principios de decisión "
        "y estilo al escribir tras un componente.",
        parts,
    )


def _tag_skill(files: Mapping[str, str]) -> MbaSkillDTO | None:
    """La taxonomía de etiquetas de TOOLS.md como skill, limitada a lo que MBA
    puede proponer. Los tags derivables los pone Hubara y se le dice que no los
    proponga."""
    tools_md = files.get("TOOLS.md", "")
    for heading, lines in _sections(tools_md):
        if not re.search(r"etiqueta", heading, re.IGNORECASE):
            continue
        body_lines: list[str] = []
        for line in lines:
            m = _BULLET.match(line)
            if not m:
                continue
            tag = _TAG_BULLET.match(m.group(2).strip())
            if tag and tag.group(1) in PROPOSABLE_TAGS:
                body_lines.append(f"- {tag.group(1)}: {_clean_inline(tag.group(2))}")
        if not body_lines:
            return None
        body = (
            "# Resultado de la conversación\n\n"
            "Cuando la conversación termina sin pedido, informa el resultado a Hubara "
            "con la tool manage_conversation_tag. Solo estos valores:\n\n"
            + "\n".join(body_lines)
            + "\n\nLos demás resultados (pedido confirmado, pago pendiente, compra "
            "exitosa) los determina Hubara automáticamente a partir del pedido "
            "registrado y del silencio del cliente: NO los propongas."
        )
        return _make_skill(
            "etiquetas-de-cierre",
            "Aplicar al cierre de la conversación cuando el cliente muestra interés sin "
            "comprar o descarta la compra: informa el resultado a Hubara.",
            [("TOOLS.md", body)],
        )
    return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


MBA_BASE_URL = "https://api.facebook.com"
MBA_API_VERSION = "2.0.0"
CONNECTOR_API_KEY_PLACEHOLDER = "<HUBARA_MBA_API_KEY>"
ALLOWLIST_PHONE_PLACEHOLDER = "+57XXXXXXXXXX"
# Nombre del parámetro que Meta rellena con el teléfono del cliente (macro).
CUSTOMER_PHONE_PARAM = "customer_phone"

_MBA_HEADERS: dict[str, str] = {
    "Authorization": "Bearer <META_ACCESS_TOKEN>",
    "X-API-Version": MBA_API_VERSION,
    "Content-Type": "application/json",
}


def _endpoint_url(section: str) -> tuple[str, str]:
    ep = next(e for e in ENDPOINTS if e.section == section)
    return ep.method, MBA_BASE_URL + ep.path


def _tool_param(name: str, description: str, *, required: bool = False) -> dict[str, Any]:
    return {"type": "string", "description": description, "required": required}


def _customer_phone_param() -> dict[str, Any]:
    return {
        "type": "string",
        "description": "Teléfono del cliente que escribe (lo inyecta Meta, E.164).",
        "required": True,
        "binding": {"kind": "macro", "macro": CUSTOMER_PHONE_MACRO},
    }


def _tool_request_definition(tool: MbaConnectorToolDTO) -> dict[str, Any]:
    llm_desc = "Lo extrae el agente de la conversación."
    rd: dict[str, Any] = {
        "method": tool.method,
        "path": tool.path,
        "path_parameters": {},
        "query_parameters": {},
        "headers": {},
    }
    if tool.method == "GET":
        rd["query_parameters"] = {CUSTOMER_PHONE_PARAM: _customer_phone_param()}
        for p in tool.query_parameters:
            rd["query_parameters"][p] = _tool_param(p, llm_desc)
    else:
        params: dict[str, Any] = {CUSTOMER_PHONE_PARAM: _customer_phone_param()}
        for p in tool.body_parameters:
            params[p] = _tool_param(p, llm_desc)
        rd["body"] = {
            "content_type": "application/json",
            "params": params,
            "required": [CUSTOMER_PHONE_PARAM],
        }
    return rd


def _build_requests(cfg: MbaConfigDTO) -> tuple[MbaRequestDTO, ...]:
    """Las llamadas exactas a Meta, en el orden en que se enviarían.

    Orden = el de la guía get-started: conocimiento (business_info, FAQs) →
    skills → connector → sus tools → UI skills → settings (rollout apagado) →
    allowlist. Un request por ítem: la API de Meta no acepta lotes.
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
    bi_body: dict[str, Any] = {}
    for key in (
        "business_description",
        "payment_method",
        "delivery_and_shipping",
        "return_policy",
        "purchase_info",
    ):
        value = getattr(bi, key)
        if value:
            bi_body[key] = value
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
    add(
        "business_info",
        "business_info",
        bi_body,
        "PUT reemplaza TODO el bloque; los campos sin fuente no viajan.",
    )

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

    if cfg.connector is not None:
        con = cfg.connector
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
                            {
                                "field_name": con.auth_header,
                                "value": CONNECTOR_API_KEY_PLACEHOLDER,
                                "prefix": "",
                            }
                        ],
                        "query_params": [],
                    }
                },
                "requires_certificate": con.requires_certificate,
            },
            "La respuesta trae el `id` del connector: es el {connector_id} de las tools.",
        )
        for tool in con.tools:
            add(
                "connector_tools",
                tool.name,
                {
                    "name": tool.name,
                    "description": tool.description,
                    "request_definition": _tool_request_definition(tool),
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
    handoff: dict[str, Any] = {
        "enabled": st.handoff.enabled,
        "message_selection": st.handoff.message_selection,
    }
    if st.handoff.message:
        handoff["message"] = st.handoff.message
    followup: dict[str, Any] = {"enabled": st.followup.enabled}
    if st.followup.enabled:
        followup["followup_interval_in_seconds"] = st.followup.followup_interval_in_seconds
        if st.followup.message:
            followup["message"] = st.followup.message
    phrases: list[str] = []
    for p in st.never_say_phrases:
        if p.phrase not in phrases:
            phrases.append(p.phrase)
    add(
        "settings",
        "settings",
        {
            "rollout": {"enabled": st.rollout_enabled},
            "ai_audience": st.ai_audience,
            "handoff": handoff,
            "followup": followup,
            "never_say_phrases": phrases,
        },
        "Va al final: con rollout.enabled=false MBA no responde a nadie hasta que se "
        "prenda a mano. never_say_phrases reemplaza la lista completa.",
    )

    add(
        "allowlist",
        "allowlist",
        {"consumer_phone_number": ALLOWLIST_PHONE_PLACEHOLDER},
        "Único valor que NO sale del workspace: los teléfonos de prueba de F0 "
        "(uno por request, E.164). Con ai_audience=ALLOWLISTED_ONLY solo ellos "
        "hablan con MBA y no hay facturación.",
    )
    return tuple(out)


def normalize_mba_config(
    agent_id: str, sources: WorkspaceSources, workspace: str = ""
) -> MbaConfigDTO:
    faqs, faq_excluded = _build_faqs(sources)
    handoff_msg = _handoff_message(sources.files)
    settings = MbaSettingsDTO(
        rollout_enabled=False,
        ai_audience="ALLOWLISTED_ONLY",
        handoff=MbaHandoffDTO(
            enabled=True,
            message=handoff_msg,
            message_selection="CUSTOM" if handoff_msg else "DEFAULT",
        ),
        # Los seguimientos siguen en Hubara (Window Strategist + templates fuera
        # de ventana): el followup nativo de MBA queda apagado para no duplicar.
        followup=MbaFollowupDTO(enabled=False, followup_interval_in_seconds=900, message=None),
        never_say_phrases=_build_never_say(sources),
    )
    connector, ui_skills, treatments, tool_excluded = _build_tools(sources)
    skills = _build_skills(sources)
    for extra_skill in (
        _tool_usage_skill(sources),
        _escalation_skill(sources.files),
        _tag_skill(sources.files),
    ):
        if extra_skill is not None:
            skills = skills + (extra_skill,)
    cfg = MbaConfigDTO(
        agent_id=agent_id,
        channel="whatsapp",
        business_info=_build_business_info(sources),
        settings=settings,
        skills=skills,
        faqs=faqs,
        connector=connector,
        ui_skills=ui_skills,
        tool_treatments=treatments,
        excluded=_STATIC_EXCLUSIONS + faq_excluded + tool_excluded,
        endpoints=ENDPOINTS,
        workspace=workspace,
    )
    return replace(cfg, requests=_build_requests(cfg))
