"""El modelo del manifest de GraphAgents — un **superset** del YAML nativo de
AgentSpan + nuestras llaves de gobernanza.

Dos unidades de catálogo de primera clase:
- **tool** (`tools/<id>/`, ver `tool_model.py`) — bindeable por `tools: [{uses, with}]`.
- **agente** (`manifests/<id>.agent.yaml`) — referenciable por `uses: agent://<id>@<major>`,
  exponible como tool (`exposes_as_tool`) y publicable hacia afuera (`publish`).

Un manifest = el nodo raíz de un task graph. Es recursivo: un supervisor lista
`agents`, donde cada entrada es un agente inline o una REFERENCIA al catálogo.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sdk.tool_model import FieldSpec

Archetype = Literal["extractor", "analyzer", "reporter", "supervisor"]
Strategy = Literal[
    "handoff", "router", "parallel", "sequential", "swarm", "round_robin", "random", "manual"
]
CertLevel = Literal["none", "C0", "C1", "C2", "C3"]

_SLUG = re.compile(r"^[a-z][a-z0-9-]*$")

# Llaves NUESTRAS — se quitan antes de `agentspan deploy` (ver native_subset).
EXT_KEYS = {"archetype", "capability", "consumes", "certification", "exposes_as_tool", "publish", "uses", "inputs", "group", "contract"}


class AgentContract(BaseModel):
    """El contrato I/O DECLARADO de un agente de catálogo — espejo del de una tool
    (`tool.yaml` inputs/outputs, mismos `FieldSpec`). Es lo que hace a un agente
    conectable con validación: el wiring de un supervisor se chequea contra
    `inputs` (G-CONTRACT) y la compatibilidad agente→agente contra `outputs`."""

    inputs: dict[str, FieldSpec] = Field(default_factory=dict)
    outputs: dict[str, FieldSpec] = Field(default_factory=dict)


class ToolSpec(BaseModel):
    """Una tool de un agente: inline (`name`) o tomada del catálogo (`uses`)."""

    model_config = ConfigDict(populate_by_name=True)

    name: Optional[str] = None  # tool inline / nativa de agentspan
    uses: Optional[str] = None  # ref al catálogo: "id" o "id@major" (G-BIND)
    type: Literal["worker", "http", "mcp", "api"] = "worker"
    approval_required: bool = False
    binding: dict = Field(default_factory=dict, alias="with")  # state -> input

    @model_validator(mode="after")
    def _name_or_uses(self) -> "ToolSpec":
        if not self.name and not self.uses:
            raise ValueError("una tool debe declarar `name` (inline) o `uses` (catálogo)")
        return self

    @property
    def ref_id(self) -> Optional[str]:
        return self.uses.split("@", 1)[0] if self.uses else None


class PublishSpec(BaseModel):
    """Cómo se expone un agente hacia AFUERA (cross-system)."""

    model_config = ConfigDict(populate_by_name=True)

    as_: Literal["mcp", "http"] = Field(alias="as")
    name: Optional[str] = None  # nombre público (default: el del agente)


class AgentNode(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: Optional[str] = None
    uses: Optional[str] = None  # "agent://id@major" — REFERENCIA a un agente del catálogo
    archetype: Optional[Archetype] = None
    description: str = ""
    model: Optional[str] = None
    instructions: Optional[str] = None
    # --- ext (GraphAgents) ---
    capability: Optional[str] = None  # "modulo:funcion" -> StateGraph LangGraph
    consumes: list[str] = Field(default_factory=list)
    certification: CertLevel = "none"
    exposes_as_tool: bool = False  # el agente puede invocarse como tool de otro agente
    publish: Optional[PublishSpec] = None  # exponer hacia afuera (mcp/http)
    # ext (GraphAgents): el contrato I/O declarado (habilita conectar con validación).
    contract: Optional[AgentContract] = None
    # Estatus de catálogo para el explorer (badge). Ausente = producción (el pod real).
    # `demo` = ejemplo/proving-ground (greeter, ads-supervisor); `variant` = variante de
    # verificación (ads-extractors-parallel). Metadata pura: no afecta la ejecución.
    group: Optional[Literal["demo", "variant"]] = None
    # --- composición ---
    strategy: Optional[Strategy] = None
    agents: list["AgentNode"] = Field(default_factory=list)
    tools: list[ToolSpec] = Field(default_factory=list)
    guardrails: list[str] = Field(default_factory=list)
    # ext (GraphAgents): el binding del TASK GRAPH (state→input de este agente cuando
    # corre dentro de un supervisor que COMPONE). `{capability_input: $state.<key>}`.
    # El loader lo consume para threadear el estado; G-WIRE exige que exista en un
    # supervisor no-router. Es LA forma de orquestar en esta arquitectura.
    inputs: dict = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _slug(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not _SLUG.match(v):
            raise ValueError(f"name '{v}' debe ser kebab-case [a-z][a-z0-9-]*")
        return v

    @model_validator(mode="after")
    def _ref_or_inline(self) -> "AgentNode":
        if self.uses:
            # nodo de referencia: hereda nombre del ref si no se dio uno local.
            if self.name is None:
                self.name = self.ref_agent_id
        else:
            if self.name is None:
                raise ValueError("un agente necesita `name` (o `uses` para referenciar el catálogo)")
            if self.archetype is None:
                raise ValueError(f"agente '{self.name}' necesita `archetype` (salvo nodos de referencia)")
        return self

    @property
    def ref_agent_id(self) -> Optional[str]:
        if not self.uses:
            return None
        rest = self.uses.split("://", 1)[-1]
        return rest.split("@", 1)[0]

    @property
    def is_reference(self) -> bool:
        return bool(self.uses)

    @property
    def is_supervisor(self) -> bool:
        return self.archetype == "supervisor"


AgentNode.model_rebuild()


class TaskGraphManifest(AgentNode):
    """El nodo raíz de un task graph (= un archivo de `manifests/`)."""


def load_manifest(path: str | Path) -> TaskGraphManifest:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: el manifest debe ser un mapping YAML")
    return TaskGraphManifest.model_validate(data)


def iter_nodes(node: AgentNode) -> list[AgentNode]:
    """El nodo y todos sus descendientes (DFS)."""
    out = [node]
    for a in node.agents:
        out.extend(iter_nodes(a))
    return out


def native_subset(node: AgentNode) -> dict:
    """El YAML que entiende `agentspan deploy` — sin nuestras llaves ext."""
    out: dict = {}
    for k in ("name", "description", "model", "instructions", "strategy"):
        v = getattr(node, k, None)
        if v not in (None, ""):
            out[k] = v
    if node.tools:
        out["tools"] = [{"name": (t.name or t.ref_id), "type": t.type} for t in node.tools]
    if node.agents:
        out["agents"] = [native_subset(a) for a in node.agents]
    return out
