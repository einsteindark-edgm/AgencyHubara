"""Tests de la normalización *workspace → configuración Meta Business Agent*
del plugin ``agents_admin``.

La función bajo prueba es PURA: recibe los archivos del workspace de un agente
(bootstrap .md + skills con front-matter) y devuelve el DTO con la forma EXACTA
de los endpoints de configuración de MBA (skills, business_info, faqs,
settings). Sin I/O, sin LLM: dado el mismo workspace, siempre el mismo output.
"""
from __future__ import annotations

from src.plugins.agents_admin.mba_config import (
    WorkspaceSkill,
    WorkspaceSources,
    normalize_mba_config,
)

_IDENTITY = "# Eres el Asesor\n\nEres un experto de ventas de una marca premium."
_SOUL = "# Soul\n\n## Personalidad\n\nSereno, contenido, formal."
_AGENTS = "# Agent rules\n\n## Turn structure\n\n- Responde directo al usuario."
_USER = "# User Profile\n\n- **Nombre**: Hubara"


def _sources(**overrides) -> WorkspaceSources:
    files = {
        "IDENTITY.md": _IDENTITY,
        "SOUL.md": _SOUL,
        "AGENTS.md": _AGENTS,
        "USER.md": _USER,
        "TOOLS.md": "# Tools\n\ntabla de tools",
    }
    files.update(overrides.pop("files", {}))
    return WorkspaceSources(files=files, skills=tuple(overrides.pop("skills", ())))


# ── Skills ────────────────────────────────────────────────────────────────


def test_persona_skill_concatenates_identity_and_soul_with_sources() -> None:
    cfg = normalize_mba_config("sales", _sources())

    persona = next(s for s in cfg.skills if s.title == "persona-y-tono")
    assert persona.sources == ("IDENTITY.md", "SOUL.md")
    # el cuerpo es el contenido REAL de ambos archivos, identidad primero
    assert persona.skill.index("Eres un experto de ventas") < persona.skill.index(
        "Sereno, contenido, formal."
    )
    assert persona.char_count == len(persona.skill)
    assert persona.char_limit == 20000
    assert persona.over_limit is False
    # descripción = cuándo aplicar (contrato MBA: ≤1024 chars)
    assert persona.description and len(persona.description) <= 1024


def test_operational_rules_skill_comes_from_agents_md() -> None:
    cfg = normalize_mba_config("sales", _sources())

    rules = next(s for s in cfg.skills if s.title == "reglas-operativas")
    assert rules.sources == ("AGENTS.md",)
    assert "Responde directo al usuario." in rules.skill


def test_script_skill_orders_always_skill_then_stages_canonically() -> None:
    skills = (
        WorkspaceSkill(
            name="etapa_cierre",
            description="Guion de etapa - cierre.",
            always=False,
            body="# Etapa: Cierre\n\nCierra sin saltar pasos.",
        ),
        WorkspaceSkill(
            name="sales_script",
            description="Núcleo del guion conversacional.",
            always=True,
            body="# Guion núcleo\n\nMapa del funnel.",
        ),
        WorkspaceSkill(
            name="etapa_descubrimiento",
            description="Guion de etapa - descubrimiento.",
            always=False,
            body="# Etapa: Descubrimiento\n\nSaluda por hora.",
        ),
    )
    cfg = normalize_mba_config("sales", _sources(skills=skills))

    script = next(s for s in cfg.skills if s.title == "guion-sales-script")
    assert script.sources == (
        "skills/sales_script/SKILL.md",
        "skills/etapa_descubrimiento/SKILL.md",
        "skills/etapa_cierre/SKILL.md",
    )
    body = script.skill
    assert body.index("Mapa del funnel.") < body.index("Saluda por hora.") < body.index(
        "Cierra sin saltar pasos."
    )
    # la descripción de MBA sale del front-matter del skill always
    assert script.description == "Núcleo del guion conversacional."


