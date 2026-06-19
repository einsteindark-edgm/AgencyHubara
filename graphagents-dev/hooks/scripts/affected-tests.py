#!/usr/bin/env python3
"""PostToolUse hook (plugin graphagents-dev): tras editar producción de
GraphAgents, corre el test afectado (golden-replay / conformance / arquitectura)
y reporta el resultado vía additionalContext. NO bloquea — informa. La idea es
cerrar el loop rojo->verde sin que el agente tenga que acordarse de correr el test.

Heurística de mapeo archivo -> test:
  graphs/<x>.py     -> tests/graphs/test_<x>_golden.py + tests/conformance/test_<x>_conformance.py
  sdk/<...>.py       -> tests/architecture/  (los checks del TestKit + validez de manifests)
  tools/<x>.py       -> tests/**/test_*<x>*.py
Si no encuentra ningún test, lo dice (recordatorio de escribir el rojo primero).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

_PROD = re.compile(r"/GraphAgents/(sdk|graphs|tools)/.*\.py$")
_TEST = re.compile(r"(/tests?/|\.test\.|/test_|_test\.py$|conftest\.py$|/fixtures/)")


def _emit(msg: str) -> None:
    print(
        json.dumps(
            {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": msg}}
        )
    )


def _ga_root(file_path: str) -> Path | None:
    marker = "/GraphAgents/"
    i = file_path.find(marker)
    if i < 0:
        return None
    return Path(file_path[: i + len(marker) - 1])


def _candidates(ga: Path, file_path: str) -> list[Path]:
    p = Path(file_path)
    stem = p.stem
    out: list[Path] = []
    if "/graphs/" in file_path:
        out += [
            ga / "tests" / "graphs" / f"test_{stem}_golden.py",
            ga / "tests" / "conformance" / f"test_{stem}_conformance.py",
        ]
    if "/sdk/" in file_path:
        out += [ga / "tests" / "architecture"]
    if "/tools/" in file_path:
        out += list((ga / "tests").rglob(f"test_*{stem}*.py"))
    # fallback genérico por stem
    out += list((ga / "tests").rglob(f"test_*{stem}*.py"))
    seen, uniq = set(), []
    for c in out:
        if c.exists() and str(c) not in seen:
            seen.add(str(c))
            uniq.append(c)
    return uniq


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    file_path = (data.get("tool_input") or {}).get("file_path", "") or ""
    if not _PROD.search(file_path) or _TEST.search(file_path):
        sys.exit(0)

    ga = _ga_root(file_path)
    if ga is None or not ga.exists():
        sys.exit(0)

    tests = _candidates(ga, file_path)
    if not tests:
        _emit(
            f"graphagents-dev: editaste {Path(file_path).name} y NO encontré un test "
            "que lo cubra. En este harness el test va PRIMERO (golden-replay para una "
            "capability, decision payload para una tool). Escribí el rojo antes de "
            "seguir, o delegá en graph-tdd-author."
        )
        sys.exit(0)

    rels = [str(t.relative_to(ga)) for t in tests]
    try:
        proc = subprocess.run(
            ["uv", "run", "pytest", *rels, "-q", "--no-header"],
            cwd=str(ga),
            capture_output=True,
            text=True,
            timeout=150,
            env={**os.environ},
        )
    except FileNotFoundError:
        _emit("graphagents-dev: `uv` no está en PATH — no pude correr el test afectado. Corré `/graphagents-gates` a mano.")
        sys.exit(0)
    except subprocess.TimeoutExpired:
        _emit(f"graphagents-dev: el test afectado ({', '.join(rels)}) excedió 150s — corré `/graphagents-gates` a mano.")
        sys.exit(0)

    tail = (proc.stdout or proc.stderr or "").strip().splitlines()[-8:]
    verdict = "VERDE" if proc.returncode == 0 else f"ROJO (exit {proc.returncode})"
    _emit(
        f"graphagents-dev: test afectado {verdict} -> {', '.join(rels)}\n"
        + "\n".join(tail)
        + ("\nMantené verde (refactor seguro)." if proc.returncode == 0
           else "\nSi es el rojo esperado, implementá el mínimo para ponerlo verde.")
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
