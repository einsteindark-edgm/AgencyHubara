"""Golden-replay de `order-sentinel` (analyzer, molde window-strategist): lee las
conversaciones escaladas a HUMANO con orden vinculada y emite intents de transición
de estado del pedido. La clasificación la hace el nodo LLM por `ports["llm"]`
(molde ctwa-report.narrate: FixtureLLM en golden, LiteLLMProxy en real); los
GUARDRAILS son deterministas y viven en el nodo plan PURO — este golden los fija:

  1. `to_stage` permitidos SOLO {preparing, ready, shipping, delivered};
     `cancelled` propuesto por el LLM → suppressed `stage_not_allowed`.
  2. Forward-only según el DAG estático new→preparing→ready→shipping→delivered
     partiendo de `current_stage`; ilegal → suppressed `invalid_transition`.
  3. Solo confidence == "high" despacha; medium/low → suppressed `low_confidence`.
  4. Máx 1 intent de transición por order_id → el duplicado queda suppressed
     `duplicate_order_intent`.
  5. confirm_payment solo con payment_confirmed == false; si ya está true →
     suppressed `payment_already_confirmed`. El intent NO lleva `to_stage`.
  6. action "none" → silencio normal (ni dispatch ni suppressed).
  7. JSON malformado del LLM → suppressed `unparseable_llm_output` (no crash).
  8. LLM inalcanzable → entry en `llm_errors` (session_id + mensaje), no crash.
  9. Falta `payload` → ValueError de dominio (MF-7).
 10. `conversations` vacío → listas vacías.

FASE ROJA: `graphs.order_sentinel` NO existe todavía. El import es LAZY (adentro
de `_run`) para que el primer rojo inevitable sea un FAILED con
`ModuleNotFoundError: No module named 'graphs.order_sentinel'` dentro de cada
test (no un error de colección), y los asserts quedan EXACTOS para que, apenas
exista el módulo, el rojo pase a ser de comportamiento (AssertionError con el
diff del output).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sdk.connectorkit.ports import FixtureLLM

GA = Path(__file__).resolve().parents[2]
FIX = GA / "fixtures" / "order_sentinel_snapshot.json"

NOW_MS = 1720500000000


def _run(input: dict, *, ports: dict | None = None, tools: dict | None = None) -> dict:
    """Import lazy del grafo bajo test (ver docstring del módulo: fase roja)."""
    from graphs.order_sentinel import run

    return run(input, ports=ports, tools=tools)


def _payload() -> dict:
    return json.loads(FIX.read_text(encoding="utf-8"))


def _payload_one(
    *,
    session_id: str = "wa_573009998877",
    order_id: str = "order_01SOLO",
    current_stage: str = "preparing",
    payment_confirmed: bool = True,
    text: str = "ya está listo tu pedido",
    who: str = "human_operator",
) -> dict:
    return {
        "schema_version": 1,
        "now_ms": NOW_MS,
        "conversations": [
            {
                "session_id": session_id,
                "order_id": order_id,
                "current_stage": current_stage,
                "payment_confirmed": payment_confirmed,
                "messages": [
                    {"who": who, "text": text, "at_ms": NOW_MS - 10000, "has_media": False},
                ],
            }
        ],
    }


def _router(routes: dict[str, dict]) -> FixtureLLM:
    """FixtureLLM que responde por conversación matcheando texto de sus mensajes en
    el prompt `user`. CONTRATO implícito que este router fija: el prompt del nodo
    LLM DEBE contener el texto crudo de los mensajes de la conversación (si no,
    el LLM no tiene con qué clasificar) — un prompt sin match revienta VISIBLE."""

    def reply(user: str) -> str:
        for needle, verdict in routes.items():
            if needle in user:
                return json.dumps(verdict, ensure_ascii=False)
        raise AssertionError(
            f"order-sentinel: el prompt al LLM no contiene los mensajes de la "
            f"conversación (ningún needle matcheó). Prompt: {user!r}"
        )

    return FixtureLLM(reply)


class _UnreachableLLM:
    """El proxy LiteLLM inalcanzable del run real en AWS (molde L-26, ctwa-report):
    levanta la MISMA excepción que urllib (URLError envolviendo el OSError 99)."""

    def complete(self, *, system: str, user: str, temperature: float = 0.0) -> str:
        import urllib.error

        raise urllib.error.URLError(OSError(99, "Cannot assign requested address"))


class _RecordingLLM:
    """Wrapper observador del port: cuenta llamadas y captura kwargs — para fijar
    'UNA llamada por conversación' y temperature=0.0 en el SEAM del port (contrato
    observable, no implementación interna)."""

    def __init__(self, inner: FixtureLLM) -> None:
        self._inner = inner
        self.calls: list[dict] = []

    def complete(self, *, system: str, user: str, temperature: float = 0.0) -> str:
        self.calls.append({"system": system, "user": user, "temperature": temperature})
        return self._inner.complete(system=system, user=user, temperature=temperature)


#: Los veredictos del LLM para el snapshot golden, ruteados por texto del mensaje.
_GOLDEN_ROUTES = {
    # wa_573001112233 — operador confirmó el pago (payment_confirmed=false) → dispatch
    "ya consigné": {
        "action": "confirm_payment",
        "evidence": ["perfecto, ya quedó confirmado tu pago"],
        "confidence": "high",
    },
    # wa_573002223344 — "ya está listo tu pedido" (preparing→ready, legal) → dispatch
    "ya está listo tu pedido": {
        "action": "transition",
        "to_stage": "ready",
        "evidence": ["ya está listo tu pedido, mañana te lo enviamos"],
        "confidence": "high",
    },
    # wa_573003334455 — "creo que ya salió el domiciliario" (hedged) → medium → suppressed
    "domiciliario": {
        "action": "transition",
        "to_stage": "shipping",
        "evidence": ["creo que ya salió el domiciliario con tu pedido, te confirmo"],
        "confidence": "medium",
    },
    # wa_573004445566 — charla sin señal de estado → none → silencio (aún con low)
    "caja de regalo": {"action": "none", "evidence": [], "confidence": "low"},
}


def test_golden_snapshot_emite_dispatch_y_suppressed_exactos() -> None:
    out = _run({"payload": _payload()}, ports={"llm": _router(_GOLDEN_ROUTES)})
    assert out == {
        "schema_version": 1,
        "snapshot_now_ms": 1720500000000,
        "dispatch": [
            {
                "kind": "order_stage_intent",
                "session_id": "wa_573001112233",
                "order_id": "order_01PAGO",
                "action": "confirm_payment",
                "evidence": ["perfecto, ya quedó confirmado tu pago"],
                "confidence": "high",
            },
            {
                "kind": "order_stage_intent",
                "session_id": "wa_573002223344",
                "order_id": "order_01LISTO",
                "action": "transition",
                "to_stage": "ready",
                "evidence": ["ya está listo tu pedido, mañana te lo enviamos"],
                "confidence": "high",
            },
        ],
        "suppressed": [
            {
                "session_id": "wa_573003334455",
                "order_id": "order_01DOMI",
                "reason": "low_confidence",
            }
        ],
        "llm_errors": [],
    }


def test_llm_se_llama_una_vez_por_conversacion_con_temperature_cero() -> None:
    recorder = _RecordingLLM(_router(_GOLDEN_ROUTES))
    _run({"payload": _payload()}, ports={"llm": recorder})
    assert len(recorder.calls) == 4  # una por conversación del snapshot
    assert all(c["temperature"] == 0.0 for c in recorder.calls)  # G-DET del nodo LLM


def test_llm_propone_cancelled_queda_suprimido_stage_not_allowed() -> None:
    """Guardrail 1 — y su PRECEDENCIA: cancelled ni siquiera está en el DAG, pero
    la razón visible es `stage_not_allowed` (regla 1 gana), no invalid_transition."""
    payload = _payload_one(
        order_id="order_01CANCEL", current_stage="shipping",
        text="mejor cancélalo, ya no lo quiero", who="customer",
    )
    llm = FixtureLLM(json.dumps({
        "action": "transition", "to_stage": "cancelled",
        "evidence": ["mejor cancélalo, ya no lo quiero"], "confidence": "high",
    }))
    out = _run({"payload": payload}, ports={"llm": llm})
    assert out["dispatch"] == []
    assert out["suppressed"] == [
        {
            "session_id": "wa_573009998877",
            "order_id": "order_01CANCEL",
            "reason": "stage_not_allowed",
        }
    ]


def test_transicion_new_a_delivered_saltea_el_dag_queda_invalid_transition() -> None:
    """Guardrail 2 — forward-only ADYACENTE en el DAG: de `new` solo se puede a
    `preparing`; saltar a `delivered` es ilegal aunque delivered sea stage válido."""
    payload = _payload_one(
        order_id="order_01SALTO", current_stage="new",
        text="listo, entregado tu pedido, gracias por comprar",
    )
    llm = FixtureLLM(json.dumps({
        "action": "transition", "to_stage": "delivered",
        "evidence": ["listo, entregado tu pedido"], "confidence": "high",
    }))
    out = _run({"payload": payload}, ports={"llm": llm})
    assert out["dispatch"] == []
    assert out["suppressed"] == [
        {
            "session_id": "wa_573009998877",
            "order_id": "order_01SALTO",
            "reason": "invalid_transition",
        }
    ]


def test_transicion_hacia_atras_queda_invalid_transition() -> None:
    """Guardrail 2 — el DAG es forward-only: shipping→preparing (hacia atrás) NUNCA."""
    payload = _payload_one(
        order_id="order_01ATRAS", current_stage="shipping",
        text="me toca volver a empacarlo, hubo un detalle",
    )
    llm = FixtureLLM(json.dumps({
        "action": "transition", "to_stage": "preparing",
        "evidence": ["me toca volver a empacarlo"], "confidence": "high",
    }))
    out = _run({"payload": payload}, ports={"llm": llm})
    assert out["dispatch"] == []
    assert out["suppressed"] == [
        {
            "session_id": "wa_573009998877",
            "order_id": "order_01ATRAS",
            "reason": "invalid_transition",
        }
    ]


def test_dos_conversaciones_misma_orden_despachan_max_un_intent_de_transicion() -> None:
    """Guardrail 4: la MISMA orden vinculada a dos sesiones (p.ej. el cliente
    escribe de dos números) no puede producir dos intents — el segundo queda
    suppressed `duplicate_order_intent`."""
    payload = {
        "schema_version": 1,
        "now_ms": NOW_MS,
        "conversations": [
            {
                "session_id": "wa_dup_a",
                "order_id": "order_01DUP",
                "current_stage": "preparing",
                "payment_confirmed": True,
                "messages": [
                    {"who": "human_operator", "text": "ya está listo tu pedido",
                     "at_ms": NOW_MS - 20000, "has_media": False},
                ],
            },
            {
                "session_id": "wa_dup_b",
                "order_id": "order_01DUP",
                "current_stage": "preparing",
                "payment_confirmed": True,
                "messages": [
                    {"who": "human_operator", "text": "quedó listo, te aviso apenas salga",
                     "at_ms": NOW_MS - 10000, "has_media": False},
                ],
            },
        ],
    }
    llm = _router({
        "ya está listo tu pedido": {
            "action": "transition", "to_stage": "ready",
            "evidence": ["ya está listo tu pedido"], "confidence": "high",
        },
        "quedó listo": {
            "action": "transition", "to_stage": "ready",
            "evidence": ["quedó listo, te aviso apenas salga"], "confidence": "high",
        },
    })
    out = _run({"payload": payload}, ports={"llm": llm})
    assert out["dispatch"] == [
        {
            "kind": "order_stage_intent",
            "session_id": "wa_dup_a",
            "order_id": "order_01DUP",
            "action": "transition",
            "to_stage": "ready",
            "evidence": ["ya está listo tu pedido"],
            "confidence": "high",
        }
    ]
    assert out["suppressed"] == [
        {
            "session_id": "wa_dup_b",
            "order_id": "order_01DUP",
            "reason": "duplicate_order_intent",
        }
    ]


def test_confirm_payment_sobre_pago_ya_confirmado_queda_suprimido() -> None:
    """Guardrail 5 (idempotencia del seam): si payment_confirmed ya es true, un
    confirm_payment del LLM NO viaja — suppressed `payment_already_confirmed`."""
    payload = _payload_one(
        order_id="order_01YAPAGO", payment_confirmed=True,
        text="listo, tu pago quedó confirmado",
    )
    llm = FixtureLLM(json.dumps({
        "action": "confirm_payment",
        "evidence": ["listo, tu pago quedó confirmado"], "confidence": "high",
    }))
    out = _run({"payload": payload}, ports={"llm": llm})
    assert out["dispatch"] == []
    assert out["suppressed"] == [
        {
            "session_id": "wa_573009998877",
            "order_id": "order_01YAPAGO",
            "reason": "payment_already_confirmed",
        }
    ]


def test_llm_json_malformado_suprime_unparseable_sin_crashear() -> None:
    """Guardrail 7 (MF: el LLM devuelve prosa en vez de JSON): no crash, la
    conversación queda suppressed `unparseable_llm_output` y el run COMPLETA."""
    payload = _payload_one(order_id="order_01ROTO")
    llm = FixtureLLM("Claro, el pedido parece listo para enviar. ¿Confirmo?")
    out = _run({"payload": payload}, ports={"llm": llm})
    assert out["dispatch"] == []
    assert out["llm_errors"] == []
    assert out["suppressed"] == [
        {
            "session_id": "wa_573009998877",
            "order_id": "order_01ROTO",
            "reason": "unparseable_llm_output",
        }
    ]


def test_llm_inalcanzable_registra_error_visible_y_no_crashea() -> None:
    """Guardrail 8 (molde test_run_survives_an_unreachable_llm_proxy, L-26): el
    proxy en otra caja revienta con URLError(Errno 99) — la conversación se salta,
    el error queda VISIBLE en llm_errors (session_id + mensaje) y el run COMPLETA."""
    payload = _payload_one(order_id="order_01CAIDO")
    out = _run({"payload": payload}, ports={"llm": _UnreachableLLM()})
    assert out["dispatch"] == []
    assert out["suppressed"] == []
    assert len(out["llm_errors"]) == 1
    err = out["llm_errors"][0]
    assert err["session_id"] == "wa_573009998877"
    assert "99" in err["error"]  # el diagnóstico viaja al trace, no se traga


def test_sin_vendor_llm_degrada_visible_a_llm_errors() -> None:
    """La rama `llm_port_missing` (run sin ports / build(llm=None)): cada
    conversación degrada VISIBLE a llm_errors — es exactamente el modo de fallo
    que el cable durable (build_agent inyecta consumes) existe para prevenir;
    si reaparece en prod, el result lo dice en vez de fingir silencio."""
    out = _run({"payload": _payload_one(order_id="order_01SINLLM")}, ports=None)
    assert out["dispatch"] == []
    assert out["suppressed"] == []
    assert len(out["llm_errors"]) == 1
    assert "llm_port_missing" in out["llm_errors"][0]["error"]


def test_falta_payload_lanza_valueerror_de_dominio() -> None:
    """Guardrail 9 (MF-7, molde window-strategist): no KeyError crudo."""
    with pytest.raises(ValueError, match="payload"):
        _run({}, ports={"llm": FixtureLLM("{}")})


def test_snapshot_sin_conversaciones_emite_listas_vacias() -> None:
    """Guardrail 10: vacío adentro, vacío afuera — sin llamar al LLM siquiera."""
    recorder = _RecordingLLM(FixtureLLM("{}"))
    out = _run(
        {"payload": {"schema_version": 1, "now_ms": NOW_MS, "conversations": []}},
        ports={"llm": recorder},
    )
    assert out == {
        "schema_version": 1,
        "snapshot_now_ms": NOW_MS,
        "dispatch": [],
        "suppressed": [],
        "llm_errors": [],
    }
    assert recorder.calls == []
