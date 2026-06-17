"""Premortem F-SDK — la superficie del SDK no arrastra vendors por accidente.

Hallazgo del premortem (2026-06-12): el ``__init__`` eager del connectorkit
hizo que importar ``src.plugins.ads.api`` (consumidor de SOLO atribución,
stdlib-pura) arrastrara la cadena completa de composición de los ports —
**+759 módulos vendor (litellm/medusa) y +5s de import** en todo proceso que
toque ads (el boot del API incluido). Fix: carga lazy PEP 562 por símbolo.

Estos guards lo vuelven imposible de regresionar. Corren en SUBPROCESS para
medir un import limpio (el proceso de pytest ya tiene medio mundo importado).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HUB_ROOT = Path(__file__).resolve().parents[2]

_VENDOR_PROBE = (
    "import sys\n"
    "{imports}\n"
    "bad = sorted(m for m in sys.modules if m.startswith(('litellm', 'src.platform.medusa'))"
    " or '.medusa_' in m or m.startswith('src.platform.orders.composition'))\n"
    "print(','.join(bad))\n"
)


def _run_probe(imports: str) -> list[str]:
    env = {**os.environ, "OTEL_SDK_DISABLED": "true"}
    env.pop("MEDUSA_BASE_URL", None)  # el probe debe vivir SIN env de vendor
    env.pop("MEDUSA_ADMIN_TOKEN", None)
    proc = subprocess.run(
        [sys.executable, "-c", _VENDOR_PROBE.format(imports=imports)],
        cwd=HUB_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"el probe de import falló (¿import-time crash sin env de vendor?):\n"
        f"{proc.stderr[-2000:]}"
    )
    out = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    return [m for m in out.split(",") if m]


def test_importing_ads_api_does_not_drag_vendors() -> None:
    """ads consume SOLO atribución (stdlib) — su import no carga litellm/medusa."""
    dragged = _run_probe("import src.plugins.ads.api")
    assert not dragged, (
        f"importar ads.api arrastró {len(dragged)} módulos vendor (premortem "
        f"F-SDK; el connectorkit debe ser lazy): {dragged[:8]}"
    )


def test_importing_connectorkit_is_lazy_until_attribute_access() -> None:
    """El kit en sí es barato; el vendor se paga recién al usar SU port."""
    dragged = _run_probe("import src.sdk.connectorkit")
    assert not dragged, (
        f"import src.sdk.connectorkit cargó vendors sin que nadie pida un "
        f"port (PEP 562 roto): {dragged[:8]}"
    )


def test_lazy_exports_mirror_eager_ports_module() -> None:
    """`connectorkit.__getattr__` y `connectorkit/ports.py` no pueden divergir."""
    import src.sdk.connectorkit as ck

    lazy = set(ck._LAZY_EXPORTS)
    # ports.py es el espejo eager — todo lo lazy debe existir ahí con el mismo nombre:
    import src.sdk.connectorkit.ports as eager

    missing = sorted(n for n in lazy if not hasattr(eager, n))
    assert not missing, f"símbolos lazy sin espejo eager en ports.py: {missing}"
    # y el acceso lazy resuelve de verdad (cache incluido):
    assert ck.OrderQueryPort is eager.OrderQueryPort
