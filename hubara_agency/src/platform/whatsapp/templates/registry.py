"""Template registry — catalogo de templates aprobados por Meta para esta WABA.

Cada `TemplateSpec` declara contrato (variables esperadas, category, stage
donde aplica) que el caller usa para:
  1. Validar que los inputs (variables del LLM o del watchdog eligibility)
     matchean el template antes de mandar el payload a Meta.
  2. Resolver qué template usar dado un stage del episodio (watchdog).
  3. Distinguir utility vs marketing al momento de pricing y observability.

Carga del catalog desde YAML — singleton por proceso via `composition.py`
(R-STATELESS). El catalog es inmutable post-carga.

NOTA: `waba_template_name` debe coincidir EXACTAMENTE con el nombre
aprobado en Meta Business Manager. Si Meta re-categoriza un utility a
marketing automáticamente, el `pricing` del webhook gana (fuente de
verdad post-facto). El `category` declarado aquí es la intención al
momento del submit.

Ver HU-WA24H-001 §4.5 para el contrato completo.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


# =============================================================================
# DTOs (R-JSON, frozen)
# =============================================================================


@dataclass(frozen=True)
class TemplateVariable:
    """Una variable posicional del template (Meta usa {{1}}, {{2}}, ...).

    El orden en `TemplateSpec.variables` define el orden posicional Meta:
    primer var en la tupla = {{1}}, segundo = {{2}}, etc. El `name` es el
    label semantico que los callers usan para pasar variables como dict.
    """

    name: str
    type: str  # "string" por ahora — Meta también soporta currency/datetime
    max_length: int | None
    description: str | None


@dataclass(frozen=True)
class TemplateSpec:
    """Spec inmutable de un template aprobado.

    Identidad:
      * `name`        — id interno (puede incluir version: e.g. `..._v1`).
      * `waba_template_name` — nombre exacto en Meta Business Manager.
    """

    name: str
    category: str  # "utility" | "marketing" | "authentication"
    language: str  # "es_CO" | "es_AR" | ...
    waba_template_name: str
    semantics: str
    triggers_when_window_expiring: bool
    requires_episode_stage: str | None
    variables: tuple[TemplateVariable, ...]
    #: Copy aprobado en Meta con slots `{{1}}`, `{{2}}`… (espejo de
    #: `infra/whatsapp-provisioning/definitions/templates.json`). Lo usa el
    #: dashboard para previsualizar y para pintar en el chat lo que se envió.
    body: str | None = None
    #: Encabezado multimedia aprobado en Meta (`"image"`) o None (sin
    #: encabezado). La foto NO es fija: se elige en cada envío (media_id), así
    #: la plantilla de pedido listo lleva la foto real del pedido.
    header_format: str | None = None


# =============================================================================
# Registry
# =============================================================================


#: Path al catalog YAML (relativo a este módulo).
CATALOG_PATH: Path = Path(__file__).parent / "catalog.yaml"


def load_template_registry_from_yaml(
    path: Path | None = None,
) -> dict[str, TemplateSpec]:
    """Carga el catalog YAML → dict {name → TemplateSpec}.

    Defensivo: valida shape, tipos, duplicate names, category enum,
    triggers_when_window_expiring=false para marketing (regla dura).
    """
    catalog_path = path or CATALOG_PATH
    if not catalog_path.exists():
        raise FileNotFoundError(f"Template catalog YAML not found: {catalog_path}")

    raw: dict[str, Any] = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    return _build_registry_from_dict(raw)


def _build_registry_from_dict(raw: dict[str, Any]) -> dict[str, TemplateSpec]:
    if "templates" not in raw or not isinstance(raw["templates"], list):
        raise ValueError("Template catalog must have a top-level `templates` list")

    registry: dict[str, TemplateSpec] = {}
    for entry in raw["templates"]:
        spec = _build_template_spec_from_dict(entry)
        if spec.name in registry:
            raise ValueError(
                f"Duplicate template name in catalog: {spec.name}. "
                "Use _vN suffix to version locally."
            )
        registry[spec.name] = spec
    return registry


_VALID_CATEGORIES: frozenset[str] = frozenset(
    {"utility", "marketing", "authentication"}
)

#: Encabezados multimedia soportados por el builder. Solo imagen por ahora.
_VALID_HEADER_FORMATS: frozenset[str] = frozenset({"image"})


def _build_template_spec_from_dict(entry: dict[str, Any]) -> TemplateSpec:
    required = (
        "name",
        "category",
        "language",
        "waba_template_name",
        "semantics",
        "triggers_when_window_expiring",
        "variables",
    )
    missing = [k for k in required if k not in entry]
    if missing:
        raise ValueError(
            f"Template entry missing required fields {missing}: {entry.get('name', '<unnamed>')}"
        )

    if entry["category"] not in _VALID_CATEGORIES:
        raise ValueError(
            f"Invalid category {entry['category']!r} for template {entry['name']!r}. "
            f"Valid: {sorted(_VALID_CATEGORIES)}"
        )

    # Regla dura: marketing NUNCA puede ser disparado por el watchdog.
    # Es decisión consciente del LLM o cadencia (cuesta dinero).
    if (
        entry["category"] == "marketing"
        and entry["triggers_when_window_expiring"] is True
    ):
        raise ValueError(
            f"Template {entry['name']!r}: marketing templates MUST have "
            "triggers_when_window_expiring=false. Marketing decisions are "
            "for the LLM or the cadence workflow, never the watchdog."
        )

    header_format = entry.get("header_format")
    if header_format is not None and header_format not in _VALID_HEADER_FORMATS:
        raise ValueError(
            f"Invalid header_format {header_format!r} for template {entry['name']!r}. "
            f"Valid: {sorted(_VALID_HEADER_FORMATS)}"
        )

    variables_raw = entry["variables"]
    if not isinstance(variables_raw, list):
        raise ValueError(
            f"Template {entry['name']!r}: `variables` must be a list, "
            f"got {type(variables_raw).__name__}"
        )

    variables: list[TemplateVariable] = []
    for var_raw in variables_raw:
        if "name" not in var_raw or "type" not in var_raw:
            raise ValueError(
                f"Template {entry['name']!r}: variable missing name/type: {var_raw}"
            )
        variables.append(
            TemplateVariable(
                name=var_raw["name"],
                type=var_raw["type"],
                max_length=var_raw.get("max_length"),
                description=var_raw.get("description"),
            )
        )

    return TemplateSpec(
        name=entry["name"],
        category=entry["category"],
        language=entry["language"],
        waba_template_name=entry["waba_template_name"],
        semantics=entry["semantics"],
        triggers_when_window_expiring=entry["triggers_when_window_expiring"],
        requires_episode_stage=entry.get("requires_episode_stage"),
        variables=tuple(variables),
        body=entry.get("body"),
        header_format=header_format,
    )


# =============================================================================
# Validation helpers (puros)
# =============================================================================


def validate_variables(
    spec: TemplateSpec, variables: dict[str, str]
) -> list[str]:
    """Valida que `variables` matchee el spec. Retorna lista de errores
    (vacía si todo OK).

    Reglas:
      * Toda variable declarada en spec.variables debe estar en `variables`.
      * No puede haber variables extra en `variables` que no estén en spec.
      * Cada valor debe ser str (Meta exige string en el payload).
      * Ningún valor puede ser vacío o solo-espacios: Meta rechaza params ""
        con 131008 "Parameter of type text is missing text value" (incidente
        2026-07-21, order #22) — mejor fallar acá, local y con nombre.
      * Si la variable tiene `max_length`, el valor no puede excederlo.
    """
    errors: list[str] = []
    declared_names = {v.name for v in spec.variables}
    provided_names = set(variables.keys())

    missing = declared_names - provided_names
    extra = provided_names - declared_names
    if missing:
        errors.append(f"Missing variables: {sorted(missing)}")
    if extra:
        errors.append(f"Unexpected variables: {sorted(extra)}")

    for var_spec in spec.variables:
        if var_spec.name not in variables:
            continue  # ya cubierto por missing
        value = variables[var_spec.name]
        if not isinstance(value, str):
            errors.append(
                f"Variable {var_spec.name!r}: must be str, got {type(value).__name__}"
            )
            continue
        if not value.strip():
            errors.append(
                f"Variable {var_spec.name!r}: must not be empty (Meta rejects "
                "empty template params with error 131008)"
            )
            continue
        if var_spec.max_length is not None and len(value) > var_spec.max_length:
            errors.append(
                f"Variable {var_spec.name!r}: length {len(value)} exceeds "
                f"max {var_spec.max_length}"
            )

    return errors


#: Meta rechaza un param de texto con salto de línea, tab o >4 espacios seguidos.
_META_FORBIDDEN_PARAM_TEXT = re.compile(r"[\n\r\t]| {5,}")


def meta_text_param_errors(variables: dict[str, str]) -> list[str]:
    """Errores por variables que Meta rechazaría como param de texto del body
    (saltos de línea, tabs o más de 4 espacios consecutivos). Lista vacía = OK.

    Separado de `validate_variables` a propósito: lo consume el envío del
    operador humano (dashboard), donde el texto lo escribe una persona.
    """
    return [
        f"Variable {name!r}: no puede tener saltos de línea, tabs ni más de "
        "4 espacios seguidos (Meta rechaza el parámetro)"
        for name, value in variables.items()
        if isinstance(value, str) and _META_FORBIDDEN_PARAM_TEXT.search(value)
    ]


def render_template_body(spec: TemplateSpec, variables: dict[str, str]) -> str:
    """Texto que recibe el cliente: el `body` con cada `{{N}}` reemplazado por
    la variable N-ésima de `spec.variables` (orden posicional de Meta).

    Sin `body` declarado devuelve el marker legible `[Template: name] k=v`.
    Un slot sin variable queda tal cual (`{{N}}`) — el caller valida antes con
    `validate_variables`; acá solo se pinta.
    """
    if not spec.body:
        summary = ", ".join(f"{k}={v}" for k, v in variables.items())
        return f"[Template: {spec.name}] {summary}".strip()
    rendered = spec.body
    for index, var in enumerate(spec.variables, start=1):
        if var.name in variables:
            rendered = rendered.replace("{{%d}}" % index, variables[var.name])
    return rendered


def get_templates_for_stage(
    registry: dict[str, TemplateSpec], stage: str
) -> list[TemplateSpec]:
    """Filtra templates aplicables a un stage de episodio.

    Reglas:
      * `requires_episode_stage == stage` → match.
      * `requires_episode_stage == "any"` → match siempre.
      * `requires_episode_stage is None` → NO match (template sin stage
         explícito es para uso solo via LLM consciente, no auto-resolution).
    """
    matches: list[TemplateSpec] = []
    for spec in registry.values():
        if spec.requires_episode_stage == stage:
            matches.append(spec)
        elif spec.requires_episode_stage == "any":
            matches.append(spec)
    return matches


def get_watchdog_template_for_stage(
    registry: dict[str, TemplateSpec], stage: str
) -> TemplateSpec | None:
    """Resuelve qué template usar para el watchdog dado un stage.

    Filtra por `triggers_when_window_expiring=True` AND match de stage.
    Retorna el primer match (orden del YAML). None si no hay match —
    el caller decide skip vs fallback.
    """
    candidates = [
        spec
        for spec in get_templates_for_stage(registry, stage)
        if spec.triggers_when_window_expiring
    ]
    return candidates[0] if candidates else None
