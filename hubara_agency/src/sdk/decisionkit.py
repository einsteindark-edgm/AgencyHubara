"""DecisionKit — paquetes de decisión, para plugins.

Fachada SDK (P-28) sobre `src.platform.decisions`: la "inteligencia" de una
tienda escrita como datos tipados y versionados (YAML + condiciones CEL),
certificada antes de desplegar. Diseño: PAQUETES_DE_DECISION.md;
decisión del lenguaje: ADR-2026-10-01-decision-bundles.md; uso:
docs/_sdk/17-decisionkit.md.

Uso canónico (motor de decisiones del plugin de ventas)::

    from src.sdk.decisionkit import DOUBT, answers_from_result, load_bundle

    bundle = load_bundle(bundle_dir, catalog_path)   # BundleError si no compila
    table = bundle.capability("baja")
    value = table.decide(answers=answers_from_result(table.spec.questions, result))
    if value is DOUBT:
        ...  # decide la regla

Certificar desde la terminal: ``uv run python -m src.sdk.cli decisions check``.
"""
from __future__ import annotations

from src.platform.decisions import (
    DOUBT as DOUBT,
)
from src.platform.decisions import (
    ENGINE_CONTRACT as ENGINE_CONTRACT,
)
from src.platform.decisions import (
    Bundle as Bundle,
)
from src.platform.decisions import (
    BuiltinRef as BuiltinRef,
)
from src.platform.decisions import (
    BundleError as BundleError,
)
from src.platform.decisions import (
    Capability as Capability,
)
from src.platform.decisions import (
    Catalog as Catalog,
)
from src.platform.decisions import (
    CelExpressions as CelExpressions,
)
from src.platform.decisions import (
    CompiledBundle as CompiledBundle,
)
from src.platform.decisions import (
    CompiledCapability as CompiledCapability,
)
from src.platform.decisions import (
    Diagnostic as Diagnostic,
)
from src.platform.decisions import (
    ExpressionError as ExpressionError,
)
from src.platform.decisions import (
    ExpressionPort as ExpressionPort,
)
from src.platform.decisions import (
    Question as Question,
)
from src.platform.decisions import (
    answers_from_result as answers_from_result,
)
from src.platform.decisions import (
    check_bundle as check_bundle,
)
from src.platform.decisions import (
    load_bundle as load_bundle,
)
