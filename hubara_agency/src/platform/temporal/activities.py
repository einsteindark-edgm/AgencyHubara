"""
Actividad Customizada: execute_tool

Esta actividad ES OBLIGATORIA para reemplazar la nativa de exoclaw_temporal.
La actividad nativa tiene hardcodeados sus registros puros. Esta versión híbrida invoca nuesto propio
src.core.registries, lo que permite que el Agente use Herramientas Personalizadas creadas por la Agencia.
"""

from __future__ import annotations
import json
import time
from pathlib import Path
from temporalio import activity

from exoclaw.agent.tools.protocol import ToolContext
from exoclaw_temporal.config import ExecuteToolInput

from src.platform.constants import ROUTE_HUMANO, ROUTE_VENTAS
from src.platform.contracts import RemarketingEligibility
from src.platform.registries import get_base_tools_registry
from src.platform.tool_extensions import apply_tool_extensions
from src.platform.temporal.heartbeat import with_heartbeat
from src.platform.config import WORKSPACE_VAULT_DIR

# Tags terminales — no se reactiva remarketing si la conversación está cerrada.
# COMPRA_EXITOSA: la venta ya cerró. HUMANO: humano tomó control (sinónimo de
# active_route=humano, pero algunas tools setean el tag y no el route, así que
# chequeamos ambos por defense-in-depth).
_TERMINAL_TAGS_FOR_REMARKETING: frozenset[str] = frozenset({
    "COMPRA_EXITOSA",
    "HUMANO",
})

@activity.defn(name="execute_tool")
@with_heartbeat(every=10)
async def execute_tool(input: ExecuteToolInput) -> str:
    """Implementación agnóstica de ejecución de herramientas con control de pulsos (Heartbeat) de Temporal."""

    workspace_path = Path(input.workspace.path)
    registry = get_base_tools_registry(workspace_path)
    # NEW-5 cerrado: el registry de tools por dominio se inyecta via
    # `register_tool_extension(...)` en el `worker.py` de cada dominio.
    # `execute_tool` ya NO importa `src.domains.*` por path concreto.
    apply_tool_extensions(registry, workspace_path)

    ctx = ToolContext(
        session_key=input.session_id,
        channel=input.channel,
        chat_id=input.chat_id,
    )

    # PR-C: la tool ahora implementa `execute_with_context(ctx, **params)`, asi
    # que `ToolRegistry.execute` (exoclaw/agent/tools/registry.py:102-105) inyecta
    # `ctx` por su cuenta. Ya no contaminamos `input.params` con un objeto no
    # JSON-serializable.
    if input.params is None:
        input.params = {}
    return await registry.execute(input.name, input.params, ctx)

@activity.defn(name="claim_conversation_routing")
async def claim_conversation_routing(session_id: str, new_route: str) -> None:
    """Marca el `active_route` de la sesion en el vault PER-SESION.

    POST-MORTEM workflow remarketing-wa_573125671604: la firma anterior
    `claim_conversation_routing(workspace_path, new_route)` recibia el RUNTIME
    WORKSPACE CANONICO del agente (e.g. `/app/.../sales_whatsapp/workspace/`)
    y escribia `metadata.json` ahi. Pero `LoadOrStartSalesSession` (el use
    case que el webhook ejecuta para rutear el siguiente mensaje) lee de
    `WORKSPACE_VAULT_DIR / session_id / metadata.json`. Resultado: el
    `active_route` se escribia en un archivo distinto al que el webhook
    leia, asi que el routing siempre defaultaba a `ventas` y los workflows
    de Remarketing quedaban zombies (nunca recibian signal del cliente).

    Fix: la activity ahora recibe `session_id` y deriva el path del vault
    via `WORKSPACE_VAULT_DIR + session_id + metadata.json`. Los callers
    (RemarketingSessionWorkflow) deben pasar `session_id`, no `ws_path`.
    """
    metadata_file = WORKSPACE_VAULT_DIR / session_id / "metadata.json"
    metadata_file.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if metadata_file.exists():
        data = json.loads(metadata_file.read_text(encoding="utf-8"))

    data["active_route"] = new_route

    if "status_history" not in data:
        data["status_history"] = []

    data["status_history"].append({
        "tag": data.get("tag", "NO_ETIQUETADO"),
        "motivo": data.get("motivo", f"Transferencia de ruta a {new_route}"),
        "active_route": new_route,
        "timestamp": time.time()
    })

    metadata_file.write_text(json.dumps(data, indent=2), encoding="utf-8")


