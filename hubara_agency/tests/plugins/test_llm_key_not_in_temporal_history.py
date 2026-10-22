"""La llave del LLM no viaja en los payloads de Temporal (auditoría de Temporal, Ola 0).

Todo lo que cruza el borde workflow↔activity queda guardado en la historia de
Temporal Cloud: lo ve cualquiera con lectura del namespace (UI, CLI, historias
exportadas para fixtures). `build_default_llm_config` metía la llave del proxy
(`DEEPSEEK_API_KEY`) en el `LLMConfig` que los bootstraps devuelven dentro del
`SessionInput`, y de ahí viajaba en cada input de `build_prompt`, `llm_chat` y
`record_turn`. La llave vive en el entorno del worker y la lee `llm_chat`.

Dos alcances, la misma regla (ningún campo con nombre de secreto —`api_key`,
`*_api_key`, `access_token`, `*_secret`, `*_password`, `authorization`— con
valor, y el valor de la llave en ningún payload):
  * un turno real de ventas V2 con la sonda del laboratorio (workflow de
    producción sobre el servidor de pruebas, en un proceso con la llave en el
    entorno ANTES de importar la app: una llave leída al importar también se
    ve) y su historia completa;
  * lo que devuelve el bootstrap de cada agente, serializado como lo guarda
    Temporal.
"""
from __future__ import annotations

import base64
import json
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from temporalio.converter import DataConverter
from temporalio.testing import ActivityEnvironment

from tests.plugins.chats.lab.test_lab_sandbox_leaks import _run_probe
from tests.plugins.chats.lab.test_lab_sandbox_materialize import _case

#: La llave del worker en estas pruebas. Nunca es una llave real.
_WORKER_KEY = "sk-guard-not-a-real-key-7f3c9a1d"
#: Nombres de secreto. No `token` a secas: `max_tokens` y `flow_token` no lo son.
_SECRET_FIELD = re.compile(r"(^|_)(api_key|access_token|secret|password)$|^(proxy_)?authorization$")


def _secret_fields(value: Any, path: str = "") -> Iterator[str]:
    """Rutas de los campos con nombre de llave que traen valor."""
    if isinstance(value, dict):
        for key, item in value.items():
            where = f"{path}.{key}" if path else str(key)
            if _SECRET_FIELD.search(str(key).lower().replace("-", "_")) and item not in (None, ""):
                yield where
            yield from _secret_fields(item, where)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from _secret_fields(item, f"{path}[{i}]")


def _leaks(payloads: list[tuple[str, str]]) -> list[str]:
    """Dónde aparece una llave: `<evento>: <campo>`, sin el valor."""
    found: list[str] = []
    for label, text in payloads:
        if _WORKER_KEY in text:
            found.append(f"{label}: el valor de la llave")
        try:
            decoded = json.loads(text)
        except ValueError:
            continue
        found.extend(f"{label}: {where}" for where in _secret_fields(decoded))
    return found


def _payload_texts(value: Any) -> Iterator[str]:
    """Cada payload (`metadata` + `data` en base64) de un evento exportado."""
    if isinstance(value, dict):
        if isinstance(value.get("data"), str) and "metadata" in value:
            yield base64.b64decode(value["data"]).decode("utf-8", "replace")
        for item in value.values():
            yield from _payload_texts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _payload_texts(item)


def _event_label(event: dict[str, Any]) -> str:
    kind = str(event.get("eventType", "?")).removeprefix("EVENT_TYPE_")
    attrs = next((v for k, v in event.items() if k.endswith("EventAttributes") and isinstance(v, dict)), {})
    name = (attrs.get("activityType") or {}).get("name") or (attrs.get("workflowType") or {}).get("name") or ""
    return f"#{event.get('eventId')} {kind} {name}".strip()


def _history_payloads(history: dict[str, Any]) -> list[tuple[str, str]]:
    return [(_event_label(e), text) for e in history.get("events") or [] for text in _payload_texts(e)]


def _carries_llm_config(text: str) -> bool:
    try:
        decoded = json.loads(text)
    except ValueError:
        return False
    return isinstance(decoded, dict) and isinstance(decoded.get("llm"), dict) and "model" in decoded["llm"]


def test_the_detector_flags_every_secret_shaped_field_with_a_value() -> None:
    """Sin esto el guard podría quedar vacío en silencio: caza cada nombre de
    secreto con valor y deja pasar los nombres parecidos que no lo son."""
    payload = {
        "llm": {"api_key": "x", "api_key_env": "DEEPSEEK_API_KEY", "max_tokens": 5},
        "workspace": {"web_search_api_key": ""},
        "headers": {"Authorization": "Bearer y", "X-Api-Key": "z"},
        "meta": [{"access_token": "t", "app_secret": "s", "admin_password": "p", "flow_token": "f"}],
    }

    assert sorted(_secret_fields(payload)) == [
        "headers.Authorization",
        "headers.X-Api-Key",
        "llm.api_key",
        "meta[0].access_token",
        "meta[0].admin_password",
        "meta[0].app_secret",
    ]


def test_a_real_sales_v2_turn_leaves_no_llm_key_in_its_temporal_history(tmp_path: Path) -> None:
    report = _run_probe(
        tmp_path, _case(), arm="B", extra_env={"DEEPSEEK_API_KEY": _WORKER_KEY, "PROBE_HISTORY": "1"}
    )

    assert report["result"]["error"] is None, report["result"]["error"]
    payloads = [p for history in report["histories"] for p in _history_payloads(history)]
    assert any("llm_chat" in label and _carries_llm_config(text) for label, text in payloads), (
        "la historia no tiene el input de `llm_chat`: la prueba no estaría mirando lo que importa"
    )
    assert _leaks(payloads) == []


def _sales_bootstrap() -> tuple[Any, Any]:
    from src.plugins.chats.agent.sales.activities import bootstrap_sales_session_activity
    from src.plugins.chats.agent.sales.contracts import SalesSessionInput

    return bootstrap_sales_session_activity, SalesSessionInput(session_id="wa_573000000002", runtime_workspace_path=None)


def _remarketing_bootstrap() -> tuple[Any, Any]:
    from src.plugins.chats.agent.remarketing.activities import bootstrap_remarketing_session_activity
    from src.plugins.chats.agent.remarketing.contracts import RemarketingSessionInput

    return bootstrap_remarketing_session_activity, RemarketingSessionInput(
        session_id="wa_573000000003", motivo="dudó del precio", runtime_workspace_path=None
    )


def _eta_bootstrap() -> tuple[Any, Any]:
    from src.plugins.eta.agent.eta.activities import bootstrap_eta_session_activity
    from src.plugins.eta.agent.eta.contracts import EtaSessionInput

    return bootstrap_eta_session_activity, EtaSessionInput(
        session_id="wa_573000000004", order_id="order_01GUARD", to_stage="preparing"
    )


_BOOTSTRAPS: dict[str, Callable[[], tuple[Any, Any]]] = {
    "sales": _sales_bootstrap,
    "remarketing": _remarketing_bootstrap,
    "eta": _eta_bootstrap,
}


@pytest.mark.parametrize("agent", sorted(_BOOTSTRAPS))
async def test_the_session_bootstrap_returns_no_llm_key(agent: str, monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", _WORKER_KEY)
    bootstrap, start = _BOOTSTRAPS[agent]()

    session = await ActivityEnvironment().run(bootstrap, start)

    stored = DataConverter.default.payload_converter.to_payloads([session])
    payloads = [(f"{agent}: resultado del bootstrap", p.data.decode("utf-8")) for p in stored]
    assert _carries_llm_config(payloads[0][1])
    assert _leaks(payloads) == []
