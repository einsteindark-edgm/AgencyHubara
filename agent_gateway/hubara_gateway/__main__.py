"""`python -m hubara_gateway` — arranca el gateway con la config del entorno."""

import logging
import os
import sys

import httpx
import uvicorn
from starlette.applications import Starlette

from hubara_gateway.app import create_app
from hubara_gateway.auth import CognitoTokenVerifier, JwksKeyResolver
from hubara_gateway.backend import HttpBackend, M2MTokenProvider
from hubara_gateway.config import ConfigError, GatewayConfig


def build_app(config: GatewayConfig) -> Starlette:
    """Ensambla la app. Sin I/O al construir: JWKS y token M2M se piden al primer uso."""
    timeout = httpx.Timeout(config.backend_timeout_s, connect=5.0)
    tokens = M2MTokenProvider(
        token_url=config.m2m_token_url,
        client_id=config.m2m_client_id,
        client_secret=config.m2m_client_secret,
        scope=config.m2m_scope,
        http=httpx.AsyncClient(timeout=timeout),
    )
    backend = HttpBackend(
        base_url=config.backend_url,
        tokens=tokens,
        # Sin redirects: un 3xx no debe llevar el token M2M a otro host.
        http=httpx.AsyncClient(timeout=timeout, follow_redirects=False),
        max_response_bytes=config.backend_max_response_bytes,
    )
    verifier = CognitoTokenVerifier(config.cognito, key_resolver=JwksKeyResolver(config.cognito.jwks_url))
    return create_app(
        cognito=config.cognito,
        verifier=verifier,
        backend=backend,
        allowed_origins=config.allowed_origins,
        allowed_hosts=config.allowed_hosts,
        limits=config.limits,
    )


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    # httpx loguea URLs completas (con ids/queries) en INFO: solo warnings.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        config = GatewayConfig.from_env(os.environ)
    except ConfigError as exc:
        logging.getLogger("hubara_gateway").error("config error: %s", exc)
        raise SystemExit(2) from exc
    uvicorn.run(
        build_app(config),
        host=config.bind,
        port=config.port,
        access_log=False,  # el log propio (tool + status) no lleva tokens ni PII
        proxy_headers=True,
        server_header=False,
    )


if __name__ == "__main__":
    main()
