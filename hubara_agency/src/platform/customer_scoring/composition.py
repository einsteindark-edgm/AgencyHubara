"""Composition root del módulo `customer_scoring`.

Patrón estándar DEHA:
  * `get_customer_scoring_port()` con `@lru_cache(maxsize=1)` — singleton
    proceso-wide. El loader cachea con su propio mtime check.
  * El adapter default (`YamlCustomerScoringAdapter`) compone:
      - `YamlRulesLoader` (lee rules.yaml)
      - `FilesystemMetadataStore` (lee metadata.json del vault)
      - `features.compute_customer_features` (pure)
      - `rules.apply_rules` (pure)
"""
from __future__ import annotations

import logging
import time
from functools import lru_cache
from pathlib import Path

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.customer_scoring.features import compute_customer_features
from src.platform.customer_scoring.llm_summary import CustomerSummaryAdapter
from src.platform.customer_scoring.loader import (
    RulesUnavailableError,
    YamlRulesLoader,
)
from src.platform.customer_scoring.port import (
    CustomerScore,
    CustomerScoringPort,
)
from src.platform.customer_scoring.rules import apply_rules
from src.platform.state import FilesystemMetadataStore

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_customer_summary_adapter() -> CustomerSummaryAdapter:
    """Singleton del adapter LLM (on-demand summary).

    Usa el modelo + api_base default (mismo proxy litellm que el resto del
    proyecto). Sin config explícita — el adapter ya tiene defaults sensatos
    y degrada gracefully a un fallback determinístico si la llamada falla.
    """
    return CustomerSummaryAdapter()

# Path canónico del rules.yaml. Editable in-place sin redeploy.
# Resolved a un Path absoluto en __init__.py-load para evitar surprises por
# CWD diferente en tests vs runtime.
_DEFAULT_RULES_PATH: Path = (
    Path(__file__).resolve().parents[3] / "config" / "customer_scoring" / "rules.yaml"
)


class YamlCustomerScoringAdapter:
    """Adapter live: compone features (vault) + rules (YAML) → CustomerScore."""

    def __init__(
        self,
        *,
        metadata_store: FilesystemMetadataStore,
        rules_loader: YamlRulesLoader,
    ) -> None:
        self._metadata_store = metadata_store
        self._loader = rules_loader

    def score_session(
        self,
        session_id: str,
        *,
        now_ms: int,
        medusa_order_totals_cop: dict[str, int] | None = None,
        medusa_order_created_at_ms: dict[str, int] | None = None,
    ) -> CustomerScore:
        # 1. Cargar rules (cache hit si mtime no cambió).
        try:
            doc = self._loader.load()
        except RulesUnavailableError:
            log.exception(
                "customer_scoring: rules.yaml unavailable — devolviendo "
                "score vacío para session_id=%s",
                session_id,
            )
            return _empty_score()

        # 2. Leer metadata del vault. Si la sesión no existe → score vacío.
        metadata = self._metadata_store.read(session_id)
        if not metadata:
            return _empty_score(rules_version=doc.version)

        # 3. Compute features (pure).
        features = compute_customer_features(
            metadata,
            now_ms=now_ms,
            medusa_order_totals_cop=medusa_order_totals_cop,
            medusa_order_created_at_ms=medusa_order_created_at_ms,
        )

        # 4. Apply rules (pure).
        return apply_rules(features, doc)


class NoopCustomerScoringAdapter:
    """Stub usado cuando rules.yaml no existe (e.g. deploy temprano sin config).

    Devuelve un score "Sin datos" para que el frontend renderee MissingData
    en vez de romper.
    """

    def score_session(
        self,
        session_id: str,
        *,
        now_ms: int,
        medusa_order_totals_cop: dict[str, int] | None = None,
        medusa_order_created_at_ms: dict[str, int] | None = None,
    ) -> CustomerScore:
        return _empty_score()


def _empty_score(*, rules_version: int = 0) -> CustomerScore:
    """Score "vacío" para clientes sin actividad / sin config."""
    return CustomerScore(
        tag="Sin datos",
        score_letter="—",
        score_value=0,
        score_reason="No hay historial suficiente para calificar al cliente",
        breakdown=[],
        monetary_cop=0,
        last_purchase_at_ms=None,
        frequency_total=0,
        episodes_total=0,
        rules_version=rules_version,
    )


@lru_cache(maxsize=1)
def get_customer_scoring_port() -> CustomerScoringPort:
    """Singleton del port. Compone YamlRulesLoader + MetadataStore.

    Si el rules.yaml NO existe al boot, devuelve el Noop adapter (logs warning).
    """
    rules_path = _DEFAULT_RULES_PATH
    if not rules_path.exists():
        log.warning(
            "customer_scoring: %s no existe — usando NoopCustomerScoringAdapter "
            "(scores devolverán 'Sin datos')",
            rules_path,
        )
        return NoopCustomerScoringAdapter()

    loader = YamlRulesLoader(rules_path)
    # Validar primer load explícitamente — si falla, caer a Noop con warning.
    try:
        loader.load()
    except RulesUnavailableError as exc:
        log.error(
            "customer_scoring: rules.yaml inválido al boot (%s) — Noop fallback",
            exc,
        )
        return NoopCustomerScoringAdapter()

    metadata_store = FilesystemMetadataStore(WORKSPACE_VAULT_DIR)
    return YamlCustomerScoringAdapter(
        metadata_store=metadata_store, rules_loader=loader
    )


def utc_now_ms() -> int:
    """Helper proceso-wide: tiempo actual en ms epoch. Usado por la API
    endpoint que arma el request al port. Funciones puras NO lo importan
    (lo reciben por DI)."""
    return int(time.time() * 1000)
