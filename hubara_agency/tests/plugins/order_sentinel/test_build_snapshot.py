"""Tests del plan PURO del snapshot de order_sentinel (fase roja TDD).

`build_snapshot_from_sessions(now_ms, sessions)` — sin I/O ni reloj, molde
`reengagement/agent/cycle/use_cases/build_snapshot.py`. Cada session es
`(session_id, metadata, history_events, watermark_ms | None)`:

  * metadata = el dict de metadata.json (tag, episodes[], registered_order).
  * history_events = líneas ya parseadas del JSONL de historial
    (`<session>/sessions/<session>.jsonl`, shape del
    FilesystemMessageHistoryStore: role/sender/content/timestamp ISO/image_url).
  * watermark_ms = último analizado (None = nunca analizado).

Contrato fijado acá (decisiones de esta fase roja):
  * El snapshot lleva TOP-LEVEL `watermarks: {session_id: max_at_ms_de_los_
    NUEVOS}` — "hasta dónde analicé" — que el workflow pasa después a
    `execute_order_intents`.
  * `current_stage` / `payment_confirmed` NO los pone el use case puro
    (los inyecta la activity vía API de orders) — campos AUSENTES acá.
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.plugins.order_sentinel.agent.cycle.use_cases.build_snapshot import (
    build_snapshot_from_sessions,
)

NOW_MS = 1_752_000_000_000


def _iso(ms: int) -> str:
    """epoch ms → ISO UTC como lo persiste FilesystemMessageHistoryStore."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _msg(role: str, content, ms: int, *, sender: str | None = None,
         image_url: str | None = None) -> dict:
    event: dict = {"role": role, "content": content, "timestamp": _iso(ms)}
    if sender:
        event["sender"] = sender
    if image_url:
        event["image_url"] = image_url
    return event


_METADATA_HUMANO_CON_ORDEN = {
    "tag": "HUMANO",
    "episodes": [{"episode_id": "ep_001", "order_id": "order_ABC"}],
}


def test_mapea_who_texto_at_ms_y_media_del_historial():
    """role=user→customer; assistant+sender=human→human_operator;
    assistant sin sender→bot. content no-string (multimodal) → text ""
    conservando has_media. at_ms = epoch ms del timestamp ISO."""
    events = [
        _msg("user", "ya pagué", 1_000),
        _msg("user", [{"type": "image"}], 2_000, image_url="media/comprobante.jpg"),
        _msg("assistant", "ok, reviso", 3_000),
        _msg("assistant", "confirmado, sale hoy", 4_000, sender="human"),
    ]
    snapshot = build_snapshot_from_sessions(
        NOW_MS, [("wa_573001", _METADATA_HUMANO_CON_ORDEN, events, None)]
    )

    assert snapshot["schema_version"] == 1
    assert snapshot["now_ms"] == NOW_MS
    (convo,) = snapshot["conversations"]
    assert convo["session_id"] == "wa_573001"
    assert convo["order_id"] == "order_ABC"
    assert convo["messages"] == [
        {"who": "customer", "text": "ya pagué", "at_ms": 1_000, "has_media": False},
        {"who": "customer", "text": "", "at_ms": 2_000, "has_media": True},
        {"who": "bot", "text": "ok, reviso", "at_ms": 3_000, "has_media": False},
        {
            "who": "human_operator",
            "text": "confirmado, sale hoy",
            "at_ms": 4_000,
            "has_media": False,
        },
    ]
    # El use case puro NO conoce el estado de la orden — eso lo inyecta la
    # activity consultando GET /api/orders/{id}. Campos AUSENTES, no None.
    assert "current_stage" not in convo
    assert "payment_confirmed" not in convo


def test_excluye_sesiones_sin_tag_humano_sin_orden_o_sin_mensajes_nuevos():
    events_nuevos = [_msg("user", "hola", 5_000)]
    sessions = [
        # Orden vinculada pero tag != HUMANO → fuera.
        (
            "wa_no_humano",
            {"tag": "INTERESADO", "episodes": [{"order_id": "order_X"}]},
            events_nuevos,
            None,
        ),
        # tag HUMANO pero sin orden vinculada → fuera.
        (
            "wa_sin_orden",
            {"tag": "HUMANO", "episodes": [{"episode_id": "ep_1"}]},
            events_nuevos,
            None,
        ),
        # HUMANO + orden, pero nada posterior al watermark → fuera.
        (
            "wa_sin_nuevos",
            _METADATA_HUMANO_CON_ORDEN,
            [_msg("user", "viejo", 4_000)],
            4_000,
        ),
    ]
    snapshot = build_snapshot_from_sessions(NOW_MS, sessions)
    assert snapshot["conversations"] == []


