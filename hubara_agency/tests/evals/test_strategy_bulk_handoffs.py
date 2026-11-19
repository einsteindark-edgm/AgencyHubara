"""La sección «relevos al por mayor» del informe para el Estratega (tarjeta 34).

La decisión `datos_por_mayor` (paquete ventas-10) anota, en cada relevo por
mayorista, pedido grande o evento de empresa, qué datos faltaban (producto,
cantidad, fecha, ciudad, empresa) y si el cliente pidió una persona. El
informe cuenta, por relevo, qué faltaba, en cuántos Colossus habría preguntado,
cuántos mensajes escribió el equipo después y si terminó en pedido.

Y lo compara con el equipo: Jev lee lo que el equipo escribió después del
relevo (en la caja, con lo personal tapado) y dice cuál de los cinco datos
pidió. Al informe solo llega el conteo. La meta de la tarjeta: con 12 relevos o
más, en el 80 % de los que el equipo pidió algún dato, Colossus había marcado
como faltante al menos uno de esos.

Solo conteos: ni textos ni números de clientes. Datos sintéticos.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from src.plugins.chats.agent.sales_eval import strategy
from src.plugins.chats.shared import turn_traces
from src.sdk.connectorkit import InMemoryOrderFacts, OrderFacts

NOW = datetime(2026, 10, 10, 15, 0, tzinfo=UTC)
RECENT_MS = int(datetime(2026, 10, 8, 14, 0, tzinfo=UTC).timestamp() * 1000)
OLDER_MS = int(datetime(2026, 9, 20, 14, 0, tzinfo=UTC).timestamp() * 1000)
MINUTE_MS = 60_000
DATA = ("producto", "cantidad", "fecha", "ciudad", "empresa")
CAPABILITY = "datos_por_mayor"


def _at(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat()


def _conversation(vault: Path, sid: str, *, started_ms: int, workflow: str = "v2", order_id: str | None = None,
                  reason: str = "WHOLESALE_B2B") -> int:
    """Un episodio con un turno del bot que releva al minuto 2; devuelve cuándo."""
    turn_traces.append_trace(vault, sid, {
        "episode_id": "ep_001", "turn": 1, "workflow": workflow, "trigger": "customer",
        "turn_started_ms": started_ms + MINUTE_MS, "recorded_at_ms": started_ms + MINUTE_MS + 5_000,
        "stage_out": "descubrimiento",
    })
    handoff_ms = started_ms + 2 * MINUTE_MS
    path = vault / sid / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "episodes": [{"episode_id": "ep_001", "started_at_ms": started_ms, "closed_at_ms": None, "order_id": order_id}],
        "status_history": [{"tag": "HUMANO", "active_route": "humano", "reason_category": reason,
                            "timestamp": handoff_ms / 1000}],
    }), encoding="utf-8")
    return handoff_ms


def _noted(vault: Path, sid: str, row: dict) -> None:
    path = vault / sid / "evals" / "decisions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"stage": "turno", "capability": CAPABILITY, **row}, ensure_ascii=False) + "\n")


def _shadow(at_ms: int, missing: list[str]) -> dict:
    """En sombra: la regla no anota nada y lo que leyó Jev queda en `jev`."""
    return {"at_ms": at_ms, "by": "reglas", "provider": "sombra", "value": [], "jev": missing,
            "agree": not missing}


def _unsure(at_ms: int) -> dict:
    return {"at_ms": at_ms, "by": "reglas", "provider": "sombra", "value": []}


def _team_wrote(vault: Path, sid: str, *items: tuple[int, str]) -> None:
    path = vault / sid / "sessions" / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for at_ms, text in items:
            fh.write(json.dumps({"role": "assistant", "sender": "human", "content": text, "timestamp": _at(at_ms)},
                                ensure_ascii=False) + "\n")


class _Reader:
    """El lector del equipo falso: lo que «pidió» sale de palabras del texto."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    async def __call__(self, text: str) -> frozenset[str] | None:
        self.texts.append(text)
        if "NO-SE" in text:
            return None
        return frozenset(d for d in DATA if f"[{d}]" in text)


def _paid(order_id: str) -> OrderFacts:
    return OrderFacts(order_id=order_id, display_id="1", total_cop=1, currency_code="cop", pay_status="paid",
                      stage="new", customer="Cliente sintético", is_draft=False, is_test=False)


async def _report(vault: Path, **kwargs) -> dict:
    kwargs.setdefault("order_facts", InMemoryOrderFacts())
    kwargs.setdefault("team_reader", _Reader())
    return await strategy.build_report(vault, days=7, base_days=28, now=NOW, **kwargs)


