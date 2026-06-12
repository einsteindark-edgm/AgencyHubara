"""TestKit (el TCK) — la suite de conformance que cada plugin INSTANCIA.

El movimiento clave del plan (F-SDK-2): los gates dejan de ser solo una suite
central que "policía" y se vuelven un kit que el plugin instancia en 3 líneas
— así un check nuevo acá upgradea a TODOS los plugins de una (cero drift de
copy-paste, lección L-3):

    # hubara_agency/tests/conformance/test_eta_conformance.py (el archivo ENTERO)
    from src.sdk.testkit import conformance_suite

    globals().update(conformance_suite("eta"))

El gate P-27 (tests/architecture/test_p27_conformance_files.py) exige que ese
archivo exista por cada plugin del repo — la emulación del "no compila".

Tres frontends, una fuente (``checks.py``):

- ``conformance_suite(id)`` → tests pytest (este módulo);
- ``run_conformance(id)`` → ``CertificationReport`` (CLI / CI / catálogo);
- ``python -m src.sdk.cli check`` → salida estilo rustc.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from src.sdk.diagnostics import format_diagnostic
from src.sdk.testkit.archetypes import (
    PROFILES as PROFILES,
    ArchetypeProfile as ArchetypeProfile,
    GlobRule as GlobRule,
)
from src.sdk.testkit.checks import (
    ALL_CHECKS as ALL_CHECKS,
    CheckContext as CheckContext,
    CheckResult as CheckResult,
    build_context as build_context,
    run_all_checks as run_all_checks,
)
from src.sdk.testkit.report import (
    SDK_VERSION as SDK_VERSION,
    CertificationReport as CertificationReport,
    certification_dir as certification_dir,
    compute_level as compute_level,
    run_conformance as run_conformance,
    write_report as write_report,
)


def conformance_suite(
    plugin_id: str, repo_root: Path | None = None
) -> dict[str, Callable[[], None]]:
    """Genera los tests pytest del TCK para ``plugin_id``.

    Devuelve ``{test_name: test_fn}`` para inyectar con
    ``globals().update(...)``. Un test por check; cada fallo se reporta con el
    diagnóstico completo (código + fix + ref) — el mensaje de error ES la
    documentación.

    Los WARNINGS del perfil (migraciones pendientes) NO fallan el test: se
    listan en el reporte de ``certify`` y en el catálogo.
    """
    suite: dict[str, Callable[[], None]] = {}

    def _make(check_fn) -> Callable[[], None]:
        def _test() -> None:
            ctx = build_context(plugin_id, repo_root=repo_root)
            failures = [r for r in check_fn(ctx) if r.status == "fail"]
            if failures:
                rendered = "\n\n".join(
                    format_diagnostic(r.code, r.detail, location=r.location)
                    for r in failures
                )
                raise AssertionError(
                    f"TCK[{plugin_id}] {check_fn.__name__} — "
                    f"{len(failures)} violación(es):\n\n{rendered}"
                )

        _test.__doc__ = (check_fn.__doc__ or check_fn.__name__).strip().splitlines()[0]
        return _test

    for check_fn in ALL_CHECKS:
        name = check_fn.__name__.removeprefix("check_")
        suite[f"test_tck_{plugin_id}_{name}"] = _make(check_fn)
    return suite
