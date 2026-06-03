"""BaggageSpanProcessor — vuelca OTel Baggage a atributos de span (HU-003 A7).

El package oficial ``opentelemetry-processor-baggage`` NO está instalado en el venv,
así que replicamos su comportamiento mínimo: en ``on_start``, leer el baggage del
contexto activo y copiarlo como atributos del span.

**Por qué importa (atribución de costos):** el costo del LLM (``gen_ai.usage.cost`` +
tokens) lo emite OpenLIT en un span que se crea DENTRO de la activity ``llm_chat``.
``run_agent_turn`` setea ``session.id`` / ``episode.id`` / ``whatsapp.number`` como
baggage en el workflow; el ``TracingInterceptor`` de Temporal auto-propaga ese baggage
workflow→activity (CompositePropagator con W3CBaggagePropagator), y este processor lo
copia al span gen_ai. Resultado: ``GROUP BY session.id, episode.id`` sobre el costo
funciona en SigNoz.

Seguro/determinístico: ``on_start`` solo lee baggage y setea atributos — cero I/O.
Con ``OTEL_SDK_DISABLED=true`` el provider no graba y los atributos no se exportan.
"""

from __future__ import annotations

from opentelemetry.baggage import get_all as _baggage_get_all
from opentelemetry.context import Context
from opentelemetry.sdk.trace import Span, SpanProcessor


class BaggageSpanProcessor(SpanProcessor):
    """Copia el baggage del contexto a atributos del span en ``on_start``.

    Por defecto copia TODO el baggage — somos los únicos que lo seteamos, no hay
    propagadores upstream. Si en el futuro entra baggage de terceros, pasar
    ``allowed_keys`` para restringir qué claves se vuelcan al span.
    """

    def __init__(self, allowed_keys: frozenset[str] | None = None) -> None:
        self._allowed_keys = allowed_keys

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        for key, value in _baggage_get_all(parent_context).items():
            if self._allowed_keys is not None and key not in self._allowed_keys:
                continue
            if value is not None:
                span.set_attribute(key, str(value))

    def on_end(self, span: Span) -> None:
        pass

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True
