"""Firebase Cloud Messaging, API HTTP v1 (la única que Google mantiene desde 2024).

Credenciales (SSM → `.env`, el operador las carga fuera de banda; guía:
`docs/mobile-native/activar-avisos-push.html`):

* `FCM_SERVICE_ACCOUNT_JSON`: la llave de la cuenta de servicio de Firebase
  (Configuración del proyecto → Cuentas de servicio → Generar clave privada),
  en UNA línea. Con ella se firma un JWT (RS256) que se cambia por un token de
  acceso de OAuth de una hora (`FCM_SCOPE`), sin la librería de Google.
* `FIREBASE_ANDROID_CONFIG_JSON`: el `google-services.json` de la app Android.
  De ahí salen las opciones con las que el TELÉFONO arranca Firebase.

HTTP honesto (L-1): `send` nunca lanza. 404 UNREGISTERED, 403
SENDER_ID_MISMATCH y 400 por token inválido = el token no sirve (se borra);
todo lo demás (cuota, 5xx, sin respuesta, un error en lo que mandamos) =
`FAILED`, y el token queda.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx
import jwt
import structlog

from src.platform.config import is_placeholder
from src.platform.push.ports import FirebaseClientOptions, PushMessage, PushOutcome

logger = structlog.get_logger()

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
FCM_SEND_URL = "https://fcm.googleapis.com/v1/projects/{project}/messages:send"
DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"
#: Se pide un token de acceso nuevo un poco antes de que venza.
_TOKEN_MARGIN_S = 120
_DEAD_TOKEN_CODES = frozenset({"UNREGISTERED", "SENDER_ID_MISMATCH"})


def _load_json(raw: str | None) -> dict[str, Any] | None:
    if raw is None or is_placeholder(raw):
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def service_account(raw: str | None) -> dict[str, Any] | None:
    """La cuenta de servicio de `FCM_SERVICE_ACCOUNT_JSON`, o None si falta, es el placeholder o no es una."""
    data = _load_json(raw)
    if data is None or data.get("type") != "service_account":
        return None
    if not all(isinstance(data.get(k), str) and data[k] for k in ("project_id", "private_key", "client_email")):
        return None
    return data


def android_client_options(raw: str | None, *, package: str) -> FirebaseClientOptions | None:
    """Las opciones del cliente Android `package` de un `google-services.json`, o None."""
    data = _load_json(raw)
    if data is None:
        return None
    project = data.get("project_info") if isinstance(data.get("project_info"), dict) else {}
    for client in data.get("client") or []:
        if not isinstance(client, dict):
            continue
        info = client.get("client_info") if isinstance(client.get("client_info"), dict) else {}
        android = info.get("android_client_info") if isinstance(info.get("android_client_info"), dict) else {}
        if android.get("package_name") != package:
            continue
        keys = [k.get("current_key") for k in client.get("api_key") or [] if isinstance(k, dict)]
        values = (project.get("project_id"), info.get("mobilesdk_app_id"), next(iter(keys), None),
                  project.get("project_number"))
        if not all(isinstance(v, str) and v for v in values):
            return None
        project_id, app_id, api_key, sender = values
        return FirebaseClientOptions(project_id=project_id, application_id=app_id, api_key=api_key,
                                     gcm_sender_id=sender)
    return None


def _error_code(response: httpx.Response) -> tuple[str, str]:
    """(errorCode de FCM o status de Google, mensaje) de una respuesta de error."""
    try:
        error = response.json().get("error") or {}
    except ValueError:
        return "", ""
    if not isinstance(error, dict):
        return "", ""
    for detail in error.get("details") or []:
        if isinstance(detail, dict) and detail.get("errorCode"):
            return str(detail["errorCode"]), str(error.get("message") or "")
    return str(error.get("status") or ""), str(error.get("message") or "")


class FcmV1PushAdapter:
    name = "fcm_v1"
    configured = True

    def __init__(
        self,
        *,
        account: Mapping[str, Any],
        client: FirebaseClientOptions | None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = 10.0,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._account = account
        self._client = client
        self._transport = transport
        self._timeout_s = timeout_s
        self._now = now
        self._access: tuple[str, float] | None = None
        self._url = FCM_SEND_URL.format(project=account["project_id"])

    def client_options(self) -> FirebaseClientOptions | None:
        return self._client

    async def send(self, token: str, message: PushMessage) -> PushOutcome:
        android: dict[str, Any] = {"priority": "HIGH" if message.urgent else "NORMAL", "ttl": f"{message.ttl_s}s"}
        if message.collapse_key:
            android["collapse_key"] = message.collapse_key
        body = {"message": {"token": token, "data": {k: str(v) for k, v in message.data.items()}, "android": android}}
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout_s) as http:
                for attempt in range(2):
                    access = await self._access_token(http)
                    if access is None:
                        return PushOutcome.FAILED
                    response = await http.post(self._url, json=body, headers={"Authorization": f"Bearer {access}"})
                    if response.status_code == 401 and attempt == 0:
                        self._access = None  # el token de acceso venció antes de tiempo: uno nuevo, una vez
                        continue
                    return self._outcome(response)
        except httpx.HTTPError as exc:
            logger.warning("push.fcm.unreachable", error=type(exc).__name__)
        return PushOutcome.FAILED

    def _outcome(self, response: httpx.Response) -> PushOutcome:
        if response.status_code == 200:
            return PushOutcome.SENT
        code, text = _error_code(response)
        if code in _DEAD_TOKEN_CODES or (code == "INVALID_ARGUMENT" and "registration token" in text.lower()):
            return PushOutcome.UNREGISTERED
        logger.warning("push.fcm.failed", status=response.status_code, code=code)
        return PushOutcome.FAILED

    async def _access_token(self, http: httpx.AsyncClient) -> str | None:
        now = self._now()
        if self._access is not None and self._access[1] - _TOKEN_MARGIN_S > now:
            return self._access[0]
        token_uri = str(self._account.get("token_uri") or DEFAULT_TOKEN_URI)
        claims = {"iss": self._account["client_email"], "scope": FCM_SCOPE, "aud": token_uri,
                  "iat": int(now), "exp": int(now) + 3600}
        headers = {"kid": self._account["private_key_id"]} if self._account.get("private_key_id") else None
        try:
            assertion = jwt.encode(claims, self._account["private_key"], algorithm="RS256", headers=headers)
        except (ValueError, TypeError, jwt.PyJWTError) as exc:
            logger.warning("push.fcm.bad_private_key", error=type(exc).__name__)
            return None
        response = await http.post(token_uri, data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion,
        })
        if response.status_code != 200:
            logger.warning("push.fcm.no_access_token", status=response.status_code)
            return None
        try:
            payload = response.json()
            access, expires = str(payload["access_token"]), float(payload.get("expires_in") or 3600)
        except (ValueError, KeyError, TypeError):
            return None
        self._access = (access, now + expires)
        return access