def test_skill_over_20000_chars_is_flagged_not_truncated() -> None:
    big_soul = "# Soul\n\n" + ("x" * 20500)
    cfg = normalize_mba_config("sales", _sources(files={"SOUL.md": big_soul}))

    persona = next(s for s in cfg.skills if s.title == "persona-y-tono")
    assert persona.over_limit is True
    assert persona.char_count > 20000
    assert "x" * 20500 in persona.skill  # no se recorta en silencio


def test_skill_titles_are_valid_mba_titles() -> None:
    """Contrato MBA: ≤64 chars, minúsculas/números/guiones, sin guion al borde."""
    skills = (
        WorkspaceSkill(
            name="Notas_Olfativas",
            description="Pirámides olfativas por aroma.",
            always=False,
            body="# Notas\n\nLavanda: salida cítrica.",
        ),
    )
    cfg = normalize_mba_config("sales", _sources(skills=skills))

    for s in cfg.skills:
        assert len(s.title) <= 64
        assert s.title == s.title.lower()
        assert not s.title.startswith("-") and not s.title.endswith("-")
        assert all(ch.isalnum() or ch == "-" for ch in s.title)
    assert any(s.title == "conocimiento-notas-olfativas" for s in cfg.skills)


# ── FAQs (tablas de objeciones → POST /agent_config/faq) ────────────────

_SCRIPT_WITH_OBJECTIONS = """# Guion

## Manejo de objeciones (en cualquier etapa)

| Objeción | Respuesta (adapta al hilo, no copies literal) |
|---|---|
| "Está caro." | "Entiendo. La diferencia está en la cera de palma 100% vegetal." |
| "¿Cuánto demora el envío?" | "Bogotá 1 a 2 días hábiles. Resto del país 2 a 3 días hábiles." (`load_skill("hubara_catalog")` si pide más detalle) |
| "¿Tienen descuentos?" | `escalate_to_human("DISCOUNT_REQUEST")` — no negocias precios. |
| Por mayor / B2B / evento | `escalate_to_human("BULK_ORDER")`. |

## Tagging al cerrar

| Caso | Acción |
|---|---|
| Interesado | `manage_conversation_tag("INTERESADO")` |
"""


def test_faqs_come_from_objection_tables_without_tool_call_rows() -> None:
    skills = (
        WorkspaceSkill(
            name="sales_script", description="Núcleo.", always=True, body=_SCRIPT_WITH_OBJECTIONS
        ),
    )
    cfg = normalize_mba_config("sales", _sources(skills=skills))

    faqs = {f.question: f for f in cfg.faqs}
    assert set(faqs) == {"Está caro.", "¿Cuánto demora el envío?"}
    assert faqs["Está caro."].answer == (
        "Entiendo. La diferencia está en la cera de palma 100% vegetal."
    )
    # la respuesta se limpia de notas internas para el LLM (backticks/tools)
    assert faqs["¿Cuánto demora el envío?"].answer == (
        "Bogotá 1 a 2 días hábiles. Resto del país 2 a 3 días hábiles."
    )
    assert faqs["Está caro."].source == "skills/sales_script/SKILL.md"
    # las filas que resuelven con una tool NO son FAQ: quedan visibles como excluidas
    excluded = {e.source: e.reason for e in cfg.excluded}
    assert any("¿Tienen descuentos?" in src for src in excluded)
    # una tabla Caso|Acción (tagging) no es una tabla de FAQs
    assert "Interesado" not in faqs


# ── never_say_phrases (settings) ─────────────────────────────────────────

_IDENTITY_WITH_TABLE = """# Identidad

## REGLA #1 — Dialecto colombiano

| Rioplatense PROHIBIDO | Colombiano OBLIGATORIO |
|---|---|
| vos / sos | tú / eres |
| ahorita (en sentido rioplatense) | ahora / en un momento |

Si el cliente pregunta "¿eres un bot?", desvías con naturalidad ("Soy parte del equipo de Hubara").
"""

