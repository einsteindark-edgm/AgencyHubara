"""Guards de G1 (lecciones L-1/L-2 hechas gate):
- G-RUN-SIG: la firma de `run` debe aceptar la inyección (input, *, ports, tools).
- el execution-id del runtime es DETERMINISTA (contador, no time/random)."""
from __future__ import annotations

from sdk.runtime import LocalRuntime
from sdk.testkit.checks import _accepts_injection


def test_run_sig_guard_acepta_firma_uniforme() -> None:
    assert _accepts_injection(lambda input, *, ports=None, tools=None: None)
    assert _accepts_injection(lambda input, **kw: None)


def test_run_sig_guard_caza_firma_incompleta() -> None:
    # le falta `tools` → el loader la rompería en runtime (L-2); el gate la caza.
    assert not _accepts_injection(lambda input, *, ports=None: None)


def test_execution_id_es_determinista() -> None:
    # dos runtimes frescos producen el MISMO primer id (contador, no time/random; L-1).
    a = LocalRuntime().run(lambda x: x, {"k": 1})
    b = LocalRuntime().run(lambda x: x, {"k": 2})
    assert a.id == b.id == "local-000001"
