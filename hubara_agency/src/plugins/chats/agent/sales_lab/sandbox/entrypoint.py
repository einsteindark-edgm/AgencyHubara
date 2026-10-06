"""Entrypoint de UN caso en la caja del laboratorio (plan §3.3–§3.5, PR 11).

    python -m src.plugins.chats.agent.sales_lab.sandbox.entrypoint \\
        --case case.json --bench /lab/bench/<banco> \\
        --sandbox /lab/runs/<corrida>/<brazo>/<rep>/<caso> --out result.json [--arm B] [--store-bundle ventas-2]

Un proceso por caso: el vault, el historial del LLM y los clientes quedan en
caché por proceso (`lru_cache`, constantes de módulo), así que cada caso
arranca limpio. Orden (candado 3 del plan §3.5):

  1. el entorno del sandbox ANTES de importar la app (`sandbox/env.py`),
     con el paquete de decisión del brazo si lo fija (`B@ventas-2`,
     PAQUETES_DE_DECISION.md F6: las tools leen el vocabulario al importarse)
     o el de la tienda, que manda el lanzador (`--store-bundle`);
  2. el guard de la caja: sin llaves de producción, el Temporal de la caja,
     carpetas dentro de /lab. Si falla, sale con 2 sin importar nada;
  3. se importa la app y el guard corre OTRA vez: `load_dotenv` de la config
     podría haber sumado variables de un `.env` perdido en la imagen (sale 3);
  4. corre el turno (`sandbox/turn.py`) contra el Temporal de la caja y
     escribe el resultado. Sale 0 si el turno terminó, 1 si no.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from src.plugins.chats.agent.sales.decisions.bots import bot_for_arm
from src.plugins.chats.agent.sales_lab.arms import arm_env
from src.plugins.chats.agent.sales_lab.guard import check_lab_env
from src.plugins.chats.agent.sales_lab.sandbox.env import prepare_case_env


def _arm(value: str) -> str:
    """Un brazo que el simulador sabe correr (con su paquete, si lo fija)."""
    try:
        bot_for_arm(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None
    return value


def _store_bundle(value: str) -> str:
    """El paquete de la tienda que mandó el lanzador: tiene que estar en la
    imagen. Vacío = el default del código (órdenes de antes)."""
    if not value:
        return ""
    from src.plugins.chats.shared.store_pack import bundle_dir
    from src.sdk.decisionkit import BundleError

    try:
        bundle_dir(value)
    except (BundleError, OSError) as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None
    return value


def _refuse(stage: str, problems: list[str]) -> None:
    print(f"sales_lab/sandbox NO corre el caso ({stage}):", *problems, sep="\n  ", file=sys.stderr)


async def _run(args: argparse.Namespace) -> int:
    from src.sdk.runtime import get_temporal_client

    problems = check_lab_env(os.environ)
    if problems:
        _refuse("después de importar la app", problems)
        return 3
    from src.plugins.chats.agent.sales_lab.sandbox.turn import run_case

    case = json.loads(Path(args.case).read_text(encoding="utf-8"))
    client = await get_temporal_client()
    result = await run_case(
        case,
        bench_dir=Path(args.bench),
        sandbox_dir=Path(args.sandbox),
        client=client,
        timeout_s=float(args.timeout),
        arm=args.arm,
        store_bundle=args.store_bundle,
    )
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, default=str), encoding="utf-8")
    return 0 if result.get("error") is None else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Un turno simulado del bot de ventas en el sandbox del laboratorio")
    parser.add_argument("--case", required=True)
    parser.add_argument("--bench", required=True)
    parser.add_argument("--sandbox", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--timeout", default="600")
    parser.add_argument("--arm", default="A1", type=_arm)
    parser.add_argument("--store-bundle", default="", type=_store_bundle)
    args = parser.parse_args(argv)
    os.environ.update(arm_env(args.arm, args.store_bundle))
    prepare_case_env(os.environ, Path(args.sandbox))
    problems = check_lab_env(os.environ)
    if problems:
        _refuse("guard de la caja", problems)
        return 2
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