_SOUL_WITH_PROHIBITIONS = """# Soul

**NO tono Hubara**: efusivo, con diminutivos ("rapidito", "veladita"), con muletillas tipo "¡Qué frescura!".

🚫 PROHIBIDO al inicio del `content`:
- `Here's my attempt:`, `Sure!`, `Okay,`
- `Aquí va:`, `Mi respuesta:`

- 🚫 **Los datos de envío salen SOLO del formulario.** Si dudas, PREGUNTA ("¿Te lo enviamos a la misma dirección?") y espera el sí.
- **Habla como se habla**: "te confirmo en un momento". Nada de call center ("¿en qué más puedo colaborarle?").
"""

_AGENTS_WITH_PROMISES = """# Agent rules

## Promesas offline (PROHIBIDO ABSOLUTO)

- NUNCA digas "voy a averiguar", "déjame revisar", "te confirmo en un rato".

## Escalación

- **Escalación a humano**: envías UN último mensaje breve, por ejemplo *"Un colega del equipo te responde en este mismo chat 🤍"*, y luego llamas la tool.
"""


def test_never_say_phrases_extracted_only_from_prohibition_led_lines() -> None:
    cfg = normalize_mba_config(
        "sales",
        _sources(
            files={
                "IDENTITY.md": _IDENTITY_WITH_TABLE,
                "SOUL.md": _SOUL_WITH_PROHIBITIONS,
                "AGENTS.md": _AGENTS_WITH_PROMISES,
            }
        ),
    )
    phrases = [p.phrase for p in cfg.settings.never_say_phrases]

    # tabla PROHIBIDO: cada celda de la columna prohibida, sin paréntesis
    for expected in ("vos", "sos", "ahorita"):
        assert expected in phrases
    # bullets encabezados por NUNCA / NO tono / 🚫
    for expected in (
        "voy a averiguar",
        "déjame revisar",
        "te confirmo en un rato",
        "rapidito",
        "¡Qué frescura!",
        "Here's my attempt:",
        "Sure!",
        "Aquí va:",
    ):
        assert expected in phrases
    # lo que NO debe entrar: ejemplos de uso correcto, preguntas, columnas permitidas
    for forbidden in (
        "Soy parte del equipo de Hubara",
        "¿Te lo enviamos a la misma dirección?",
        "te confirmo en un momento",
        "tú",
        "content",
    ):
        assert forbidden not in phrases
    # sin duplicados y con trazabilidad
    assert len(phrases) == len({p.lower() for p in phrases})
    src = next(p.source for p in cfg.settings.never_say_phrases if p.phrase == "vos")
    assert src == "IDENTITY.md"


def test_handoff_message_is_the_quoted_example_of_the_escalation_section() -> None:
    cfg = normalize_mba_config("sales", _sources(files={"AGENTS.md": _AGENTS_WITH_PROMISES}))

    assert cfg.settings.handoff.enabled is True
    assert cfg.settings.handoff.message_selection == "CUSTOM"
    assert cfg.settings.handoff.message == (
        "Un colega del equipo te responde en este mismo chat 🤍"
    )


def test_handoff_falls_back_to_default_when_no_example() -> None:
    cfg = normalize_mba_config("sales", _sources(files={"AGENTS.md": _AGENTS}))

    assert cfg.settings.handoff.message is None
    assert cfg.settings.handoff.message_selection == "DEFAULT"


def test_f0_defaults_keep_agent_off_and_allowlisted_without_followup() -> None:
    cfg = normalize_mba_config("sales", _sources())

    assert cfg.settings.rollout_enabled is False
    assert cfg.settings.ai_audience == "ALLOWLISTED_ONLY"
    # los seguimientos siguen en Hubara (Window Strategist): MBA followup apagado
    assert cfg.settings.followup.enabled is False


# ── business_info (PUT /agent_config/business_info) ──────────────────────

_USER_WITH_FACTS = """# User Profile

## Tenant / Organización

- **Nombre**: Hubara
- **Industria**: marca premium colombiana de velas artesanales (cera de palma)
- **Zona horaria por defecto**: `America/Bogota` (UTC-5)

## Hechos conocidos que el agente puede asumir

- Todos los precios están en COP (pesos colombianos).
- Envíos: solo nacional (Colombia).
- El horario laboral del equipo es zona horaria de Colombia (`America/Bogota`).
"""

