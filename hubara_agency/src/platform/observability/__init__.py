"""Observabilidad OTel — HU-002.

Bootstrap único de OpenTelemetry (traces + metrics + LiteLLM GenAI callback).
Punto de entrada: `init_otel(service_name)`, llamado una vez por proceso al
arranque de cada worker (junto a `setup_logging()`).
"""

from src.platform.observability.otel import init_otel, otel_workflow_runner

__all__ = ["init_otel", "otel_workflow_runner"]
