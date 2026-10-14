"""El worker de reseñas llama a chats por HTTP: necesita la URL de la API.

Revisión 2026-10-13: con 1–3★ el worker pasa la conversación al equipo por
`session-actions@v1` (castkit → API, identidad de servicio). Ni SSM ni
render-env-from-ssm.sh dan `HUBARA_API_BASE_URL`: en producción el service se
llama `api`, en el compose local `hubara-api` (PM-003, mismo patrón que eta,
order_sentinel y post_sale_return). Sin la variable el cast falla y el cliente
se queda con la promesa «alguien del equipo te escribe hoy».
"""
from __future__ import annotations

from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[3]
_PROD = _REPO / "infra" / "compose" / "docker-compose.prod.yml"
_MANIFEST = _REPO / "frontend_dashboard" / "src" / "plugins" / "reviews" / "plugin.yaml"
_MODULE = "src.plugins.reviews.workers.requests"


def test_prod_compose_gives_the_reviews_worker_the_api_service_name() -> None:
    services = yaml.safe_load(_PROD.read_text(encoding="utf-8"))["services"]
    [service] = [s for s in services.values() if isinstance(s, dict) and _MODULE in (s.get("command") or [])]
    assert (service.get("environment") or {}).get("HUBARA_API_BASE_URL") == "http://api:8000"


def test_local_compose_env_uses_the_local_api_service_name() -> None:
    manifest = yaml.safe_load(_MANIFEST.read_text(encoding="utf-8"))
    [worker] = manifest["agent"]["workers"]
    assert worker["compose"]["env"]["HUBARA_API_BASE_URL"] == "http://hubara-api:8000"