_CATALOG_SKILL = """# Conocimiento Central

## IDENTIDAD DE MARCA

- **Ingredientes puros**: cera de palma 100% origen vegetal.

## ENVÍOS Y PAGOS (políticas estables)

- **Envíos a Bogotá**: $12.000 a $15.000 aprox. 1 a 2 días hábiles.
- **Formas de pago** (infórmalas así, son las TRES únicas):
  - **Contra entrega**: solo compras mayores a $45.000 COP.
  - **Pago anticipado**: por Nequi o llave **3229041190**.

## POLÍTICAS ADICIONALES

- **Descuento de Bienvenida**: 5% automático para primeras compras a través de la web.
- **Garantía**: 48 horas de cobertura para envíos rotos o defectuosos.

## Cómo conseguir productos / precios

| Necesitas | Tool a usar |
|---|---|
| Listar productos | `search_products(q="lavanda")` |
"""


def test_business_info_classifies_bullets_by_field_and_strips_markdown() -> None:
    skills = (
        WorkspaceSkill(
            name="hubara_catalog",
            description="Identidad de marca + políticas.",
            always=False,
            body=_CATALOG_SKILL,
        ),
    )
    cfg = normalize_mba_config(
        "sales", _sources(files={"USER.md": _USER_WITH_FACTS}, skills=skills)
    )
    bi = cfg.business_info

    assert "Nombre: Hubara" in bi.business_description
    assert "cera de palma 100% origen vegetal" in bi.business_description
    assert "**" not in bi.business_description

    assert "Envíos a Bogotá: $12.000 a $15.000 aprox. 1 a 2 días hábiles." in bi.delivery_and_shipping
    assert "Envíos: solo nacional (Colombia)." in bi.delivery_and_shipping

    assert "Contra entrega: solo compras mayores a $45.000 COP." in bi.payment_method
    assert "Pago anticipado: por Nequi o llave 3229041190." in bi.payment_method
    assert "Todos los precios están en COP (pesos colombianos)." in bi.payment_method

    assert "Garantía: 48 horas" in bi.return_policy
    assert "Descuento de Bienvenida: 5%" in bi.purchase_info

    assert "America/Bogota" in bi.contact_info.hours_of_operation
    assert bi.contact_info.email is None
    assert bi.contact_info.address is None

    # la tabla de tools NO es conocimiento del negocio
    assert "search_products" not in (
        bi.business_description + bi.payment_method + bi.purchase_info
    )
    assert set(bi.sources) == {"USER.md", "skills/hubara_catalog/SKILL.md"}


def test_knowledge_skill_feeds_business_info_and_is_not_emitted_as_skill() -> None:
    skills = (
        WorkspaceSkill(
            name="hubara_catalog", description="Políticas.", always=False, body=_CATALOG_SKILL
        ),
        WorkspaceSkill(
            name="notas_olfativas", description="Aromas.", always=False, body="# Notas\n\n## Lavanda\n\nsalida cítrica."
        ),
    )
    cfg = normalize_mba_config("sales", _sources(skills=skills))

    titles = {s.title for s in cfg.skills}
    assert "conocimiento-hubara-catalog" not in titles
    assert "conocimiento-notas-olfativas" in titles


# ── Exclusiones y endpoints (trazabilidad para la UI) ────────────────────


def test_tools_md_is_excluded_with_reason_and_endpoints_listed() -> None:
    cfg = normalize_mba_config("sales", _sources())

    reasons = {e.source: e.reason for e in cfg.excluded}
    assert "TOOLS.md" in reasons and "connector" in reasons["TOOLS.md"].lower()

    paths = {(e.method, e.path) for e in cfg.endpoints}
    assert ("POST", "/{entity_id}/agent_config/skills") in paths
    assert ("PUT", "/{entity_id}/agent_config/business_info") in paths
    assert ("POST", "/{entity_id}/agent_config/faq") in paths
    assert ("PUT", "/{entity_id}/agent_config/settings") in paths


