"""Motor de scoring de clientes para el dashboard "Historial cliente".

Diseño:
  * Determinístico, hot-reloadable, auditable.
  * `features.py` computa hechos RFM puros del cliente (recency, frequency,
    monetary, lost_ratio, msgs_avg, episodes_total, first_seen_days_ago).
  * `rules.py` aplica un YAML de reglas a esos features → `CustomerScore`
    (tag + letter A/B/C/D + score 0-100 + reason + breakdown).
  * `loader.py` lee `config/customer_scoring/rules.yaml` con hot-reload por
    mtime y validación al cargar (fallback al doc previo si el nuevo es
    inválido).
  * `port.py` define el `CustomerScoringPort` (Protocol) — la fachada que
    consumen `plugins/orders/api/`.

Por qué NO LLM para el score: determinístico, auditable, cero costo, hot-reload
sin redeploy son los criterios del operador. El LLM se reserva para el botón
on-demand "Resumir cliente" (texto narrativo), no para el número.

DEHA: 100% puro en `features.py` + `rules.py`. El único I/O vive en `loader.py`
(read filesystem) y en el aggregator (vault metadata + Medusa orders).
"""
from src.platform.customer_scoring.port import (
    CustomerFeatures,
    CustomerScore,
    CustomerScoringPort,
    ScoreBreakdownItem,
)
from src.platform.customer_scoring.features import compute_customer_features
from src.platform.customer_scoring.rules import (
    InvalidRulesDocError,
    RulesDoc,
    apply_rules,
    parse_rules_doc,
)
from src.platform.customer_scoring.loader import (
    YamlRulesLoader,
    RulesUnavailableError,
)

__all__ = [
    "CustomerFeatures",
    "CustomerScore",
    "CustomerScoringPort",
    "InvalidRulesDocError",
    "RulesDoc",
    "RulesUnavailableError",
    "ScoreBreakdownItem",
    "YamlRulesLoader",
    "apply_rules",
    "compute_customer_features",
    "parse_rules_doc",
]
