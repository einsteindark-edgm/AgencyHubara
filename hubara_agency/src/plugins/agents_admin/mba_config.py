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
from dataclasses import dataclass, field
from typing import Iterator, Mapping

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
class MbaConfigDTO:
    agent_id: str
    channel: str
    business_info: MbaBusinessInfoDTO
    settings: MbaSettingsDTO
    skills: tuple[MbaSkillDTO, ...] = field(default_factory=tuple)
    faqs: tuple[MbaFaqDTO, ...] = field(default_factory=tuple)
    excluded: tuple[MbaExcludedDTO, ...] = field(default_factory=tuple)
    endpoints: tuple[MbaEndpointDTO, ...] = field(default_factory=tuple)


ENDPOINTS: tuple[MbaEndpointDTO, ...] = (
    MbaEndpointDTO("skills", "POST", "/{entity_id}/agent_config/skills"),
    MbaEndpointDTO("business_info", "PUT", "/{entity_id}/agent_config/business_info"),
    MbaEndpointDTO("faqs", "POST", "/{entity_id}/agent_config/faq"),
    MbaEndpointDTO("settings", "PUT", "/{entity_id}/agent_config/settings"),
    MbaEndpointDTO("allowlist", "POST", "/{entity_id}/agent_config/allowlist"),
)

_STATIC_EXCLUSIONS: tuple[MbaExcludedDTO, ...] = (
    MbaExcludedDTO(
        "TOOLS.md",
        "Mapa de tools del LLM: en MBA se modela como connector tools "
        "(desarrollo 2), no como texto de skill.",
    ),
    MbaExcludedDTO(
        "memory/MEMORY.md · memory/HISTORY.md",
        "Memoria dinámica del runtime; MBA mantiene su propio contexto por hilo.",
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
        parts = [(_skill_source(core), core.body)]
        parts += [(_skill_source(st), st.body) for st in stages]
        out.append(_make_skill(f"guion-{core.name}", core.description, parts))

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


def _bullets_with_fields(md: str) -> Iterator[tuple[str, str]]:
    """Yields ``(field, texto limpio)`` por cada bullet clasificable."""
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
            yield fld, text


def _build_business_info(sources: WorkspaceSources) -> MbaBusinessInfoDTO:
    buckets: dict[str, list[str]] = {name: [] for name in _BI_FIELDS}
    used: list[str] = []
    docs = [("USER.md", sources.files["USER.md"])] if "USER.md" in sources.files else []
    docs += [(_skill_source(s), s.body) for s in sources.skills if _is_knowledge_skill(s)]
    for source, md in docs:
        touched = False
        for fld, text in _bullets_with_fields(md):
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
# Entry point
# ---------------------------------------------------------------------------


def normalize_mba_config(agent_id: str, sources: WorkspaceSources) -> MbaConfigDTO:
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
    return MbaConfigDTO(
        agent_id=agent_id,
        channel="whatsapp",
        business_info=_build_business_info(sources),
        settings=settings,
        skills=_build_skills(sources),
        faqs=faqs,
        excluded=_STATIC_EXCLUSIONS + faq_excluded,
        endpoints=ENDPOINTS,
    )