# ── Lectura del workspace (I/O) + endpoint ───────────────────────────────

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.plugins.agents_admin import service  # noqa: E402
from src.plugins.agents_admin.api import router  # noqa: E402


def test_read_workspace_sources_parses_skill_frontmatter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "pkg" / "agent" / "foo" / "workspace"
    (ws / "skills" / "guion").mkdir(parents=True)
    (ws / "skills" / "etapa_cierre").mkdir(parents=True)
    (ws / "memory").mkdir()
    (ws / "IDENTITY.md").write_text("# Id\n\nSoy foo.", encoding="utf-8")
    (ws / "SOUL.md").write_text("# Soul\n\nSereno.", encoding="utf-8")
    (ws / "memory" / "MEMORY.md").write_text("secreto del cliente", encoding="utf-8")
    (ws / "skills" / "guion" / "SKILL.md").write_text(
        '---\ndescription: Núcleo del guion.\nmetadata: {"exoclaw": {"always": true}}\n---\n\n# Guion\n\nCuerpo.',
        encoding="utf-8",
    )
    (ws / "skills" / "etapa_cierre" / "SKILL.md").write_text(
        "---\ndescription: Etapa cierre.\n---\n\n# Cierre\n\nCierra.", encoding="utf-8"
    )
    monkeypatch.setattr(service, "_REPO_ROOT", tmp_path)

    src = service.read_workspace_sources("pkg/agent/foo/workspace")

    assert src.files["IDENTITY.md"] == "# Id\n\nSoy foo."
    assert "memory/MEMORY.md" not in src.files and "MEMORY.md" not in src.files
    by_name = {s.name: s for s in src.skills}
    assert by_name["guion"].always is True
    assert by_name["guion"].description == "Núcleo del guion."
    assert by_name["guion"].body.strip() == "# Guion\n\nCuerpo."  # sin front-matter
    assert by_name["etapa_cierre"].always is False


def test_real_sales_workspace_normalizes_end_to_end() -> None:
    """Integración con el workspace VIVO de sales: lo que la UI va a mostrar."""
    cfg = service.build_mba_config("sales")
    assert cfg is not None

    titles = [s.title for s in cfg.skills]
    assert titles[:3] == ["persona-y-tono", "reglas-operativas", "guion-sales-script"]
    guion = cfg.skills[2]
    # documenta el presupuesto real: guion + 5 etapas hoy supera los 20k
    assert guion.over_limit is True

    questions = {f.question for f in cfg.faqs}
    assert "¿Cómo puedo pagar?" in questions
    assert "¿Cuánto demora el envío?" in questions

    phrases = {p.phrase for p in cfg.settings.never_say_phrases}
    assert {"vos", "voy a averiguar", "rapidito"} <= phrases

    assert "3229041190" in cfg.business_info.payment_method
    assert "1 a 2 días hábiles" in cfg.business_info.delivery_and_shipping
    assert cfg.settings.handoff.message and "colega" in cfg.settings.handoff.message


def test_build_mba_config_unknown_agent_returns_none() -> None:
    assert service.build_mba_config("no-existe") is None


def test_endpoint_mba_config_404_and_200() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api/agents", tags=["Agents"])
    client = TestClient(app)

    assert client.get("/api/agents/no-existe/mba-config").status_code == 404

    resp = client.get("/api/agents/sales/mba-config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_id"] == "sales"
    assert data["channel"] == "whatsapp"
    assert {"skills", "faqs", "business_info", "settings", "excluded", "endpoints"} <= set(data)
    assert data["settings"]["handoff"]["message_selection"] == "CUSTOM"
    assert isinstance(data["settings"]["never_say_phrases"], list)


# ── Guards de calidad (fugas vistas en el workspace real de sales) ────────