def _four_handoffs(vault: Path) -> None:
    # A: faltaban cantidad y fecha; el equipo pidió la fecha; terminó en pedido pagado.
    at = _conversation(vault, "wa_100000000001", started_ms=RECENT_MS, order_id="ord_paid")
    _noted(vault, "wa_100000000001", _shadow(at + 1_000, ["cantidad", "fecha"]))
    _team_wrote(vault, "wa_100000000001", (at + 5 * MINUTE_MS, "Hola Ana, ¿para qué [fecha] la necesitas?"),
                (at + 9 * MINUTE_MS, "Listo, te paso el precio"))
    # B: faltaba todo; el equipo pidió la ciudad.
    at = _conversation(vault, "wa_100000000002", started_ms=RECENT_MS, reason="BULK_ORDER")
    _noted(vault, "wa_100000000002", _shadow(at + 1_000, list(DATA)))
    _team_wrote(vault, "wa_100000000002", (at + 4 * MINUTE_MS, "¿A qué [ciudad] te lo enviamos? Mi número es 3001234567"))
    # C: pidió una persona; faltaba el producto; el equipo pidió el producto.
    at = _conversation(vault, "wa_100000000003", started_ms=RECENT_MS, reason="CORPORATE_EVENT")
    _noted(vault, "wa_100000000003", _shadow(at + 1_000, ["producto", "persona"]))
    _team_wrote(vault, "wa_100000000003", (at + 4 * MINUTE_MS, "Claro, ¿qué [producto] te interesa?"))
    # D: no faltaba nada y el equipo pidió la cantidad (Colossus no la marcó).
    at = _conversation(vault, "wa_100000000004", started_ms=RECENT_MS)
    _noted(vault, "wa_100000000004", _shadow(at + 1_000, []))
    _team_wrote(vault, "wa_100000000004", (at + 4 * MINUTE_MS, "¿Cuántas, la [cantidad] exacta?"))


async def test_without_notes_the_section_says_what_is_missing(tmp_path: Path) -> None:
    _conversation(tmp_path, "wa_100000000001", started_ms=RECENT_MS)

    section = (await _report(tmp_path))["relevos_por_mayor"]

    assert section["disponible"] is False
    assert "sombra" in section["faltante"] and section["motivo"]


async def test_each_handoff_is_counted_by_what_was_missing(tmp_path: Path) -> None:
    _four_handoffs(tmp_path)

    report = await _report(tmp_path, order_facts=InMemoryOrderFacts([_paid("ord_paid")]))

    section = report["relevos_por_mayor"]
    assert section["disponible"] is True and section["nota"]
    assert section["datos"] == list(DATA)
    now = section["actual"]["colossus"]
    assert now["relevos"] == 4
    assert now["leidos"] == 4 and now["sin_lectura"] == 0
    assert now["con_algun_faltante"] == 3
    assert now["faltaba_por_dato"] == {"producto": 2, "cantidad": 2, "fecha": 2, "ciudad": 1, "empresa": 1}
    # Faltaba algo y no pidió una persona: A y B.
    assert now["habria_preguntado"] == 2
    assert now["pidio_persona"] == 1
    assert now["mensajes_del_equipo_despues"] == 5
    assert now["terminaron_en_pedido"] == 1


async def test_it_is_compared_with_what_the_team_asked_afterwards(tmp_path: Path) -> None:
    _four_handoffs(tmp_path)

    now = (await _report(tmp_path))["relevos_por_mayor"]["actual"]["colossus"]

    team = now["contra_el_equipo"]
    assert team["relevos_leidos"] == 4
    assert team["equipo_pidio_algun_dato"] == 4
    assert team["equipo_pidio_por_dato"] == {"producto": 1, "cantidad": 1, "fecha": 1, "ciudad": 1, "empresa": 0}
    # A (fecha), B (ciudad) y C (producto) lo había marcado; D no.
    assert team["colossus_lo_habia_marcado"] == 3
    # Menos de 12 relevos comparados: el porcentaje todavía no sale.
    assert now["suficientes"] is False and now["acierto_pct"] is None


async def test_with_twelve_compared_handoffs_the_percentage_comes_out(tmp_path: Path) -> None:
    for n in range(12):
        sid = f"wa_1000000001{n:02d}"
        at = _conversation(tmp_path, sid, started_ms=RECENT_MS)
        _noted(tmp_path, sid, _shadow(at + 1_000, ["fecha"] if n < 10 else []))
        _team_wrote(tmp_path, sid, (at + 3 * MINUTE_MS, "¿Para qué [fecha]?"))

    now = (await _report(tmp_path))["relevos_por_mayor"]["actual"]["colossus"]

    assert now["suficientes"] is True
    assert now["acierto_pct"] == 83.3


