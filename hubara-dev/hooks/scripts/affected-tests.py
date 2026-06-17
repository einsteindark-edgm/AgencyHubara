#!/usr/bin/env python3
"""PostToolUse hook (plugin hubara-dev): tras editar código de producción, corre
el test AFECTADO y devuelve 🔴/🟢 como additionalContext — cierra el bucle TDD
de forma determinista (lo que un hook SÍ puede hacer con certeza, a diferencia
de adivinar "existe un test que falla").

- `.py` bajo hubara_agency/.../src/ → corre `tests/**/test_<stem>.py` si existe.
- `.ts`/`.tsx` bajo frontend_dashboard/.../src/ → corre `vitest related <file>`.
- Sin test afectado → empuja suave (TDD: ¿escribiste el test?).

Time-boxed y best-effort: ante cualquier error/timeout, no estorba (exit 0).
Los dummies van inline (el shell del subprocess no hereda un export previo).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

_TEST = re.compile(r"(/tests?/|/__tests__/|\.test\.|\.spec\.|/test_|_test\.py$|\.arch\.test\.)")
_DUMMIES = {
    "MEDUSA_BASE_URL": "http://medusa.invalid",
    "MEDUSA_ADMIN_TOKEN": "ci-dummy",
    "OTEL_SDK_DISABLED": "true",
}
_TIMEOUT = 100


def _emit(msg: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": msg}}))


def _run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    env = {**os.environ, **_DUMMIES}
    try:
        p = subprocess.run(
            cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=_TIMEOUT
        )
        return p.returncode, (p.stdout + p.stderr)[-1200:]
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except Exception as exc:  # noqa: BLE001
        return 125, str(exc)


def _backend(file_path: str, root: Path) -> None:
    hub = root / "hubara_agency"
    stem = Path(file_path).stem
    matches = sorted(p for p in (hub / "tests").rglob(f"test_{stem}.py"))
    if not matches:
        _emit(
            f"TDD (hubara-dev): no encontré un test afectado para `{stem}.py`. "
            "¿Existe el test que exige este cambio? Si no, escribilo (00-tdd-law.md)."
        )
        return
    rel = [str(m.relative_to(hub)) for m in matches[:3]]
    code, out = _run(["uv", "run", "pytest", *rel, "-q"], hub)
    tail = out.strip().splitlines()[-1] if out.strip() else ""
    if code == 0:
        _emit(f"🟢 TDD (hubara-dev): test afectado verde — {' '.join(rel)} ({tail})")
    elif code in (124, 125):
        _emit(f"TDD (hubara-dev): no pude correr {' '.join(rel)} ({out.strip()[:120]}).")
    else:
        _emit(
            f"🔴 TDD (hubara-dev): test afectado FALLA — {' '.join(rel)}.\n{tail}\n"
            "Si acabás de escribir el rojo, implementá el mínimo para el verde. "
            "Si era verde y ahora falla, lo rompiste — arreglalo antes de seguir."
        )


def _frontend(file_path: str, root: Path) -> None:
    fe = root / "frontend_dashboard"
    try:
        rel = str(Path(file_path).resolve().relative_to(fe.resolve()))
    except Exception:
        rel = file_path
    code, out = _run(["npx", "vitest", "related", "--run", rel], fe)
    tail = out.strip().splitlines()[-1] if out.strip() else ""
    if code == 0:
        _emit(f"🟢 TDD (hubara-dev): vitest related verde — {rel} ({tail})")
    elif code in (124, 125):
        _emit(f"TDD (hubara-dev): no pude correr vitest related {rel} ({out.strip()[:120]}).")
    else:
        _emit(f"🔴 TDD (hubara-dev): vitest related FALLA — {rel}.\n{tail}")


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    file_path = (data.get("tool_input") or {}).get("file_path", "") or ""
    if not file_path or _TEST.search(file_path) or "/src/" not in file_path:
        sys.exit(0)

    root = Path(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
    try:
        if file_path.endswith(".py") and "/hubara_agency/" in file_path:
            _backend(file_path, root)
        elif file_path.endswith((".ts", ".tsx")) and "/frontend_dashboard/" in file_path:
            _frontend(file_path, root)
    except Exception:
        pass  # nunca romper el flujo del usuario
    sys.exit(0)


if __name__ == "__main__":
    main()
