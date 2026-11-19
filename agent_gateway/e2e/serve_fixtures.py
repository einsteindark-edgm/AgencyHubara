"""Gateway REAL (misma app) con un backend que reproduce los fixtures de prod y un emisor
JWT local. Para el e2e con el cliente del plugin sin depender de Cognito ni del backend.

    uv --directory agent_gateway run python e2e/serve_fixtures.py <tokens.json>

Escribe en <tokens.json> un token normal (`user`) y uno admin+pii (`admin`), ambos con
`aud` = http://localhost:8765/mcp. Nunca lee `raw/` de los fixtures.
"""

import copy
import json
import sys
from pathlib import Path
from typing import Any

import uvicorn

from hubara_gateway.app import GatewayLimits, create_app
from hubara_gateway.auth import CognitoTokenVerifier
from hubara_gateway.backend import BackendResponse
from tests.auth_helpers import PII, READ, Issuer, StaticKeys, settings
from tests.test_contract_fixtures import FIXTURES


class FixtureBackend:
    def __init__(self, root: Path) -> None:
        manifest = json.loads((root / "manifest.json").read_text())
        self.bodies = {
            (e["path"], tuple(sorted(e["query"].items()))): json.loads((root / e["file"]).read_text())["body"]
            for e in manifest["entries"]
        }

    async def get(self, path: str, query: dict[str, str], *, on_behalf_of: str | None = None) -> BackendResponse:
        body = self.bodies.get((path, tuple(sorted(query.items()))))
        if body is None:
            return BackendResponse(404, {"detail": "fixture_missing"})
        return BackendResponse(200, copy.deepcopy(body))


def main(tokens_path: str) -> None:
    issuer, cfg = Issuer(), settings()
    tokens: dict[str, Any] = {
        "user": issuer.token(),
        "admin": issuer.token(scope=f"{READ} {PII}", **{"cognito:groups": ["admin"]}),
    }
    Path(tokens_path).write_text(json.dumps(tokens))
    app = create_app(
        cognito=cfg,
        verifier=CognitoTokenVerifier(cfg, key_resolver=StaticKeys(issuer)),
        backend=FixtureBackend(FIXTURES),
        allowed_origins=(),
        allowed_hosts=("localhost:8765", "127.0.0.1:8765"),
        limits=GatewayLimits(),
    )
    uvicorn.run(app, host="127.0.0.1", port=8765, access_log=False)


if __name__ == "__main__":
    main(sys.argv[1])
