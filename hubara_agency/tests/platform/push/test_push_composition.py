"""Qué adaptador de avisos push arma el proceso según su entorno (`PUSH_PROVIDER` + las dos llaves de SSM)."""
from __future__ import annotations

import json

import pytest

from src.platform.push.adapters.fake import FakePushAdapter
from src.platform.push.adapters.fcm_v1 import FcmV1PushAdapter
from src.platform.push.adapters.null import NullPushAdapter
from src.platform.push.composition import get_push_port
from src.platform.push.ports import PushMessage
from tests.platform.push.test_push_contract import GOOGLE_SERVICES, OPTIONS, SERVICE_ACCOUNT

_ENV = ("PUSH_PROVIDER", "PUSH_FAKE_OUTBOX", "FCM_SERVICE_ACCOUNT_JSON", "FIREBASE_ANDROID_CONFIG_JSON")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for var in _ENV:
        monkeypatch.delenv(var, raising=False)
    get_push_port.cache_clear()
    yield
    get_push_port.cache_clear()


def _real_keys(monkeypatch, *, google_services: dict = GOOGLE_SERVICES) -> None:
    monkeypatch.setenv("FCM_SERVICE_ACCOUNT_JSON", json.dumps(SERVICE_ACCOUNT))
    monkeypatch.setenv("FIREBASE_ANDROID_CONFIG_JSON", json.dumps(google_services))


def test_without_keys_there_are_no_pushes() -> None:
    assert isinstance(get_push_port(), NullPushAdapter)


def test_with_the_terraform_placeholders_there_are_no_pushes(monkeypatch) -> None:
    monkeypatch.setenv("FCM_SERVICE_ACCOUNT_JSON", "PLACEHOLDER_set_out_of_band")
    monkeypatch.setenv("FIREBASE_ANDROID_CONFIG_JSON", "PLACEHOLDER_set_out_of_band")
    assert isinstance(get_push_port(), NullPushAdapter)


def test_with_both_keys_it_sends_through_fcm_and_gives_the_phone_its_options(monkeypatch) -> None:
    _real_keys(monkeypatch)
    port = get_push_port()
    assert isinstance(port, FcmV1PushAdapter)
    assert port.configured
    assert port.client_options() == OPTIONS


def test_without_the_android_app_the_backend_still_sends_but_no_phone_can_sign_up(monkeypatch) -> None:
    _real_keys(monkeypatch, google_services={"project_info": {}, "client": []})
    port = get_push_port()
    assert port.configured
    assert port.client_options() is None


def test_keys_from_two_different_firebase_projects_are_not_used(monkeypatch) -> None:
    # Google rechazaría cada aviso (SENDER_ID_MISMATCH) y borraríamos los tokens de todos.
    other = json.loads(json.dumps(GOOGLE_SERVICES))
    other["project_info"]["project_id"] = "otro-proyecto"
    _real_keys(monkeypatch, google_services=other)
    assert isinstance(get_push_port(), NullPushAdapter)


def test_the_switch_forces_the_fake_or_turns_pushes_off(monkeypatch) -> None:
    _real_keys(monkeypatch)
    monkeypatch.setenv("PUSH_PROVIDER", "off")
    assert isinstance(get_push_port(), NullPushAdapter)

    get_push_port.cache_clear()
    monkeypatch.setenv("PUSH_PROVIDER", "fake")
    assert isinstance(get_push_port(), FakePushAdapter)


async def test_the_fake_of_the_emulator_backend_writes_each_push_to_a_file(monkeypatch, tmp_path) -> None:
    outbox = tmp_path / "pushes.jsonl"
    monkeypatch.setenv("PUSH_PROVIDER", "fake")
    monkeypatch.setenv("PUSH_FAKE_OUTBOX", str(outbox))

    await get_push_port().send("token-e2e", PushMessage(data={"type": "sync"}))

    assert json.loads(outbox.read_text(encoding="utf-8"))["token"] == "token-e2e"
