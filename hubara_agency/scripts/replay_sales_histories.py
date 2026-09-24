"""Re-juega historias REALES del workflow de ventas contra el código actual.

Un cambio de workflow se despliega solo si las historias vivas siguen
re-jugándose (L-9, L-22). Los fixtures de `tests/test_replay_sales.py` son
sintéticos o congelados; este script toma historias bajadas de Temporal Cloud
y las re-juega todas. Las historias traen teléfonos de clientes: se bajan a un
directorio temporal y NUNCA se commitean.

Bajarlas (receta de la memoria "temporal-cloud-run-inspection"):

    temporal workflow list --tls --limit 400 -o json > list.json
    temporal workflow show --tls --workflow-id <id> --run-id <run> -o json > hNN.json

Re-jugarlas:

    cd hubara_agency && PYTHONPATH=. uv run python scripts/replay_sales_histories.py <directorio>

Sale con código 1 si alguna diverge. Imprime solo conteos y el run id de las
que fallan (sin el workflow id, que lleva el teléfono).
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from src.plugins.chats.agent.sales.workflows.sales_session import HubaraSalesSessionWorkflow


def _load(path: Path) -> WorkflowHistory | None:
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict) or not data.get("events"):
        return None
    started = data["events"][0].get("workflowExecutionStartedEventAttributes") or {}
    if (started.get("workflowType") or {}).get("name") != "HubaraSalesSessionWorkflow":
        return None
    return WorkflowHistory.from_json(path.stem, raw)


async def main(directory: Path) -> int:
    replayer = Replayer(workflows=[HubaraSalesSessionWorkflow])
    ok, failed, skipped = 0, [], 0
    for path in sorted(directory.glob("*.json")):
        history = _load(path)
        if history is None:
            skipped += 1
            continue
        result = await replayer.replay_workflow(history, raise_on_replay_failure=False)
        if result.replay_failure is None:
            ok += 1
        else:
            failed.append((path.name, f"{type(result.replay_failure).__name__}: {str(result.replay_failure)[:300]}"))
    print(f"re-jugadas sin divergir: {ok}; divergen: {len(failed)}; omitidas: {skipped}")
    for name, error in failed:
        print(f"  {name}: {error}")
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(asyncio.run(main(Path(sys.argv[1]))))
