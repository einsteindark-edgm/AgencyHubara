"""Registro de bots (diseño v2 §08, fase F2): el ÚNICO control de qué corre en
cada conversación. Sin Temporal: lo leen el arranque del workflow, el ingest,
las tools, las activities y el laboratorio.

Un bot = una versión del workflow + un proveedor por capacidad + un perfil de
Jev (+ el modo de las capas ①②③ de V1):

* `reglas`: decide la regla de hoy (Jev no se consulta).
* `sombra`: decide la regla; Jev contesta al lado y cada desacuerdo va a la
  cola que califica Claude Code.
* `jev`: decide Jev con la regla de respaldo (si Jev no responde o duda,
  decide la regla; los pisos legales nunca se quitan).

Dónde se escoge:
* laboratorio: el brazo (`bot_for_arm`); el sandbox lo fija para el proceso
  del caso con `DECISIONS_BOT`;
* producción (`bot_for_session`): el despliegue gradual que ya existe (números
  de prueba y porcentaje estable por conversación del control «Bot nuevo»),
  ahora por capacidad (`_rollout/decisions.json`) y por versión del workflow,
  siempre dentro del techo de Terraform (`SALES_CAPABILITIES_CEILING`,
  `SALES_WORKFLOW_V2_CEILING`; los dos `off` por defecto).

Todo nace en `reglas` y en V1: sin tocar nada, el turno es el de hoy. Un
control ilegible cuenta como el de hoy: nunca frena el mensaje del cliente.
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.plugins.chats.agent.sales.decisions.rollout import MODES, RolloutState, effective_mode
from src.plugins.chats.agent.sales.decisions.rollout_store import read_state

WORKFLOW_V1 = "HubaraSalesSessionWorkflow"
WORKFLOW_V2 = "HubaraSalesSessionWorkflowV2"
PROVIDERS: tuple[str, ...] = ("reglas", "sombra", "jev")
#: El perfil del bot nuevo: el laboratorio (brazo B) y producción (sin
#: `SALES_PERCEPTION_PROFILE`) corren el MISMO. jev-v5 = motor completo (lectura
#: del hilo, contrato de herramientas y guía de etapas, F1–F6) con el
#: cuestionario rafaga-v5: el pedido de un tipo o colección de velas entra al
#: plan y la tarjeta de tarifas solo si preguntan el costo del envío
#: (revisiones 2026-09-29).
DEFAULT_PROFILE = "jev-v5"
#: Las capacidades que el control del dashboard conoce (cada una con su
#: interruptor). Una capacidad nueva se suma aquí.
CAPABILITIES: tuple[str, ...] = (
    # Lecturas del cliente (F3), el acuse tras la despedida (#379) y la
    # cortesía que no abre venta (2026-09-30).
    "compra", "retoma", "baja", "acuse", "cortesia", "cupon", "fuera_de_catalogo", "cantidad",
    # Mapeos a listas cerradas dentro de las tools (F3) y la revisión de los
    # datos de envío de `set_order_slot` (F6).
    "categoria", "familia_de_color", "item_del_pedido", "zona_de_envio", "datos", "producto_nombrado",
    # Texto del LLM en tools y activities (F5) y respaldo en sombra (F6). El
    # preámbulo del modelo se decide también en el egreso de V2.
    "persona", "enumeracion", "monto", "selector", "afirmacion", "preambulo",
    # Decisiones del agente (F8) y la promesa del relevo a un colega
    # (red de seguridad antes de enviar, 2026-09-30).
    "contactar", "cierre", "relevo",
    # Egreso del workflow V2 (F4/F5): solo actúan en conversaciones con V2.
    "destinatario", "rescate", "portavelas", "saludo",
)
#: V2 no tiene sombra (no se corren dos workflows): apagado, canary o encendido.
WORKFLOW_MODES: tuple[str, ...] = ("off", "canary", "on")

# Modo del despliegue (off/shadow/canary/on) → proveedor de la capacidad. En
# canary, `effective_mode` ya dejó `canary` solo para los números de prueba y
# el porcentaje estable; los demás quedan en `shadow`.
_PROVIDER_OF_MODE: dict[str, str] = {"off": "reglas", "shadow": "sombra", "canary": "jev", "on": "jev"}


@dataclass(frozen=True)
class Bot:
    id: str
    workflow: str = WORKFLOW_V1
    profile: str = DEFAULT_PROFILE
    # Capas ①②③ del turno en V1 (off/shadow/canary/on), como hoy.
    layers: str = "off"
    providers: Mapping[str, str] = field(default_factory=dict)
    default_provider: str = "reglas"

    def provider(self, capability: str) -> str:
        """`reglas`, `sombra` o `jev` para esta capacidad."""
        value = self.providers.get(capability, self.default_provider)
        return value if value in PROVIDERS else "reglas"


#: Brazos del laboratorio (diseño §08), por composición: A1 = V1 con reglas (el
#: bot de hoy); B0 = V2 con reglas y sin capas (tiene que dar lo mismo que A1:
#: prueba que el esqueleto nuevo es fiel); B = V2 con Jev en cada capacidad y
#: las capas ①③. Así se separa el efecto del workflow del efecto de Jev.
LAB_BOTS: dict[str, Bot] = {
    "A1": Bot(id="A1"),
    "B0": Bot(id="B0", workflow=WORKFLOW_V2),
    "B": Bot(id="B", workflow=WORKFLOW_V2, layers="on", default_provider="jev"),
}


def bot_for_arm(arm: str) -> Bot:
    try:
        return LAB_BOTS[arm]
    except KeyError:
        raise ValueError(f"brazo desconocido: {arm!r} (hay {', '.join(LAB_BOTS)})") from None


def _decisions_path(vault_dir: Path) -> Path:
    return Path(vault_dir) / "_rollout" / "decisions.json"


def read_decisions_state(vault_dir: Path) -> dict[str, Any]:
    """`{"capabilities": {capacidad: modo}, "workflow_v2": modo}`; ilegible = vacío."""
    try:
        raw = json.loads(_decisions_path(vault_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write(vault_dir: Path, data: dict[str, Any]) -> None:
    path = _decisions_path(vault_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def write_capability_modes(vault_dir: Path, modes: Mapping[str, str]) -> None:
    """Guarda el modo de cada capacidad (lo escribe el control del dashboard)."""
    data = read_decisions_state(vault_dir)
    capabilities = dict(data.get("capabilities") or {}) if isinstance(data.get("capabilities"), dict) else {}
    capabilities.update({str(k): str(v) for k, v in modes.items()})
    _write(vault_dir, {**data, "capabilities": capabilities})


def write_workflow_mode(vault_dir: Path, mode: str) -> None:
    """Guarda el modo del workflow V2 (off/canary/on)."""
    _write(vault_dir, {**read_decisions_state(vault_dir), "workflow_v2": str(mode)})


def _ceiling(name: str) -> str:
    value = (os.getenv(name) or "off").strip().lower()
    return value if value in MODES else "off"


def capabilities_ceiling() -> str:
    """Techo de Terraform de las capacidades (`SALES_CAPABILITIES_CEILING`)."""
    return _ceiling("SALES_CAPABILITIES_CEILING")


def workflow_ceiling() -> str:
    """Techo de Terraform del workflow V2 (`SALES_WORKFLOW_V2_CEILING`)."""
    return _ceiling("SALES_WORKFLOW_V2_CEILING")


def capability_modes(vault_dir: Path) -> dict[str, str]:
    """El modo guardado de cada capacidad conocida (sin guardar = `off`)."""
    data = read_decisions_state(Path(vault_dir))
    stored = data.get("capabilities") if isinstance(data.get("capabilities"), dict) else {}
    return {cap: stored.get(cap) if stored.get(cap) in MODES else "off" for cap in CAPABILITIES}


def workflow_mode(vault_dir: Path) -> str:
    """El modo guardado del workflow V2 (sin guardar o raro = `off`)."""
    mode = read_decisions_state(Path(vault_dir)).get("workflow_v2")
    return mode if mode in WORKFLOW_MODES else "off"


def _effective(mode: Any, base: RolloutState, *, ceiling: str, session_id: str) -> str:
    if not isinstance(mode, str) or mode not in MODES:
        return "off"
    state = RolloutState(mode=mode, canary_percent=base.canary_percent, test_numbers=base.test_numbers)
    return effective_mode(state, ceiling=ceiling, session_id=session_id)


def bot_for_session(session_id: str, *, vault_dir: Path | None) -> Bot:
    """El bot de ESTA conversación (ver el docstring del módulo). Sin vault
    (una tool armada sin él) no hay control del despliegue: el bot fijado del
    laboratorio o el de hoy."""
    pinned = (os.getenv("DECISIONS_BOT") or "").strip()
    if pinned:
        return bot_for_arm(pinned)
    if vault_dir is None:
        return Bot(id="hoy")
    try:
        base = read_state(Path(vault_dir))
    except Exception:  # noqa: BLE001 — el control nunca frena el mensaje
        base = RolloutState()
    data = read_decisions_state(Path(vault_dir))
    modes = data.get("capabilities") if isinstance(data.get("capabilities"), dict) else {}
    cap_ceiling = _ceiling("SALES_CAPABILITIES_CEILING")
    providers = {
        str(cap): _PROVIDER_OF_MODE[_effective(mode, base, ceiling=cap_ceiling, session_id=session_id)]
        for cap, mode in modes.items()
    }
    workflow_mode = _effective(
        data.get("workflow_v2"), base, ceiling=_ceiling("SALES_WORKFLOW_V2_CEILING"), session_id=session_id
    )
    return Bot(
        id="produccion",
        workflow=WORKFLOW_V2 if workflow_mode in ("canary", "on") else WORKFLOW_V1,
        profile=(os.getenv("SALES_PERCEPTION_PROFILE") or "").strip() or DEFAULT_PROFILE,
        layers=_effective(base.mode, base, ceiling=_ceiling("SALES_PERCEPTION_MODE_CEILING"), session_id=session_id),
        providers=providers,
    )
