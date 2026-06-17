"""Reporte de certificación — el artefacto que conecta TCK ↔ CI ↔ catálogo.

``run_conformance(plugin_id)`` corre TODOS los checks y computa el nivel:

- ``none`` — C0 falló (el manifest ni siquiera es válido).
- ``C0`` — Declarado: manifest válido + archetype, pero algo declarado no existe.
- ``C1`` — Cargable: todo lo declarado existe, pero alguna P-rule falla.
- ``C2`` — Certificado: TCK completo verde (warnings permitidos, listados).
- ``C3`` — reservado (conducta: specs + evals + smoke — F-SDK-7).

El reporte se escribe en ``hubara_agency/.hubara/certification/<id>.json``
(gitignored — es DERIVABLE; committearlo invitaría drift). Lleva ``git_sha`` +
``generated_at``: un consumidor (catálogo) debe degradar un reporte stale a
"sin certificar", nunca inventar verde. La certificación gobierna MERGE y
catálogo — jamás el runtime (ADR-2026-06-12 §5).
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from src.sdk.testkit.checks import CheckResult, build_context, run_all_checks

SDK_VERSION = "0.1.0"

_C0_CODES = {"C0-SCHEMA", "P-29A"}


@dataclass(frozen=True)
class CertificationReport:
    plugin: str
    archetype: str | None
    level: str
    sdk: str
    git_sha: str
    generated_at: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def failed(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status == "fail"]

    @property
    def warnings(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status == "warn"]

    def to_dict(self) -> dict:
        return {
            "plugin": self.plugin,
            "archetype": self.archetype,
            "level": self.level,
            "sdk": self.sdk,
            "git_sha": self.git_sha,
            "generated_at": self.generated_at,
            "summary": {
                "pass": sum(1 for c in self.checks if c.status == "pass"),
                "fail": len(self.failed),
                "warn": len(self.warnings),
                "skip": sum(1 for c in self.checks if c.status == "skip"),
            },
            "checks": [asdict(c) for c in self.checks],
        }


def compute_level(checks: list[CheckResult]) -> str:
    fails = {c.code for c in checks if c.status == "fail"}
    if fails & _C0_CODES:
        return "none"
    if any(code.startswith("C1-") for code in fails):
        return "C0"
    if fails:
        return "C1"
    return "C2"


@lru_cache(maxsize=8)
def _git_sha(repo_root: Path) -> str:
    # lru_cache (premortem F-SDK): el catálogo certifica N plugins por request
    # — sin cache eran N subprocesos `git rev-parse` por GET del grafo.
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return out.stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def run_conformance(plugin_id: str, repo_root: Path | None = None) -> CertificationReport:
    """Corre el TCK completo de UN plugin y devuelve su reporte (sin escribir)."""
    ctx = build_context(plugin_id, repo_root=repo_root)
    checks = run_all_checks(ctx)
    return CertificationReport(
        plugin=plugin_id,
        archetype=ctx.typed.archetype if ctx.typed else None,
        level=compute_level(checks),
        sdk=SDK_VERSION,
        git_sha=_git_sha(ctx.repo_root),
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        checks=checks,
    )


def certification_dir(repo_root: Path) -> Path:
    return repo_root / "hubara_agency" / ".hubara" / "certification"


def write_report(report: CertificationReport, repo_root: Path | None = None) -> Path:
    """Persiste el reporte JSON (atomicidad no necesaria: archivo derivable)."""
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[4]
    out_dir = certification_dir(repo_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{report.plugin}.json"
    path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path
