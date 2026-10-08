"""Contrato del puerto de avisos push (`PushPort`): el MISMO para el falso y para FCM.

FCM corre contra un Google simulado (`httpx.MockTransport`): el endpoint de
tokens de OAuth y `messages:send` de la API HTTP v1. Sin red ni credenciales.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from urllib.parse import parse_qs

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from src.platform.push.adapters.fake import FakePushAdapter
from src.platform.push.adapters.fcm_v1 import FCM_SCOPE, FcmV1PushAdapter, android_client_options, service_account
from src.platform.push.adapters.null import NullPushAdapter
from src.platform.push.ports import FirebaseClientOptions, PushMessage, PushOutcome, PushPort

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()

SERVICE_ACCOUNT = {
    "type": "service_account",
    "project_id": "proyecto-prueba",
    "private_key_id": "llave-1",
    "private_key": _PEM,
    "client_email": "avisos@proyecto-prueba.iam.gserviceaccount.com",
    "token_uri": "https://oauth2.googleapis.com/token",
}
GOOGLE_SERVICES = {
    "project_info": {"project_number": "000111", "project_id": "proyecto-prueba"},
    "client": [
        {
            "client_info": {
                "mobilesdk_app_id": "1:000111:android:otra",
                "android_client_info": {"package_name": "com.otra.app"},
            },
            "api_key": [{"current_key": "llave-de-otra-app"}],
        },
        {
            "client_info": {
                "mobilesdk_app_id": "1:000111:android:abc",
                "android_client_info": {"package_name": "com.acktos.operator"},
            },
            "api_key": [{"current_key": "llave-prueba"}],
        },
    ],
}
OPTIONS = FirebaseClientOptions(
    project_id="proyecto-prueba", application_id="1:000111:android:abc", api_key="llave-prueba", gcm_sender_id="000111"
)
LIVE, DEAD = "token-vivo", "token-muerto"
SYNC = PushMessage(data={"type": "sync", "reason": "fire"}, urgent=True, collapse_key="fires", ttl_s=600)


def _fcm_error(status: int, code: str, message: str = "error") -> httpx.Response:
    return httpx.Response(status, json={"error": {
        "code": status, "message": message, "status": code,
        "details": [{"@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError", "errorCode": code}],
    }})


class FakeGoogle:
    """El lado de Google: emite tokens de acceso y responde `messages:send`."""

    def __init__(self, send: Callable[[dict], httpx.Response] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.tokens_issued = 0
        self._send = send or self._default_send

    @staticmethod
    def _default_send(message: dict) -> httpx.Response:
        if message["token"] == DEAD:
            return _fcm_error(404, "UNREGISTERED", "Requested entity was not found.")
        return httpx.Response(200, json={"name": "projects/proyecto-prueba/messages/1"})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if str(request.url) == SERVICE_ACCOUNT["token_uri"]:
            self.tokens_issued += 1
            return httpx.Response(200, json={"access_token": f"acceso-{self.tokens_issued}", "expires_in": 3599})
        return self._send(json.loads(request.content)["message"])

    def sends(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith("/messages:send")]


def _fcm(google: FakeGoogle | None = None) -> FcmV1PushAdapter:
    return FcmV1PushAdapter(
        account=service_account(json.dumps(SERVICE_ACCOUNT)),
        client=OPTIONS,
        transport=httpx.MockTransport(google or FakeGoogle()),
    )


ADAPTERS: dict[str, Callable[[], PushPort]] = {
    "fake": lambda: FakePushAdapter(options=OPTIONS, dead_tokens={DEAD}),
    "fcm_v1": lambda: _fcm(),
}


# ── El contrato: lo que vale para TODOS los adaptadores ─────────────────────


@pytest.mark.parametrize("name", sorted(ADAPTERS))
async def test_a_live_token_gets_the_push(name: str) -> None:
    adapter = ADAPTERS[name]()
    assert adapter.configured
    assert await adapter.send(LIVE, SYNC) == PushOutcome.SENT


@pytest.mark.parametrize("name", sorted(ADAPTERS))
async def test_a_token_that_no_longer_exists_says_so_to_be_pruned(name: str) -> None:
    assert await ADAPTERS[name]().send(DEAD, SYNC) == PushOutcome.UNREGISTERED


@pytest.mark.parametrize("name", sorted(ADAPTERS))
async def test_gives_the_phone_the_firebase_options_of_its_app(name: str) -> None:
    assert ADAPTERS[name]().client_options() == OPTIONS


async def test_null_adapter_sends_nothing_and_gives_no_options() -> None:
    null = NullPushAdapter()
    assert not null.configured
    assert null.client_options() is None
    assert await null.send(LIVE, SYNC) == PushOutcome.NOT_CONFIGURED


async def test_the_fake_records_what_it_would_send_and_can_fail() -> None:
    fake = FakePushAdapter()
    await fake.send(LIVE, SYNC)
    assert fake.sent == [(LIVE, SYNC)]
    assert await FakePushAdapter(fail=True).send(LIVE, SYNC) == PushOutcome.FAILED


async def test_the_fake_can_leave_each_push_in_a_file(tmp_path) -> None:
    outbox = tmp_path / "pushes.jsonl"
    await FakePushAdapter(outbox=outbox).send(LIVE, SYNC)
    line = json.loads(outbox.read_text(encoding="utf-8"))
    assert line["token"] == LIVE
    assert line["data"] == {"type": "sync", "reason": "fire"}
    assert line["urgent"] is True


# ── FCM HTTP v1: la forma de lo que sale y cómo se lee lo que vuelve ────────


async def test_fcm_sends_a_data_only_message_with_android_priority_and_collapse_key() -> None:
    google = FakeGoogle()
    await _fcm(google).send(LIVE, SYNC)
    await _fcm(google).send(LIVE, PushMessage(data={"type": "sync", "reason": "hot"}, collapse_key="hot"))

    urgent, normal = google.sends()
    assert str(urgent.url) == "https://fcm.googleapis.com/v1/projects/proyecto-prueba/messages:send"
    assert urgent.headers["authorization"] == "Bearer acceso-1"
    body = json.loads(urgent.content)["message"]
    # Solo datos: sin `notification` (la app arma el aviso con lo que lee de su backend, con sesión).
    assert body == {
        "token": LIVE,
        "data": {"type": "sync", "reason": "fire"},
        "android": {"priority": "HIGH", "ttl": "600s", "collapse_key": "fires"},
    }
    assert json.loads(normal.content)["message"]["android"]["priority"] == "NORMAL"


async def test_fcm_signs_the_service_account_assertion_and_reuses_the_access_token() -> None:
    google = FakeGoogle()
    adapter = _fcm(google)
    await adapter.send(LIVE, SYNC)
    await adapter.send(LIVE, SYNC)

    assert google.tokens_issued == 1
    form = parse_qs(google.requests[0].content.decode())
    assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
    claims = jwt.decode(
        form["assertion"][0], _KEY.public_key(), algorithms=["RS256"], audience=SERVICE_ACCOUNT["token_uri"]
    )
    assert claims["iss"] == SERVICE_ACCOUNT["client_email"]
    assert claims["scope"] == FCM_SCOPE
    assert claims["exp"] - claims["iat"] <= 3600


async def test_fcm_renews_an_expired_access_token_once() -> None:
    calls = {"n": 0}

    def send(message: dict) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, json={"error": {"code": 401, "status": "UNAUTHENTICATED"}}) if calls["n"] == 1 \
            else httpx.Response(200, json={"name": "ok"})

    google = FakeGoogle(send)
    assert await _fcm(google).send(LIVE, SYNC) == PushOutcome.SENT
    assert google.tokens_issued == 2


@pytest.mark.parametrize(
    ("response", "outcome"),
    [
        (_fcm_error(404, "UNREGISTERED"), PushOutcome.UNREGISTERED),
        (_fcm_error(400, "INVALID_ARGUMENT", "The registration token is not a valid FCM registration token"),
         PushOutcome.UNREGISTERED),
        (_fcm_error(403, "SENDER_ID_MISMATCH"), PushOutcome.UNREGISTERED),
        # Un error en lo que mandamos NO borra el token del operador.
        (_fcm_error(400, "INVALID_ARGUMENT", "Invalid value at 'message.data'"), PushOutcome.FAILED),
        (_fcm_error(429, "QUOTA_EXCEEDED"), PushOutcome.FAILED),
        (_fcm_error(503, "UNAVAILABLE"), PushOutcome.FAILED),
        (httpx.Response(500, text="<html>error</html>"), PushOutcome.FAILED),
    ],
)
async def test_fcm_reads_what_google_answers(response: httpx.Response, outcome: PushOutcome) -> None:
    assert await _fcm(FakeGoogle(lambda _m: response)).send(LIVE, SYNC) == outcome


async def test_fcm_never_raises_when_google_does_not_answer() -> None:
    def timeout(_message: dict) -> httpx.Response:
        raise httpx.ReadTimeout("sin respuesta")

    assert await _fcm(FakeGoogle(timeout)).send(LIVE, SYNC) == PushOutcome.FAILED


async def test_fcm_without_an_access_token_fails_without_raising() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    adapter = FcmV1PushAdapter(
        account=service_account(json.dumps(SERVICE_ACCOUNT)), client=OPTIONS, transport=httpx.MockTransport(handler)
    )
    assert await adapter.send(LIVE, SYNC) == PushOutcome.FAILED


# ── Lo que el operador carga en SSM ─────────────────────────────────────────


def test_the_phone_options_come_from_the_client_of_this_app_in_google_services() -> None:
    assert android_client_options(json.dumps(GOOGLE_SERVICES), package="com.acktos.operator") == OPTIONS
    assert android_client_options(json.dumps(GOOGLE_SERVICES), package="com.no.existe") is None


@pytest.mark.parametrize("raw", [None, "", "PLACEHOLDER_set_out_of_band", "{no es json", json.dumps({"type": "otro"})])
def test_a_missing_or_placeholder_credential_is_not_a_credential(raw: str | None) -> None:
    assert service_account(raw) is None
    assert android_client_options(raw, package="com.acktos.operator") is None
