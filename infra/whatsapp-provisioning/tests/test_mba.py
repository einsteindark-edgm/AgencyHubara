"""Meta Business Agent (D3.1) en el CLI de provisioning: qué exige Meta antes de
onboardear un número y que el onboarding sea idempotente. Solo stdlib + pytest;
la red se reemplaza por grabadores (el CLI no toca SSM ni Meta acá)."""
from __future__ import annotations

import importlib.util
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "whatsapp_provision.py"
_spec = importlib.util.spec_from_file_location("whatsapp_provision", _SCRIPT)
wp = importlib.util.module_from_spec(_spec)
sys.modules["whatsapp_provision"] = wp
_spec.loader.exec_module(wp)

CFG = {
    "TENANT": "hubara", "APP_ID": "3662", "APP_SECRET": "sec", "WABA_ID": "1763",
    "SYSTEM_USER_TOKEN": "EAA-token", "PHONE_NUMBER_ID": "1234", "CATALOG_ID": "868",
    "VERIFY_TOKEN": "vt", "CALLBACK_URL": "https://x/api/chats/webhook",
}
READY = {
    "scopes": ["whatsapp_business_messaging", "whatsapp_business_management", "catalog_management"],
    "subscribed": True,
    "webhook_fields": ["messages", "standby", "messaging_handovers", "message_template_status_update"],
    "eligible": True,
    "agents": [],
}


def _codes(state: dict) -> dict[str, bool]:
    return {code: ok for code, ok, _ in wp.mba_readiness(state)}


def test_webhook_subscription_carries_the_three_fields_mba_requires() -> None:
    assert {"messages", "standby", "messaging_handovers"} <= set(wp.WEBHOOK_FIELDS.split(","))
    assert "message_template_status_update" in wp.WEBHOOK_FIELDS.split(",")  # lo que ya usaba chats sigue


def test_readiness_names_each_precondition_meta_documents() -> None:
    assert _codes(READY) == {"token_scopes": True, "app_subscribed": True, "webhook_fields": True, "eligible": True}
    assert _codes({**READY, "scopes": ["whatsapp_business_messaging"]})["token_scopes"] is False
    assert _codes({**READY, "subscribed": False})["app_subscribed"] is False
    assert _codes({**READY, "webhook_fields": ["messages"]})["webhook_fields"] is False
    assert _codes({**READY, "eligible": False})["eligible"] is False
    assert _codes({**READY, "eligible": None})["eligible"] is False  # Meta no contestó = no listo
    detail = dict((c, d) for c, _, d in wp.mba_readiness({**READY, "webhook_fields": ["messages"]}))["webhook_fields"]
    assert "standby" in detail and "messaging_handovers" in detail


def test_onboard_posts_once_and_is_a_noop_when_the_number_already_has_an_agent(monkeypatch) -> None:
    calls: list[tuple] = []

    def fake_mba_api(method, entity_id, resource, token, body=None):
        calls.append((method, entity_id, resource, token, body))
        return 201, {"agent_id": "ag-1"}

    monkeypatch.setattr(wp, "_mba_api", fake_mba_api)
    assert wp.step_mba_onboard(CFG, "1234", READY) == "ag-1"
    assert calls == [("POST", "1234", "agent_onboarding", "EAA-token", {})]

    calls.clear()
    already = {**READY, "agents": [{"agent_id": "ag-0", "rollout": {"enabled": False}}]}
    assert wp.step_mba_onboard(CFG, "1234", already) == "ag-0"
    assert calls == []


def test_onboard_refuses_when_a_precondition_fails(monkeypatch) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(wp, "_mba_api", lambda *a, **k: calls.append(a) or (201, {"agent_id": "x"}))
    assert wp.step_mba_onboard(CFG, "1234", {**READY, "eligible": False}) is None
    assert wp.step_mba_onboard(CFG, "1234", {**READY, "scopes": []}) is None
    assert calls == []


def test_onboard_reports_metas_rejection_instead_of_inventing_an_agent(monkeypatch) -> None:
    monkeypatch.setattr(wp, "_mba_api", lambda *a, **k: (403, {"title": "Forbidden", "detail": "terms not accepted"}))
    out = io.StringIO()
    with redirect_stdout(out):
        assert wp.step_mba_onboard(CFG, "1234", READY) is None
    assert "terms not accepted" in out.getvalue()