def test_order_id_ultimo_episodio_fallback_registered_y_solo_prefijos_validos():
    events = [_msg("user", "listo", 9_000)]
    sessions = [
        # El ÚLTIMO episodio con order_id gana (el episodio final sin
        # order_id no borra el vínculo).
        (
            "wa_ultimo_episodio",
            {
                "tag": "HUMANO",
                "episodes": [
                    {"order_id": "order_VIEJO"},
                    {"order_id": "order_NUEVO"},
                    {"episode_id": "ep_sin_orden"},
                ],
            },
            events,
            None,
        ),
        # Sin order_id en episodes → fallback registered_order.order_id.
        (
            "wa_fallback_registered",
            {
                "tag": "HUMANO",
                "episodes": [{"episode_id": "ep_1"}],
                "registered_order": {"order_id": "draft_R1"},
            },
            events,
            None,
        ),
        # IDs que no empiezan con order_/draft_ (stubs HUB-/AUDIT-) NO
        # cuentan como orden vinculada → sesión fuera.
        (
            "wa_stub_invalido",
            {
                "tag": "HUMANO",
                "episodes": [{"order_id": "HUB-123"}],
                "registered_order": {"order_id": "AUDIT-9"},
            },
            events,
            None,
        ),
    ]
    snapshot = build_snapshot_from_sessions(NOW_MS, sessions)
    linked = {c["session_id"]: c["order_id"] for c in snapshot["conversations"]}
    assert linked == {
        "wa_ultimo_episodio": "order_NUEVO",
        "wa_fallback_registered": "draft_R1",
    }


def test_watermark_recorta_a_nuevos_mas_10_de_contexto_y_publica_watermarks():
    # 12 mensajes previos (100..1200) + 2 nuevos (1300, 1400) con
    # watermark=1200 → los 2 nuevos + SOLO los últimos 10 previos como
    # contexto, en orden cronológico.
    previos = [_msg("user", f"p{i}", i * 100) for i in range(1, 13)]
    nuevos = [
        _msg("assistant", "salió el pedido", 1_300, sender="human"),
        _msg("user", "gracias", 1_400),
    ]
    snapshot = build_snapshot_from_sessions(
        NOW_MS,
        [("wa_ctx", _METADATA_HUMANO_CON_ORDEN, previos + nuevos, 1_200)],
    )

    (convo,) = snapshot["conversations"]
    at_ms = [m["at_ms"] for m in convo["messages"]]
    assert at_ms == [
        300, 400, 500, 600, 700, 800, 900, 1_000, 1_100, 1_200, 1_300, 1_400
    ]
    # "Hasta dónde analicé": el max at_ms de los mensajes NUEVOS, top-level,
    # para que el workflow se lo pase a execute_order_intents al cierre.
    assert snapshot["watermarks"] == {"wa_ctx": 1_400}


def test_watermark_none_significa_todos_nuevos():
    events = [_msg("user", "primer contacto", 7_000)]
    snapshot = build_snapshot_from_sessions(
        NOW_MS, [("wa_virgen", _METADATA_HUMANO_CON_ORDEN, events, None)]
    )
    (convo,) = snapshot["conversations"]
    assert [m["at_ms"] for m in convo["messages"]] == [7_000]
    assert snapshot["watermarks"] == {"wa_virgen": 7_000}


def test_cap_de_mensajes_nuevos_por_conversacion():
    """PM-004 (bomba de backlog): una sesión virgen con historia larga NO mete
    toda la historia al prompt — solo los ÚLTIMOS 30 nuevos (los más recientes
    son la señal de estado actual). El watermark igual cierra en el max at_ms:
    lo más viejo que quedó afuera no se re-analiza (es historia, no señal)."""
    events = [_msg("user", f"m{i}", i * 100) for i in range(1, 81)]  # 80 nuevos
    snapshot = build_snapshot_from_sessions(
        NOW_MS, [("wa_larga", _METADATA_HUMANO_CON_ORDEN, events, None)]
    )
    (convo,) = snapshot["conversations"]
    at_ms = [m["at_ms"] for m in convo["messages"]]
    assert len(at_ms) == 30
    assert at_ms == [i * 100 for i in range(51, 81)]  # los últimos 30, cronológico
    assert snapshot["watermarks"] == {"wa_larga": 8_000}


def test_cap_de_conversaciones_por_ciclo_deja_el_resto_para_manana():
    """PM-004: máx 20 conversaciones por ciclo (cada una es una llamada LLM).
    Las excedentes quedan FUERA sin watermark → el próximo ciclo las agarra
    (el backlog drena en días, sin livelock). El recorte se reporta en
    `truncated_sessions` (no silent caps)."""
    sessions = [
        (f"wa_{i:03d}", _METADATA_HUMANO_CON_ORDEN, [_msg("user", "hola", 5_000)], None)
        for i in range(25)
    ]
    snapshot = build_snapshot_from_sessions(NOW_MS, sessions)
    assert len(snapshot["conversations"]) == 20
    assert len(snapshot["watermarks"]) == 20
    assert snapshot["truncated_sessions"] == 5
    # deterministas: entran las primeras 20 en el orden de entrada
    assert snapshot["conversations"][0]["session_id"] == "wa_000"
    assert "wa_020" not in snapshot["watermarks"]
