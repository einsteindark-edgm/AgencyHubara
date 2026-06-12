"""PluginManifest tipado (pydantic) — la mitad ejecutable del schema (F-SDK-1).

``plugin.schema.yaml`` describe el manifest; este módulo lo VALIDA. Es el
chequeo C0 de la certificación ("Declarado"): un manifest que no parsea acá
no entra al catálogo ni pasa `hubara check`.

Fidelidad al schema (contrato bidireccional, enforced por
``tests/architecture/test_manifest_model.py``):

- strict donde el schema dice ``additionalProperties: false`` (top-level,
  frontend, api, agent, transitions, action, deployment, compose, dashboard,
  permissions) → ``extra="forbid"``;
- tolerante donde el schema no lo dice (worker items, sidebar/section items,
  consumes items, wiring_intents) → ``extra="allow"``;
- los enums viven UNA vez acá (``Archetype``, ``Via``) y un contract test
  exige que el schema YAML los liste idénticos — el drift modelo↔schema
  truena en CI, no en producción (clase de bug L-10).

Uso::

    from src.sdk import parse_manifest, load_typed_manifest

    typed = load_typed_manifest("eta")     # ManifestValidationError si C0 falla
    typed.archetype                        # "notifier"
    typed.agent.workers[0].task_queue      # "queue-eta-agent"
"""
from __future__ import annotations

from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# ---------------------------------------------------------------------------
# Enums compartidos (fuente única — el schema YAML debe espejarlos)
# ---------------------------------------------------------------------------

Archetype = Literal["api_only", "full_stack", "agentic", "notifier", "sync"]
ARCHETYPES: tuple[str, ...] = get_args(Archetype)

Via = Literal[
    "start_workflow",
    "start_workflow_with_replace",
    "ensure_running",
    "signal",
    "signal_with_start",
]
VIAS: tuple[str, ...] = get_args(Via)

_ID_PATTERN = r"^[a-z][a-z0-9_]*$"
_VERSION_PATTERN = r"^[0-9]+\.[0-9]+\.[0-9]+(-[a-z0-9.]+)?$"
_QUEUE_PATTERN = r"^[a-z][a-z0-9-]*$"
_EVENT_PATTERN = r"^[A-Z][A-Za-z0-9]*Event$"
_WORKFLOW_PATTERN = r"^[A-Z][A-Za-z0-9]*$"


class ManifestValidationError(ValueError):
    """El manifest no cumple C0 (schema/forma). Mensaje con paths accionables."""


# ---------------------------------------------------------------------------
# Bloques
# ---------------------------------------------------------------------------


class SidebarEntry(BaseModel):
    model_config = ConfigDict(extra="allow")
    route: str
    label: str
    icon: str | None = None
    badge_query: str | None = None


class SectionEntry(BaseModel):
    model_config = ConfigDict(extra="allow")
    key: str
    label: str
    order: int | None = None
    icon: str | None = None


class DashboardWidget(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    position: str


class FrontendContributes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sidebar: list[SidebarEntry] = Field(default_factory=list)
    sections: list[SectionEntry] = Field(default_factory=list)
    dashboard_widgets: list[DashboardWidget] = Field(default_factory=list)


class FrontendBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entry: str = "./frontend"
    contributes: FrontendContributes | None = None


class LegacyRouter(BaseModel):
    model_config = ConfigDict(extra="allow")
    module: str
    prefix: str | None = None
    tags: list[str] = Field(default_factory=list)


class ApiBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    python_module: str | None = None
    prefix: str | None = None
    tags: list[str] = Field(default_factory=list)
    legacy_routers: list[LegacyRouter] = Field(default_factory=list)
    migrations: str | None = None


class ConsumeEntry(BaseModel):
    model_config = ConfigDict(extra="allow")
    provider: str
    contract: str
    into: str
    cast: str


class TransitionActionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    via: Via
    target_workflow: str = Field(pattern=_WORKFLOW_PATTERN)
    target_plugin: str | None = None
    target_worker: str | None = None
    signal_name: str | None = None
    workflow_id_template: str | None = None
    input_mapping: dict[str, str] = Field(default_factory=dict)
    start_delay_field: str | None = None


class TransitionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=_ID_PATTERN)
    on_event: str = Field(pattern=_EVENT_PATTERN)
    action: TransitionActionModel
    when: dict[str, str | int | bool | None] = Field(default_factory=dict)