async def test_the_team_is_read_only_after_the_handoff_and_without_personal_data(tmp_path: Path) -> None:
    at = _conversation(tmp_path, "wa_100000000001", started_ms=RECENT_MS)
    _noted(tmp_path, "wa_100000000001", _shadow(at + 1_000, ["fecha"]))
    _team_wrote(tmp_path, "wa_100000000001", (at - 30_000, "antes del relevo [ciudad]"),
                (at + 3 * MINUTE_MS, "¿Para qué [fecha]? escríbeme al 3001234567 o a ana@correo.com"))
    reader = _Reader()

    now = (await _report(tmp_path, team_reader=reader))["relevos_por_mayor"]["actual"]["colossus"]

    [text] = reader.texts
    assert "[fecha]" in text and "antes del relevo" not in text
    assert "3001234567" not in text and "ana@correo.com" not in text
    assert now["contra_el_equipo"]["equipo_pidio_por_dato"]["ciudad"] == 0


async def test_unread_handoffs_and_an_unread_team_are_counted_apart(tmp_path: Path) -> None:
    at = _conversation(tmp_path, "wa_100000000001", started_ms=RECENT_MS)
    _noted(tmp_path, "wa_100000000001", _unsure(at + 1_000))
    at = _conversation(tmp_path, "wa_100000000002", started_ms=RECENT_MS)
    _noted(tmp_path, "wa_100000000002", _shadow(at + 1_000, ["fecha"]))
    _team_wrote(tmp_path, "wa_100000000002", (at + 3 * MINUTE_MS, "NO-SE"))
    # Sin mensajes del equipo después: no hay nada que leer (ni se le pregunta a Jev).
    at = _conversation(tmp_path, "wa_100000000003", started_ms=RECENT_MS)
    _noted(tmp_path, "wa_100000000003", _shadow(at + 1_000, ["fecha"]))
    reader = _Reader()

    now = (await _report(tmp_path, team_reader=reader))["relevos_por_mayor"]["actual"]["colossus"]

    assert now["relevos"] == 3 and now["leidos"] == 2 and now["sin_lectura"] == 1
    assert now["contra_el_equipo"]["relevos_leidos"] == 1  # el que no se pudo leer no cuenta
    assert now["contra_el_equipo"]["sin_mensajes_del_equipo"] == 1
    assert len(reader.texts) == 2


async def test_handoffs_fall_in_their_window_and_their_bot(tmp_path: Path) -> None:
    _four_handoffs(tmp_path)
    at = _conversation(tmp_path, "wa_100000000005", started_ms=OLDER_MS)
    _noted(tmp_path, "wa_100000000005", _shadow(at + 1_000, ["fecha"]))

    section = (await _report(tmp_path))["relevos_por_mayor"]

    assert section["base"]["colossus"]["relevos"] == 1
    assert section["actual"]["botsito"]["relevos"] == 0


async def test_the_section_carries_only_codes(tmp_path: Path) -> None:
    _four_handoffs(tmp_path)

    dumped = json.dumps((await _report(tmp_path))["relevos_por_mayor"], ensure_ascii=False)

    for private in ("Ana", "3001234567", "wa_", "100000000002", "ord_paid", "precio"):
        assert private not in dumped


async def test_a_failing_team_reader_never_breaks_the_report(tmp_path: Path) -> None:
    _four_handoffs(tmp_path)

    async def broken(_text: str) -> frozenset[str] | None:
        raise RuntimeError("Jev caído")

    now = (await _report(tmp_path, team_reader=broken))["relevos_por_mayor"]["actual"]["colossus"]

    assert now["relevos"] == 4 and now["contra_el_equipo"]["relevos_leidos"] == 0


async def test_the_plain_report_reads_the_bulk_handoffs_in_spanish(tmp_path: Path) -> None:
    _four_handoffs(tmp_path)

    text = strategy.render(await _report(tmp_path))

    assert "Relevos al por mayor (Colossus, ahora, en sombra): 4" in text
    assert "habría preguntado en 2" in text
    assert "en 3 de 4 el equipo pidió un dato que Colossus había marcado" in text


async def test_the_plain_report_says_when_there_are_no_bulk_handoff_notes(tmp_path: Path) -> None:
    _conversation(tmp_path, "wa_100000000001", started_ms=RECENT_MS)

    text = strategy.render(await _report(tmp_path))

    assert "Relevos al por mayor: no disponible" in text
