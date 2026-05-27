"""`CustomerScoringPort` — fachada que el plugin `orders` consume.

DTOs (todos `@dataclass(frozen=True)` JSON-serializable — R-JSON):

  * `CustomerFeatures` — hechos RFM puros del cliente. Computado por
    `features.compute_customer_features(metadata, orders)`.
  * `ScoreBreakdownItem` — un componente del score con su contribución
    en puntos (para el "explain trail" del UI).
  * `CustomerScore` — el resultado: tag + letter + value + reason +
    breakdown + monetary + last_purchase. Es lo que el endpoint devuelve.

`CustomerScoringPort` es la abstracción que la API consume — el adapter
default (`YamlCustomerScoringAdapter`) compone features+rules+loader. Se
puede swappear por un mock en tests sin patches frágiles.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class CustomerFeatures:
    """Hechos RFM crudos del cliente — sin interpretación de negocio.

    Computado a partir del `metadata.json` de la sesión (episodes + legacy
    fallback) cruzado con `OrderDetailDTO` de Medusa para totales monetarios.

    Cero valores significan "no aplicable" SOLO si el cliente no tuvo
    actividad relevante. None se usa cuando el dato es desconocido (e.g.
    `recency_days=None` para cliente nunca compró). Las rules deben tolerar
    None graciosamente.
    """
    # Aggregates sobre todos los episodios del cliente.
    episodes_total: int                # incluye activos + cerrados
    episodes_won: int                  # closing_tag == COMPRA_EXITOSA o order_id truthy
    episodes_lost: int                 # closing_tag == RECHAZO
    episodes_partial: int              # closing_tag == CONFIRMADO_SIN_DATOS
    episodes_timeout: int              # closing_tag == TIMEOUT
    episodes_active: int               # closed_at_ms == None
    # Monetary — suma de totales de orders ganados (en COP major units).
    monetary_cop: int
    # Recency — días desde la última venta ganada.
    recency_days: int | None
    # Frequency — alias de episodes_won (lo expongo separado para legibilidad
    # en las rules YAML).
    frequency_total: int
    # Lost ratio: episodes_lost / (episodes_won + episodes_lost). 0.0 si ambos 0.
    lost_ratio: float
    # Msgs promedio por episodio cerrado (proxy de fricción). None si no hay
    # cerrados o si los episodes no tienen msgs_count_at_*.
    msgs_avg_to_close: float | None
    # Antigüedad del cliente: días desde el primer started_at_ms.
    first_seen_days_ago: int | None
    # Last purchase metadata para el UI directo.
    last_purchase_at_ms: int | None
    last_purchase_order_id: str | None


@dataclass(frozen=True)
class ScoreBreakdownItem:
    """Un componente individual del score, para "explain trail" en el UI.

    El UI puede mostrar tooltip: "Score A porque monetary=40 + frequency=25
    + recency=25 = 90". Cada bin que matcheó contribuye un item.
    """
    feature: str            # nombre de la feature (e.g. "monetary_cop")
    feature_value: float    # valor crudo del feature
    points: int             # puntos otorgados/restados


@dataclass(frozen=True)
class CustomerScore:
    """Resultado del scoring — lo que el endpoint devuelve y el UI consume.

    `rules_version` permite auditar "¿con qué rule set se computó esto?"
    semanas después. Si el operador cambia las rules, podemos comparar
    histórico con la versión correspondiente.
    """
    # Tag categórico (VIP / Recurrente / Nuevo / Frío / Estándar / etc).
    # El YAML define el set posible — frontend lo trata como string libre.
    tag: str
    # Letter grade A/B/C/D (estricto enum).
    score_letter: str
    # Puntos 0-100 (puede ser negativo si rules tienen penalizaciones).
    score_value: int
    # Razón humana (del YAML score_letter[].reason o reason_hints[]).
    score_reason: str
    # Breakdown para tooltip "por qué este score" (lista de bins activados).
    breakdown: list[ScoreBreakdownItem] = field(default_factory=list)
    # Datos directos para los KVs del UI sin rules adicionales.
    monetary_cop: int = 0
    last_purchase_at_ms: int | None = None
    # 5° KV: contexto de conversion rate ("3 compras de 5 episodios" indica
    # buen ratio; "1 compra de 8 episodios" hint de cliente que pregunta mucho
    # y compra poco).
    frequency_total: int = 0     # # de COMPRA_EXITOSA (alias de features.episodes_won)
    episodes_total: int = 0      # # de episodios totales (incluye activos)
    # Version del YAML que computó este score (para auditoría).
    rules_version: int = 0


@runtime_checkable
class CustomerScoringPort(Protocol):
    """Fachada cross-plugin para scoring de cliente.

    La adapter default `YamlCustomerScoringAdapter` (en `composition.py`)
    compone features.py + loader.py + rules.py. El plugin `orders` solo
    depende de este protocol — para swap a un scorer externo (e.g.
    LLM-based experimental, A/B test) no hay que tocar la API.
    """

    def score_session(
        self,
        session_id: str,
        *,
        now_ms: int,
        medusa_order_totals_cop: dict[str, int] | None = None,
        medusa_order_created_at_ms: dict[str, int] | None = None,
    ) -> CustomerScore:
        """Compute el score para un cliente identificado por `session_id`
        (formato `wa_<phone>`).

        Args:
          session_id: `wa_<phone>` — el caller lo resuelve del shipping
            address de la order o del metadata Hubara.
          now_ms: tiempo actual en ms epoch (DI para tests determinísticos).
          medusa_order_totals_cop: opcional. Map `order_id → total_cop`
            (major units). Permite al caller pasar totales ya fetcheados de
            Medusa para evitar round-trip. Si None o key missing, monetary
            queda en 0 para esos orders.
          medusa_order_created_at_ms: opcional. Map `order_id → created_at_ms`.
            Usado para recency_days más precisa que `episodes.closed_at_ms`.

        Returns:
          CustomerScore — siempre devuelve un objeto. Para clientes sin
          actividad (session_id que no existe en el vault), devuelve un
          score "vacío" con tag="Sin datos", letter="—".
        """
        ...