class LegacyInvoke(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worker: str
    plugin: str | None = None
    via: str | None = None
    when: str | None = None


class EnvSecret(BaseModel):
    model_config = ConfigDict(extra="allow")
    var: str
    secret: str | None = None
    key: str | None = None


class DeploymentBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    replicas: int | None = Field(default=None, ge=1)
    cpu_request: str | None = None
    cpu_limit: str | None = None
    memory_request: str | None = None
    memory_limit: str | None = None
    strategy: Literal["RollingUpdate", "Recreate"] | None = None
    env_secrets: list[EnvSecret] = Field(default_factory=list)


class ComposeBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    env: dict[str, str] = Field(default_factory=dict)
    volumes: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


class CapabilityEntry(BaseModel):
    model_config = ConfigDict(extra="allow")
    label: str
    icon: str | None = None


class WorkerDashboard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = None
    role: str | None = None
    category: str | None = None
    icon: str | None = None
    color: str | None = None
    model: str | None = None
    workspace: str | None = None
    capabilities: list[CapabilityEntry] = Field(default_factory=list)


class WorkerSpec(BaseModel):
    # Worker items NO llevan additionalProperties:false en el schema —
    # espejamos la tolerancia (campos nuevos aparecen acá primero; la regla
    # de oro luego los tipa + chequea, como pasó con `dashboard`).
    model_config = ConfigDict(extra="allow")
    name: str
    module: str
    task_queue: str = Field(pattern=_QUEUE_PATTERN)
    owns_route: str | None = None
    route_workflow_id_template: str | None = None
    dashboard: WorkerDashboard | None = None
    workflow_classes: list[str] = Field(default_factory=list)
    emits: list[str] = Field(default_factory=list)
    transitions: list[TransitionModel] = Field(default_factory=list)
    invokes: list[LegacyInvoke] = Field(default_factory=list)
    deployment: DeploymentBlock | None = None
    compose: ComposeBlock | None = None


class AgentBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    python_module: str | None = None
    worker_module: str | None = None
    workers: list[WorkerSpec] = Field(default_factory=list)
    task_queue: str | None = None
    mode: Literal["session_based", "turn_based"] | None = None
    graph_spec: str | None = None


class JobEntry(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    schedule: str
    handler: str


class WiringIntents(BaseModel):
    # additionalProperties: true en el schema — deliberadamente abierto.
    model_config = ConfigDict(extra="allow")
    db_tables: list[str] = Field(default_factory=list)
    s3_buckets: list[str] = Field(default_factory=list)
    filesystem_volumes: list[str] = Field(default_factory=list)
    env_vars_required: list[str] = Field(default_factory=list)


class PermissionsBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reads: list[str] = Field(default_factory=list)
    writes: list[str] = Field(default_factory=list)


class PluginManifest(BaseModel):
    """El manifest completo, tipado. ``extra="forbid"`` = top-level estricto."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=_ID_PATTERN)
    version: str = Field(pattern=_VERSION_PATTERN)
    display_name: str | None = None
    description: str | None = None
    archetype: Archetype | None = None  # P-29 exige presencia; None = legacy sin clasificar
    agentic: bool = False
    depends_on: list[str] = Field(default_factory=list)
    consumes: list[ConsumeEntry] = Field(default_factory=list)
    frontend: FrontendBlock | None = None
    api: ApiBlock | None = None
    agent: AgentBlock | None = None
    jobs: list[JobEntry] = Field(default_factory=list)
    wiring_intents: WiringIntents | None = None
    permissions: PermissionsBlock | None = None


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------


def parse_manifest(data: dict[str, Any], *, source: str = "<dict>") -> PluginManifest:
    """Valida y tipa un manifest dict (C0). Errores con paths accionables."""
    try:
        return PluginManifest.model_validate(data)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise ManifestValidationError(
            f"manifest inválido ({source}): {details}. "
            f"Schema: frontend_dashboard/src/plugins/_schema/plugin.schema.yaml; "
            f"modelo: src/sdk/manifest_model.py (C0)."
        ) from exc


def load_typed_manifest(plugin_id: str) -> PluginManifest:
    """``load_manifest`` + validación C0, en una llamada."""
    from src.platform.plugin_manifest import load_manifest

    return parse_manifest(load_manifest(plugin_id), source=f"plugin {plugin_id!r}")


def all_typed_manifests() -> list[PluginManifest]:
    """Todos los manifests del repo, tipados. Falla en el PRIMER inválido."""
    from src.platform.plugin_manifest import all_manifests

    return [parse_manifest(m, source=f"plugin {pid!r}") for pid, m in all_manifests()]
