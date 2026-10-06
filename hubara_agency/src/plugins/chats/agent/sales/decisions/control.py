"""El control del bot nuevo, POR COMANDO (decisión del operador, 2026-10-06).

«Que los botones de la UI no sirvan y todo se haga por comandos, para evitar
que alguien jugando dañe producción.» El panel de Agents solo muestra el
estado y los PUT del contrato `perception-rollout@v1` responden 403 con el
comando. Este módulo guarda las mismas garantías que tenían:

* el modo se mueve DENTRO del techo de Terraform (`SALES_*_CEILING`);
* apagar y bajar siempre pasan (interruptor de emergencia): escriben sin
  recorrer el vault ni revalidar lo guardado;
* subir sin la vara se rechaza (`ControlError` 422) con los chequeos que
  fallan, y una subida lenta no pisa un apagado que llegó mientras tanto (409);
* cada cambio queda firmado (`comando:<quien>`) y en el log.

Suma «los números de prueba deciden con Jev» (`test_numbers_jev`): solo esos
números, dentro de los techos y sin la vara, porque nadie más cambia (ver
`bots.bot_for_session`). La vara sigue exigida para todo lo que llega a los
clientes (canary con porcentaje y encendido).

Se corre DENTRO del contenedor de la API (tiene el vault y los techos de
Terraform en su entorno); desde tu máquina, `infra/scripts/bot_control.sh`
lo corre por SSM:

    python -m src.plugins.chats.agent.sales.decisions.control estado
    python -m src.plugins.chats.agent.sales.decisions.control --por ana numeros agregar wa_57…
    python -m src.plugins.chats.agent.sales.decisions.control --por ana workflow canary
    python -m src.plugins.chats.agent.sales.decisions.control --por ana prueba-jev si
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

import structlog

from src.plugins.chats.agent.sales.decisions import bots
from src.plugins.chats.agent.sales.decisions.capability_rollout import (
    CapabilityFacts,
    can_set_capability,
    can_set_workflow,
    capability_facts,
    workflow_readiness,
)
from src.plugins.chats.agent.sales.decisions.capability_rollout import readiness as capability_readiness
from src.plugins.chats.agent.sales.decisions.probe import read_latest as read_latest_probe
from src.plugins.chats.agent.sales.decisions.rollout import (
    MODES,
    Check,
    RolloutFacts,
    RolloutState,
    can_set_mode,
    readiness,
)
from src.plugins.chats.agent.sales.decisions.rollout_store import (
    ShadowMetrics,
    read_state,
    shadow_metrics,
    write_state,
)

logger = structlog.get_logger()

SID_RE = re.compile(r"wa_\d{8,15}")
TARGETS = ("shadow", "canary", "on")
WORKFLOW_TARGETS = ("canary", "on")
_MODES_MSG = "Modos: off, shadow, canary u on."
_CHANGED = {"reason": "changed", "message": "El estado cambió mientras se revisaba; vuelve a intentarlo."}


class ControlError(Exception):
    """Un cambio rechazado. `status` como el HTTP de antes (422: no se puede;
    409: el estado cambió mientras se revisaba) y `detail` con el motivo, el
    mensaje y, si aplica, los chequeos que fallan."""

    def __init__(self, status: int, detail: dict[str, Any]) -> None:
        super().__init__(str(detail.get("message") or detail.get("reason") or ""))
        self.status = status
        self.detail = detail


def _now_ms() -> int:
    return int(time.time() * 1000)


def _rank(mode: str) -> int:
    return MODES.index(mode) if mode in MODES else 0


def _ceiling() -> str:
    value = (os.getenv("SALES_PERCEPTION_MODE_CEILING") or "off").strip().lower()
    return value if value in MODES else "off"


def _profile() -> str:
    return (os.getenv("SALES_PERCEPTION_PROFILE") or "jev-v1").strip()


def api_key_present() -> bool:
    """El placeholder de Terraform no es una llave: el adaptador lo trata como
    ausente (todas las percepciones caerían con `no_api_key`)."""
    key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    return bool(key) and not key.upper().startswith("PLACEHOLDER")


# ── Lo que se muestra (el GET y `estado`) ───────────────────────────────────


def _metrics(vault: Path) -> ShadowMetrics:
    try:
        return shadow_metrics(vault, now_ms=_now_ms(), profile=_profile())
    except Exception as exc:  # noqa: BLE001 — sin métricas la sombra cuenta como cero: nadie sube por error
        logger.warning("perception.shadow_metrics_failed", error=repr(exc)[:200])
        return ShadowMetrics(days=0, turns=0, fallback_rate=None, p95_ms=None)


def _probe(vault: Path) -> dict[str, Any]:
    """El resumen de la última sonda diaria de Jev (`decisions/probe.py`);
    `sin_datos` si todavía no corrió."""
    report = read_latest_probe(vault) or {}
    at_ms = report.get("at_ms")
    pass_rate = report.get("pass_rate")
    return {
        "status": str(report.get("status") or "sin_datos"),
        "at_ms": at_ms if isinstance(at_ms, int) and not isinstance(at_ms, bool) else None,
        "pass_rate": pass_rate if isinstance(pass_rate, (int, float)) and not isinstance(pass_rate, bool) else None,
        "models": [str(m) for m in report.get("models") or [] if isinstance(m, str)],
    }


def _facts(vault: Path, state: RolloutState) -> tuple[RolloutFacts, dict[str, Any], dict[str, Any]]:
    metrics = _metrics(vault)
    probe = _probe(vault)
    facts = RolloutFacts(
        ceiling=_ceiling(),
        current=state.mode if state.mode in MODES else "off",
        signal_meta_enabled=(os.getenv("SALES_SIGNAL_INBOUND_META") or "").strip().lower() in {"on", "1", "true"},
        api_key_present=api_key_present(),
        shadow_days=metrics.days,
        shadow_turns=metrics.turns,
        shadow_fallback_rate=metrics.fallback_rate,
        shadow_p95_ms=metrics.p95_ms,
        shadow_model_changed=metrics.model_changed,
        probe_status=probe["status"] if probe["at_ms"] is not None else None,
        probe_age_ms=_now_ms() - probe["at_ms"] if probe["at_ms"] is not None else None,
    )
    return facts, asdict(metrics), probe


def _capability_facts(vault: Path, capability: str, mode: str, ceiling: str) -> CapabilityFacts:
    try:
        return capability_facts(capability, vault_dir=vault, now_ms=_now_ms(), ceiling=ceiling, current=mode)
    except Exception as exc:  # noqa: BLE001 — sin métricas cuenta como cero: nadie sube por error
        logger.warning("decisions.capability_facts_failed", capability=capability, error=repr(exc)[:200])
        return CapabilityFacts(ceiling, mode, 0, 0, None, None, 0, 0, 0)


def _capabilities(vault: Path) -> dict[str, Any]:
    ceiling = bots.capabilities_ceiling()
    out: dict[str, Any] = {}
    for capability, mode in bots.capability_modes(vault).items():
        facts = _capability_facts(vault, capability, mode, ceiling)
        out[capability] = {
            "mode": mode,
            "ceiling": ceiling,
            "facts": asdict(facts),
            "readiness": {t: [asdict(c) for c in capability_readiness(t, facts)] for t in TARGETS},
            "can": {t: list(can_set_capability(t, facts)) for t in TARGETS},
        }
    return out


def _workflow(vault: Path) -> dict[str, Any]:
    mode, ceiling = bots.workflow_mode(vault), bots.workflow_ceiling()
    return {
        "mode": mode,
        "ceiling": ceiling,
        "readiness": {
            t: [asdict(c) for c in workflow_readiness(t, ceiling=ceiling, current=mode)] for t in WORKFLOW_TARGETS
        },
        "can": {t: list(can_set_workflow(t, ceiling=ceiling, current=mode)) for t in WORKFLOW_TARGETS},
    }


def snapshot(vault_dir: Path | str) -> dict[str, Any]:
    """El estado completo: lo que devuelve el GET del contrato y muestra `estado`."""
    vault = Path(vault_dir)
    state = read_state(vault)
    facts, metrics, probe = _facts(vault, state)
    return {
        "state": {**asdict(state), "test_numbers": list(state.test_numbers)},
        "ceiling": facts.ceiling,
        "profile": _profile(),
        "metrics": metrics,
        "probe": probe,
        "readiness": {t: [asdict(c) for c in readiness(t, facts)] for t in TARGETS},
        "can": {t: list(can_set_mode(t, facts)) for t in TARGETS},
        "capabilities": _capabilities(vault),
        "workflow_v2": _workflow(vault),
        "test_numbers_jev": bots.jev_for_test_numbers(vault),
    }


# ── Los cambios ─────────────────────────────────────────────────────────────


def _not_ready(failing: Sequence[str], checks: Sequence[Check]) -> ControlError:
    return ControlError(
        422, {"reason": "not_ready", "failing": list(failing), "readiness": [asdict(c) for c in checks]}
    )


def set_rollout(
    vault_dir: Path | str,
    *,
    actor: str,
    mode: str | None = None,
    canary_percent: Any = None,
    test_numbers: Any = None,
) -> dict[str, Any]:
    """Percepción (las capas del turno), el porcentaje del canary y los
    números de prueba. Lo que viene se valida siempre; lo guardado, solo al
    subir (un valor raro guardado nunca bloquea apagar ni bajar)."""
    vault = Path(vault_dir)
    current = read_state(vault)
    if mode is None:
        mode = current.mode if current.mode in MODES else "off"
    if mode not in MODES:
        raise ControlError(422, {"reason": "invalid_mode", "message": _MODES_MSG})
    raising = _rank(mode) > _rank(current.mode)
    percent = current.canary_percent if canary_percent is None else canary_percent
    if (canary_percent is not None or raising) and (
        not isinstance(percent, int) or isinstance(percent, bool) or not 0 <= percent <= 100
    ):
        raise ControlError(422, {"reason": "invalid_percent", "message": "El porcentaje va de 0 a 100."})
    numbers = list(current.test_numbers) if test_numbers is None else test_numbers
    if (test_numbers is not None or raising) and (
        not isinstance(numbers, list) or not all(isinstance(n, str) and SID_RE.fullmatch(n) for n in numbers)
    ):
        raise ControlError(422, {"reason": "invalid_test_numbers", "message": "Números de prueba como wa_57…"})
    if raising:
        facts, _, _ = _facts(vault, current)
        failing = can_set_mode(mode, facts)
        if failing:
            raise _not_ready(failing, readiness(mode, facts))
        # Las métricas tardan: si mientras tanto alguien apagó (o movió el
        # modo), subir no pisa ese cambio.
        if read_state(vault) != current:
            raise ControlError(409, _CHANGED)
    state = RolloutState(
        mode=mode, canary_percent=percent, test_numbers=tuple(numbers), updated_at_ms=_now_ms(), updated_by=actor
    )
    write_state(vault, state)
    logger.info(
        "perception.rollout_changed",
        previous=current.mode, mode=mode, canary_percent=percent, test_numbers=len(numbers), by=actor,
    )
    return snapshot(vault)


def add_test_number(vault_dir: Path | str, sid: str, *, actor: str) -> dict[str, Any]:
    numbers = list(read_state(Path(vault_dir)).test_numbers)
    if sid not in numbers:
        numbers.append(sid)
    return set_rollout(vault_dir, actor=actor, test_numbers=numbers)


def remove_test_number(vault_dir: Path | str, sid: str, *, actor: str) -> dict[str, Any]:
    numbers = [n for n in read_state(Path(vault_dir)).test_numbers if n != sid]
    return set_rollout(vault_dir, actor=actor, test_numbers=numbers)


def set_capability(vault_dir: Path | str, capability: str, mode: str, *, actor: str) -> dict[str, Any]:
    """UNA capacidad del motor de decisiones dentro del techo: `off`=reglas,
    `shadow`=sombra, `canary`/`on`=Jev. Bajar siempre pasa; subir exige su vara."""
    vault = Path(vault_dir)
    if capability not in bots.CAPABILITIES:
        raise ControlError(
            422, {"reason": "unknown_capability", "message": "Capacidades: " + ", ".join(bots.CAPABILITIES) + "."}
        )
    if mode not in MODES:
        raise ControlError(422, {"reason": "invalid_mode", "message": _MODES_MSG})
    current = bots.capability_modes(vault)[capability]
    if _rank(mode) > _rank(current):
        facts = _capability_facts(vault, capability, current, bots.capabilities_ceiling())
        failing = can_set_capability(mode, facts)
        if failing:
            raise _not_ready(failing, capability_readiness(mode, facts))
        if bots.capability_modes(vault)[capability] != current:
            raise ControlError(409, _CHANGED)
    bots.write_capability_modes(vault, {capability: mode})
    logger.info("decisions.capability_changed", capability=capability, previous=current, mode=mode, by=actor)
    return snapshot(vault)


def set_workflow(vault_dir: Path | str, mode: str, *, actor: str) -> dict[str, Any]:
    """La versión del workflow de ventas: V2 en `canary` actúa en los números
    de prueba y el porcentaje; `on`, en todos. Bajar siempre pasa (el
    siguiente mensaje arranca V1)."""
    vault = Path(vault_dir)
    if mode not in bots.WORKFLOW_MODES:
        raise ControlError(422, {"reason": "invalid_mode", "message": "Modos del workflow: off, canary u on."})
    current = bots.workflow_mode(vault)
    failing = can_set_workflow(mode, ceiling=bots.workflow_ceiling(), current=current)
    if failing:
        raise _not_ready(failing, workflow_readiness(mode, ceiling=bots.workflow_ceiling(), current=current))
    bots.write_workflow_mode(vault, mode)
    logger.info("decisions.workflow_changed", previous=current, mode=mode, by=actor)
    return snapshot(vault)


def jev_for_test_numbers_readiness(vault_dir: Path | str) -> tuple[Check, ...]:
    """Lo que pide prender «los números de prueba deciden con Jev». Sin la
    vara: solo esos números cambian y nunca más allá del techo de Terraform."""
    numbers = read_state(Path(vault_dir)).test_numbers
    ceiling = bots.capabilities_ceiling()
    return (
        Check("test_numbers", bool(numbers), f"{len(numbers)} números de prueba (al menos uno)"),
        Check("within_ceiling", _rank(ceiling) >= _rank("canary"), f"techo de las capacidades: {ceiling} (canary o más)"),
        Check("api_key", api_key_present(), "OPENROUTER_API_KEY cargada"),
    )


def set_test_numbers_jev(vault_dir: Path | str, enabled: bool, *, actor: str) -> dict[str, Any]:
    """Prende o apaga «los números de prueba deciden con Jev». Apagar siempre pasa."""
    vault = Path(vault_dir)
    previous = bots.jev_for_test_numbers(vault)
    if enabled and not previous:
        checks = jev_for_test_numbers_readiness(vault)
        failing = [c.code for c in checks if not c.ok]
        if failing:
            raise _not_ready(failing, checks)
    bots.write_test_numbers_jev(vault, enabled)
    logger.info("decisions.test_numbers_jev_changed", previous=previous, enabled=bool(enabled), by=actor)
    return snapshot(vault)


# ── El comando ──────────────────────────────────────────────────────────────


def _default_vault() -> Path:
    """El vault del contenedor donde corre: la misma variable y el mismo
    default que `src.sdk.runtime.WORKSPACE_VAULT_DIR`, sin importarlo (arrastra
    Temporal y el núcleo del motor no lo toca)."""
    return Path(os.environ.get("WORKSPACE_VAULT_DIR", "./hubara_vault")).resolve()


def _mask(sid: str) -> str:
    digits = re.sub(r"\D", "", sid)
    return "···" + digits[-4:] if len(digits) >= 4 else "···"


def _print_state(snap: dict[str, Any]) -> None:
    state = snap["state"]
    caps = snap["capabilities"]
    on_jev = sorted(c for c, v in caps.items() if v["mode"] in ("canary", "on"))
    shadow = sorted(c for c, v in caps.items() if v["mode"] == "shadow")
    ceiling_caps = next(iter(caps.values()), {}).get("ceiling", "off")
    numbers = ", ".join(_mask(n) for n in state["test_numbers"]) or "ninguno"
    print(f"Percepción (capas del turno): {state['mode']}  (techo {snap['ceiling']}, perfil {snap['profile']})")
    print(f"Workflow V2: {snap['workflow_v2']['mode']}  (techo {snap['workflow_v2']['ceiling']})")
    print(f"Capacidades (techo {ceiling_caps}): en Jev {', '.join(on_jev) or 'ninguna'}; "
          f"en sombra {', '.join(shadow) or 'ninguna'}; las demás en reglas")
    print(f"Números de prueba: {numbers}  (porcentaje {state['canary_percent']} %)")
    print(f"Números de prueba con Jev: {'sí' if snap['test_numbers_jev'] else 'no'}")
    probe = snap["probe"]
    metrics = snap["metrics"]
    print(f"Sombra: {metrics['days']} días, {metrics['turns']} turnos; sonda de Jev: {probe['status']}")
    if state.get("updated_by"):
        print(f"Último cambio de la percepción: {state['updated_by']}")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="control",
        description="Control del bot nuevo en producción (solo por comando). Cada cambio exige --por.",
    )
    p.add_argument("--por", default="", help="quién hace el cambio (queda firmado como comando:<quien>)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("estado", help="ver qué corre hoy")
    n = sub.add_parser("numeros", help="números de prueba (wa_57…)")
    n.add_argument("accion", choices=["agregar", "quitar"])
    n.add_argument("numero")
    pct = sub.add_parser("porcentaje", help="porcentaje del canary (0 a 100)")
    pct.add_argument("valor", type=int)
    per = sub.add_parser("percepcion", help="capas del turno con Jev")
    per.add_argument("modo", choices=list(MODES))
    cap = sub.add_parser("capacidad", help="una capacidad del motor (o «todas»)")
    cap.add_argument("nombre")
    cap.add_argument("modo", choices=list(MODES))
    wf = sub.add_parser("workflow", help="versión del workflow de ventas")
    wf.add_argument("modo", choices=list(bots.WORKFLOW_MODES))
    pj = sub.add_parser("prueba-jev", help="¿los números de prueba deciden con Jev?")
    pj.add_argument("valor", choices=["si", "no"])
    return p


def _run(args: argparse.Namespace, vault: Path, actor: str) -> dict[str, Any]:
    if args.cmd == "numeros":
        change = add_test_number if args.accion == "agregar" else remove_test_number
        return change(vault, args.numero, actor=actor)
    if args.cmd == "porcentaje":
        return set_rollout(vault, actor=actor, canary_percent=args.valor)
    if args.cmd == "percepcion":
        return set_rollout(vault, actor=actor, mode=args.modo)
    if args.cmd == "capacidad":
        names = list(bots.CAPABILITIES) if args.nombre == "todas" else [args.nombre]
        snap: dict[str, Any] = {}
        for name in names:
            snap = set_capability(vault, name, args.modo, actor=actor)
        return snap
    if args.cmd == "workflow":
        return set_workflow(vault, args.modo, actor=actor)
    return set_test_numbers_jev(vault, args.valor == "si", actor=actor)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    vault = _default_vault()
    if args.cmd == "estado":
        _print_state(snapshot(vault))
        return 0
    who = (args.por or "").strip()
    if not who:
        print("Cada cambio lleva quién lo hace: agrega --por <nombre> antes del subcomando.", file=sys.stderr)
        return 2
    try:
        snap = _run(args, vault, f"comando:{who[:60]}")
    except ControlError as exc:
        detail = exc.detail
        print(f"No se aplicó ({detail.get('reason')}): {detail.get('message') or ''}".rstrip(), file=sys.stderr)
        for check in detail.get("readiness") or []:
            if not check.get("ok"):
                print(f"  ✗ {check.get('code')}: {check.get('detail')}", file=sys.stderr)
        return 2
    print("Listo.")
    _print_state(snap)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