@activity.defn(name="check_remarketing_eligibility")
async def check_remarketing_eligibility(session_id: str) -> RemarketingEligibility:
    """Verifica si el remarketing puede arrancar según el estado actual del cliente.

    POST-MORTEM workflow remarketing-wa_573125671604 run e688685d-c676-4e61-a152-b22ff49788db
    (2026-05-20): un remarketing programado con `start_delay=60s` arrancó
    DESPUÉS de que el sales workflow escaló el caso a humano. El remarketing
    workflow no chequeaba el estado y ciegamente:
      1. Llamó `claim_conversation_routing(session_id, ROUTE_REMARKETING)` →
         pisó el `active_route=humano` que el escalation había setteado.
      2. Mandó mensaje al cliente reactivando la venta — violando la regla
         de negocio: cuando hay humano en el caso, ningún bot interviene.

    Esta activity es la primera que invoca `RemarketingWorkflow.run`. Lee el
    metadata.json actual y devuelve `eligible=False` si:
      - `active_route == ROUTE_HUMANO` — humano gestionando el caso.
      - `tag` en `_TERMINAL_TAGS_FOR_REMARKETING` (COMPRA_EXITOSA, HUMANO).

    El workflow respeta la decisión y returna early sin tocar nada. El
    humano (vía dashboard) puede devolver el control cambiando active_route
    de vuelta a ventas/remarketing si quiere.

    Devuelve `RemarketingEligibility` (dataclass plano — sin nested
    dataclasses para evitar footgun F5).
    """
    metadata_file = WORKSPACE_VAULT_DIR / session_id / "metadata.json"
    if not metadata_file.exists():
        # Sesión nueva sin metadata previa — eligible por default.
        return RemarketingEligibility(
            eligible=True,
            current_route=ROUTE_VENTAS,
            current_tag="NO_ETIQUETADO",
        )

    try:
        data = json.loads(metadata_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # Si el metadata está corrupto, fail-safe = NO arrancar remarketing.
        # Mejor un remarketing perdido que pisar un caso humano por accidente.
        activity.logger.error(
            "check_remarketing_eligibility: metadata corrupto para session_id=%s — "
            "fail-safe: bloqueando remarketing. Error: %s",
            session_id, exc,
        )
        return RemarketingEligibility(
            eligible=False,
            current_route="unknown",
            current_tag="unknown",
            blocked_reason=f"metadata.json corrupto o ilegible: {exc}",
        )

    current_route = str(data.get("active_route", ROUTE_VENTAS))
    current_tag = str(data.get("tag", "NO_ETIQUETADO"))

    if current_route == ROUTE_HUMANO:
        return RemarketingEligibility(
            eligible=False,
            current_route=current_route,
            current_tag=current_tag,
            blocked_reason=(
                f"active_route={ROUTE_HUMANO} — humano gestiona el caso. "
                "El remarketing no debe intervenir hasta que el humano devuelva "
                "el control via dashboard."
            ),
        )

    if current_tag in _TERMINAL_TAGS_FOR_REMARKETING:
        return RemarketingEligibility(
            eligible=False,
            current_route=current_route,
            current_tag=current_tag,
            blocked_reason=(
                f"tag={current_tag} (terminal) — la conversación ya está cerrada. "
                f"Tags terminales: {sorted(_TERMINAL_TAGS_FOR_REMARKETING)}."
            ),
        )

    return RemarketingEligibility(
        eligible=True,
        current_route=current_route,
        current_tag=current_tag,
    )


@activity.defn(name="read_workspace_memory_activity")
async def read_workspace_memory_activity(session_id: str) -> str:
    """Lee de forma determinista y segura el memory.md del PVC para inyectarlo como contexto extra en un Workflow."""
    memory_file = WORKSPACE_VAULT_DIR / session_id / "memory.md"
    try:
        if memory_file.exists():
            content = memory_file.read_text(encoding="utf-8").strip()
            if content:
                return f"\n\nRESUMEN MEMORIA DE LA VENTA PASADA (MUY IMPORTANTE LEER):\n{content}\n"
    except OSError:
        pass
    return ""