_STAGE_WITH_PAYMENT_HEADING = """# Etapa: datos de envío

## Método de pago

- Cliente cambia el método de pago → `set_order_slot(metodo_pago=...)` y confirma.
- Envíos: el cliente da datos ANTES de esta etapa → recíbelos igual (`set_order_slot`).
"""


def test_stage_skills_never_feed_business_info() -> None:
    skills = (
        WorkspaceSkill(
            name="etapa_datos_envio", description="Etapa.", always=False, body=_STAGE_WITH_PAYMENT_HEADING
        ),
        WorkspaceSkill(
            name="sales_script", description="Núcleo.", always=True,
            body="# Guion\n\n## Pagos\n\n- Formas de pago: solo lo que diga el catálogo.",
        ),
    )
    cfg = normalize_mba_config("sales", _sources(skills=skills))

    assert "set_order_slot" not in cfg.business_info.payment_method
    assert "recíbelos igual" not in cfg.business_info.delivery_and_shipping
    assert "solo lo que diga el catálogo" not in cfg.business_info.payment_method
    assert "skills/etapa_datos_envio/SKILL.md" not in cfg.business_info.sources


def test_business_info_skips_bullets_that_are_tool_instructions() -> None:
    body = """# Conocimiento

## ENVÍOS Y PAGOS

- **Envíos a Bogotá**: 1 a 2 días hábiles.
- Si cambia el método → `set_order_slot(metodo_pago=...)` y verifica el umbral.
"""
    skills = (WorkspaceSkill(name="hubara_catalog", description="P.", always=False, body=body),)
    cfg = normalize_mba_config("sales", _sources(skills=skills))

    assert "Envíos a Bogotá: 1 a 2 días hábiles." in cfg.business_info.delivery_and_shipping
    assert "set_order_slot" not in (
        cfg.business_info.delivery_and_shipping + cfg.business_info.payment_method
    )


def test_never_say_ignores_cross_references_and_later_sentences() -> None:
    agents = """# Agent rules

## Promesas offline (PROHIBIDO ABSOLUTO)

- NUNCA digas "voy a averiguar", "ahora vuelvo". No tienes I/O asíncrono. Toda promesa de "responder después" es incumplible.

## Channel etiquette

- **Sin em dash (—)** en respuestas al cliente (ver `SOUL.md` → "Puntuación natural").
- **Sin voseo rioplatense** (ver `IDENTITY.md` → "REGLA #1").
"""
    cfg = normalize_mba_config("sales", _sources(files={"AGENTS.md": agents}))
    phrases = [p.phrase for p in cfg.settings.never_say_phrases]

    assert "voy a averiguar" in phrases and "ahora vuelvo" in phrases
    for noise in ("responder después", "Puntuación natural", "REGLA #1"):
        assert noise not in phrases


def test_never_say_table_drops_phrases_also_allowed_in_the_same_row() -> None:
    identity = """# Identidad

| Rioplatense PROHIBIDO | Colombiano OBLIGATORIO |
|---|---|
| pedile / mandale / esperá / dale | pídele / mándale / espera / dale |
"""
    cfg = normalize_mba_config("sales", _sources(files={"IDENTITY.md": identity}))
    phrases = [p.phrase for p in cfg.settings.never_say_phrases]

    assert {"pedile", "mandale", "esperá"} <= set(phrases)
    assert "dale" not in phrases


# ── Connector + tools (TOOLS.md → connector tools / UI skills / nativo) ───

