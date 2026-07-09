"""Token store del OAuth de Meta — port + fake in-memory + vendor real SSM."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class MetaToken:
    """Token OAuth de Meta + metadata mínima de la cuenta. Frozen + JSON-safe (R-JSON)."""

    access_token: str
    expires_at: int | None
    scopes: tuple[str, ...]
    account_id: str | None
    account_name: str | None


@runtime_checkable
class MetaTokenStorePort(Protocol):
    def save(self, token: MetaToken) -> None: ...
    def load(self) -> MetaToken | None: ...
    def clear(self) -> None: ...


class InMemoryTokenStore:
    """Fake in-memory del `MetaTokenStorePort` para tests. Un solo token (single-tenant)."""

    def __init__(self) -> None:
        self._token: MetaToken | None = None

    def save(self, token: MetaToken) -> None:
        self._token = token

    def load(self) -> MetaToken | None:
        return self._token

    def clear(self) -> None:
        self._token = None


class SsmTokenStore:
    """Vendor real: persiste el `MetaToken` como SecureString en SSM Parameter Store.

    Key convención `/hubara/<tenant>/meta/oauth` (single-tenant: tenant='hubara').
    `boto3` se importa perezoso (gate de lazy surface). `client_factory` se inyecta
    en tests; en prod es `None` → boto3 real. El token NUNCA se loguea.
    """

    def __init__(self, parameter_name: str, client_factory=None, region: str | None = None) -> None:
        self._name = parameter_name
        self._region = region
        self._factory = client_factory

    def _client(self):  # noqa: ANN202
        if self._factory is not None:
            return self._factory()
        import boto3

        return boto3.client("ssm", region_name=self._region)

    def save(self, token: MetaToken) -> None:
        import json

        value = json.dumps(
            {
                "access_token": token.access_token,
                "expires_at": token.expires_at,
                "scopes": list(token.scopes),
                "account_id": token.account_id,
                "account_name": token.account_name,
            }
        )
        self._client().put_parameter(
            Name=self._name, Value=value, Type="SecureString", Overwrite=True
        )

    def load(self) -> MetaToken | None:
        import json

        client = self._client()
        try:
            resp = client.get_parameter(Name=self._name, WithDecryption=True)
        except client.exceptions.ParameterNotFound:
            return None
        raw = json.loads(resp["Parameter"]["Value"])
        return MetaToken(
            access_token=raw["access_token"],
            expires_at=raw["expires_at"],
            scopes=tuple(raw["scopes"]),
            account_id=raw["account_id"],
            account_name=raw["account_name"],
        )

    def clear(self) -> None:
        self._client().delete_parameter(Name=self._name)