def test_mba_api_targets_the_business_agent_host_with_version_header(monkeypatch) -> None:
    seen = {}

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"is_eligible": true}'

    def fake_urlopen(req, timeout=0):
        seen["url"] = req.full_url
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        seen["method"] = req.get_method()
        return _Resp()

    monkeypatch.setattr(wp.urllib.request, "urlopen", fake_urlopen)
    st, body = wp._mba_api("GET", "1234", "agent_eligibility", "EAA-token")
    assert (st, body) == (200, {"is_eligible": True})
    assert seen["url"] == "https://api.facebook.com/1234/agent_eligibility"
    assert seen["headers"]["x-api-version"] == "2.0.0"
    assert seen["headers"]["authorization"] == "Bearer EAA-token"
    assert seen["method"] == "GET"


def test_ssm_block_names_the_mba_secrets_and_the_entity_id(monkeypatch) -> None:
    monkeypatch.setattr(wp, "actual_state", lambda cfg: {"numbers": [{"id": "1234"}], "subscribed_app_ids": [], "token": {}})
    monkeypatch.setattr(wp, "find_number", lambda s, cfg: {"id": "1234"})
    monkeypatch.setattr(wp, "actual_capi_dataset", lambda cfg: "ds-1")
    monkeypatch.setattr(wp, "actual_flows", lambda cfg: [])
    out = io.StringIO()
    with redirect_stdout(out):
        wp.cmd_ssm_block(CFG, None)
    text = out.getvalue()
    assert "WHATSAPP_APP_ID=3662" in text
    assert "META_MBA_TOKEN=<= META_SYSTEM_USER_TOKEN>" in text
    assert "WHATSAPP_PHONE_NUMBER_ID=1234" in text


def test_config_ignores_inline_comments(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("META_SYSTEM_USER_TOKEN", "EAA")
    f = tmp_path / "t.env"
    f.write_text('WABA_ID=1763803271643573             # WABA "Hubara"\nPHONE_NUMBER_ID=1234   # id\nDISPLAY_NAME=Hubara\n')
    cfg = wp.load_config(str(f))
    assert (cfg["WABA_ID"], cfg["PHONE_NUMBER_ID"], cfg["DISPLAY_NAME"]) == ("1763803271643573", "1234", "Hubara")


def test_webhook_step_refuses_to_repoint_a_live_callback(monkeypatch) -> None:
    posts: list[tuple] = []
    monkeypatch.setattr(wp, "actual_webhook_subscription",
                        lambda cfg: {"callback_url": "https://x/api/webhook", "fields": ["messages"], "active": True})
    monkeypatch.setattr(wp, "_api", lambda *a, **k: posts.append((a, k)) or (200, {"success": True}))
    cfg = {**CFG, "CALLBACK_URL": "https://x/api/chats/webhook"}  # config desalineada = 404 en prod
    assert wp.step_webhook(cfg) is False
    assert posts == []
    ok = wp.step_webhook({**CFG, "CALLBACK_URL": "https://x/api/webhook"})
    assert ok is True and posts[0][1]["fields"] == wp.WEBHOOK_FIELDS


def test_onboarding_locks_the_audience_to_the_closed_list_and_is_idempotent(monkeypatch) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(wp, "_mba_api", lambda *a, **k: calls.append(a) or (200, {"ai_audience": "ALLOWLISTED_ONLY"}))
    fresh = {"agent_id": "ag-1", "ai_audience": "EVERYONE", "followup": {"enabled": True}, "rollout": {"enabled": False}}
    assert wp.step_mba_lock_audience(CFG, "1234", fresh) is True
    assert calls == [("PUT", "1234", "agent_config/settings?agent_id=ag-1", "EAA-token",
                      {"ai_audience": "ALLOWLISTED_ONLY", "followup": {"enabled": False}})]
    calls.clear()
    locked = {"agent_id": "ag-1", "ai_audience": "ALLOWLISTED_ONLY", "followup": {"enabled": False}}
    assert wp.step_mba_lock_audience(CFG, "1234", locked) is True
    assert calls == []