_TOOLS_MD = """# Tools

## Mapa rápido de tools

| Tool | Cuándo | Clave |
|---|---|---|
| `search_products` | SIEMPRE antes de nombrar/preciar un producto. `q=""` + `limit=30` = todo el catálogo | El envelope trae `aromas` |
| `search_products(category=...)` | Cliente pide "las religiosas" | La categoría va en `category=` TAL CUAL |
| `get_product_by_handle` | Detalle/variantes de un producto YA visto en search | NUNCA inventes el handle |
| `present_products` ⛔ | 4+ productos (catálogo) | TODO el mensaje va en `intro_text` |
| `request_shipping_details` ⛔ | Variantes completas → pedir datos de envío | UNA vez por sesión |
| `set_order_slot` | CADA dato confirmado del pedido | memoria del pedido |
| `verify_order_for_checkout` | OBLIGATORIA antes de confirmar el pedido | `discrepancy=true` → avisa |
| `register_order` | Cliente tocó '✅ Confirmar' + datos completos | Sin esto el pedido NO existe |
| `manage_conversation_tag` | Al cerrar la conversación (obligatorio) | Taxonomía abajo |
| `escalate_to_human` | Tabla de triggers abajo | Antes: UNA línea al cliente |
| `check_order_status` | Cliente pregunta por su pedido (etapa o pago) | Trae `pay_status` real |
| `send_cta_url` | Cliente pide un link que NO es producto | `/checkout` bloqueada |
| `send_contact_card` | Cliente PIDE el número del asesor | No como atajo |
| `react_to_message` | Ack visual rápido (ej. tras submit del Flow → 🤍) | Con moderación |

## Cuándo escalar a humano (`escalate_to_human`)

| Trigger | `reason_category` |
|---|---|
| Pide >20 unidades | `BULK_ORDER` |
| Pide descuento explícito | `DISCOUNT_REQUEST` |
"""


def _cfg_with_tools():
    return normalize_mba_config("sales", _sources(files={"TOOLS.md": _TOOLS_MD}))


def test_read_tools_become_connector_tools_with_params_and_phone_binding() -> None:
    cfg = _cfg_with_tools()
    assert cfg.connector is not None
    tools = {t.name: t for t in cfg.connector.tools}

    search = tools["search_products"]
    assert search.method == "GET"
    assert search.path == "/tools/search_products"
    # params leídos de la fila de TOOLS.md (las dos filas de search_products se unen)
    assert set(search.query_parameters) == {"q", "limit", "category"}
    assert search.write is False
    # la descripción sale de la columna "Cuándo" (es lo que MBA usa para decidir)
    assert "antes de nombrar" in search.description
    # cada tool va scoped al cliente que escribe: macro de Meta
    assert "WHATSAPP_PHONE_NUMBER" in search.bindings

    status = tools["check_order_status"]
    assert status.method == "GET" and status.query_parameters == ()
    # `discrepancy=true` es un flag de salida, no un parámetro
    assert tools["verify_order_for_checkout"].method == "POST"
    assert "discrepancy" not in tools["verify_order_for_checkout"].body_parameters


def test_write_tools_are_post_and_flagged_for_idempotency() -> None:
    cfg = _cfg_with_tools()
    register = next(t for t in cfg.connector.tools if t.name == "register_order")
    assert register.method == "POST" and register.write is True
    assert "idempot" in register.notes.lower()


def test_ui_tools_map_to_native_ui_skills_by_component_type() -> None:
    cfg = _cfg_with_tools()
    ui = {u.from_tool: u for u in cfg.ui_skills}
    assert ui["present_products"].component_type == "carousel_quick_reply"
    assert ui["request_shipping_details"].component_type == "flow"
    assert ui["send_cta_url"].component_type == "cta_url"
    # la instrucción de cuándo enviarla sale de la columna "Cuándo"
    assert "datos de envío" in ui["request_shipping_details"].instruction
    # las tools de UI NO son connector tools
    assert "present_products" not in {t.name for t in cfg.connector.tools}


def test_every_llm_tool_has_a_meta_destination() -> None:
    cfg = _cfg_with_tools()
    by_tool = {t.llm_tool: t for t in cfg.tool_treatments}
    treatments = {k: v.treatment for k, v in by_tool.items()}
    assert treatments["search_products"] == "connector_tool"
    assert treatments["register_order"] == "connector_tool"
    assert treatments["present_products"] == "ui_skill"
    # las tools de estado de Hubara TAMBIÉN viajan: MBA le avisa a Hubara el
    # resultado del funnel (INTERESADO → remarketing, HUMANO → bandeja)
    assert treatments["escalate_to_human"] == "connector_tool"
    assert treatments["set_order_slot"] == "connector_tool"
    assert treatments["manage_conversation_tag"] == "connector_tool"
    assert treatments["send_contact_card"] == "ui_skill"
    # la única sin destino, con motivo explícito y verificable en F0
    assert treatments["react_to_message"] == "unmapped"
    assert "hilo" in by_tool["react_to_message"].detail
    # cada tratamiento dice a qué endpoint de Meta va
    assert by_tool["search_products"].endpoint == "/{entity_id}/agent_connectors/{connector_id}/tools"
    assert by_tool["present_products"].endpoint == "/{entity_id}/agent-ui-skills"
    assert by_tool["react_to_message"].endpoint is None
    # cobertura total: ninguna tool del mapa queda sin decidir
    assert set(treatments) == {
        "search_products", "get_product_by_handle", "present_products",
        "request_shipping_details", "set_order_slot", "verify_order_for_checkout",
        "register_order", "manage_conversation_tag", "escalate_to_human",
        "check_order_status", "send_cta_url", "send_contact_card", "react_to_message",
    }


def test_state_tools_are_write_connector_tools_with_hubara_semantics() -> None:
    cfg = _cfg_with_tools()
    tools = {t.name: t for t in cfg.connector.tools}
    for name in ("escalate_to_human", "manage_conversation_tag", "set_order_slot"):
        assert tools[name].method == "POST" and tools[name].write is True
    # escalar desde el connector implica que Hubara toma el hilo (mensaje de handoff)
    assert "hilo" in tools["escalate_to_human"].notes
    # etiquetar es lo que dispara remarketing / bandeja en Hubara
    assert "remarketing" in tools["manage_conversation_tag"].notes.lower()


def test_ui_skills_distinguish_static_from_dynamic_with_f0_note() -> None:
    cfg = _cfg_with_tools()
    ui = {u.from_tool: u for u in cfg.ui_skills}
    # estáticas: la instrucción contiene todo lo que el componente necesita
    assert ui["request_shipping_details"].kind == "static"
    assert ui["send_cta_url"].kind == "static"
    assert ui["send_contact_card"].kind == "static"
    assert ui["send_contact_card"].component_type == "cta_url"
    # dinámicas: dependen de datos del catálogo / connector → a verificar en F0
    assert ui["present_products"].kind == "dynamic"
    assert "F0" in ui["present_products"].note
    assert ui["request_shipping_details"].note == ""


def test_escalation_table_becomes_a_skill_and_tools_md_is_no_longer_excluded() -> None:
    cfg = _cfg_with_tools()
    esc = next(s for s in cfg.skills if s.title == "escalacion-a-humano")
    assert "BULK_ORDER" in esc.skill and "Pide >20 unidades" in esc.skill
    assert esc.sources == ("TOOLS.md",)
    assert "TOOLS.md" not in {e.source for e in cfg.excluded}
    # solo react_to_message queda fuera, con motivo
    tool_exclusions = {e.source for e in cfg.excluded if e.source.startswith("TOOLS.md#tool:")}
    assert tool_exclusions == {"TOOLS.md#tool:react_to_message"}

    paths = {(e.method, e.path) for e in cfg.endpoints}
    assert ("POST", "/{entity_id}/agent_connectors") in paths
    assert ("POST", "/{entity_id}/agent_connectors/{connector_id}/tools") in paths
    assert ("POST", "/{entity_id}/agent-ui-skills") in paths


def test_connector_declares_api_key_auth_and_placeholder_base_url() -> None:
    cfg = _cfg_with_tools()
    c = cfg.connector
    assert c.auth_type == "API_KEY"
    assert c.auth_header == "X-API-Key"
    assert c.requires_certificate is False
    assert "/api/mba" in c.base_url


def test_workspace_without_tools_md_has_no_connector() -> None:
    files = {"IDENTITY.md": _IDENTITY, "SOUL.md": _SOUL}
    cfg = normalize_mba_config("x", WorkspaceSources(files=files))
    assert cfg.connector is None and cfg.ui_skills == () and cfg.tool_treatments == ()
