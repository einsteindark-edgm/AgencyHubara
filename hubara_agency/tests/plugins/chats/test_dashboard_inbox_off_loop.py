"""Armar la bandeja no frena al resto del API (2026-10-22).

`list_dashboard_sessions` leía el vault entero DENTRO del event loop del único proceso uvicorn. Medido con 600 chats
sintéticos: ~200 ms por llamada (~0,9 s la primera) en los que el API no atendía nada —ni el webhook de WhatsApp ni el
SSE—, y con Graph lento (nombres de anuncios: httpx síncrono, 4 s de timeout por lote) segundos. La llaman el
muestreador del SSE en cada tick con cambios, cada conexión SSE al abrir y cada `GET /sessions`. Ahora:

- el disco y Graph van en un hilo: el loop sigue atendiendo mientras tanto;
- una lectura a la vez: quien pide mientras otra corre comparte la SIGUIENTE, que empieza después de su pedido (nunca
  una bandeja vieja), y una reconexión masiva no arma N lecturas del vault a la vez (la caja tiene la memoria justa).
"""

import asyncio
import json
import threading
from pathlib import Path

import pytest

from src.plugins.chats.api import dashboard


def _chat(vault: Path, session_id: str, **metadata) -> Path:
    folder = vault / session_id
    (folder / "sessions").mkdir(parents=True)
    (folder / "metadata.json").write_text(json.dumps({"tag": "NUEVO", **metadata}), encoding="utf-8")
    (folder / "sessions" / f"{session_id}.jsonl").write_text('{"role": "user", "content": "Hola"}\n', encoding="utf-8")
    return folder


async def _until(condition, timeout_s: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while not condition():
        if loop.time() > deadline:
            raise AssertionError("no pasó a tiempo")
        await asyncio.sleep(0.005)


@pytest.fixture
def slow_disk(monkeypatch):
    """Cada `metadata.json` se lee y la lectura queda frenada hasta que el test la suelta (o 1 s): el disco lento."""
    gate = threading.Event()
    log: list[str] = []

    class SlowStore(dashboard.FilesystemMetadataStore):
        def read(self, session_id):
            data = super().read(session_id)
            log.append("leyendo")
            gate.wait(timeout=1.0)
            log.append("leido")
            return data

    monkeypatch.setattr(dashboard, "FilesystemMetadataStore", SlowStore)
    return gate, log


async def test_mientras_se_lee_el_vault_el_api_sigue_atendiendo(_isolate_vault_dir: Path, slow_disk) -> None:
    gate, log = slow_disk
    _chat(_isolate_vault_dir, "wa_000000000101")

    building = asyncio.create_task(dashboard.list_dashboard_sessions())
    await _until(lambda: "leyendo" in log)
    log.append("api_atiende")  # otro pedido del API, mientras el disco tarda
    gate.set()
    snapshot = await building

    assert [s["session_id"] for s in snapshot["sessions"]] == ["wa_000000000101"]
    # Antes el loop quedaba tomado: «api_atiende» llegaba recién cuando el disco terminaba.
    assert log.index("api_atiende") < log.index("leido")


async def test_graph_lento_no_frena_al_api(_isolate_vault_dir: Path, monkeypatch) -> None:
    _chat(_isolate_vault_dir, "wa_000000000101", origin={"channel": "ad", "source_id": "120200000000000001", "first_seen_ms": 1})
    gate = threading.Event()
    log: list[str] = []

    def slow_graph(ad_ids, *, token, transport=None):
        log.append("graph")
        gate.wait(timeout=1.0)
        log.append("graph_respondio")
        return {"120200000000000001": {"campaign_name": "Velas", "ad_name": "Combo"}}

    monkeypatch.setattr(dashboard, "meta_marketing_token", lambda: "TOK")
    monkeypatch.setattr(dashboard, "fetch_meta_ad_names", slow_graph)
    dashboard._ad_names_cache.clear()
    try:
        building = asyncio.create_task(dashboard.list_dashboard_sessions())
        await _until(lambda: "graph" in log)
        log.append("api_atiende")
        gate.set()
        snapshot = await building
    finally:
        dashboard._ad_names_cache.clear()

    assert snapshot["sessions"][0]["origin"]["campaign_name"] == "Velas"
    assert log.index("api_atiende") < log.index("graph_respondio")


async def test_los_pedidos_a_la_vez_comparten_una_lectura(_isolate_vault_dir: Path, monkeypatch) -> None:
    for n in range(3):
        _chat(_isolate_vault_dir, f"wa_00000000010{n}")
    reads: list[str] = []

    class CountingStore(dashboard.FilesystemMetadataStore):
        def read(self, session_id):
            reads.append(session_id)
            return super().read(session_id)

    monkeypatch.setattr(dashboard, "FilesystemMetadataStore", CountingStore)

    results = await asyncio.gather(*(dashboard.list_dashboard_sessions() for _ in range(5)))

    assert all(result == results[0] for result in results)
    assert len(results[0]["sessions"]) == 3
    # Cinco pedidos (p. ej. clientes que reconectan juntos tras un deploy): una sola lectura del vault, no cinco.
    assert len(reads) == 3


async def test_quien_pide_durante_una_lectura_recibe_una_que_empezo_despues(_isolate_vault_dir: Path, slow_disk) -> None:
    gate, log = slow_disk
    folder = _chat(_isolate_vault_dir, "wa_000000000101", tag="NUEVO")

    first = asyncio.create_task(dashboard.list_dashboard_sessions())
    await _until(lambda: "leyendo" in log)  # la primera ya leyó el chat (NUEVO) y sigue en curso
    (folder / "metadata.json").write_text(json.dumps({"tag": "INTERESADO"}), encoding="utf-8")  # el chat cambió
    second = asyncio.create_task(dashboard.list_dashboard_sessions())
    await asyncio.sleep(0.01)
    gate.set()

    assert (await first)["sessions"][0]["tag"] == "NUEVO"
    # Compartir la lectura en curso le daría la bandeja de antes del cambio: el muestreador publicaría una vieja.
    assert (await second)["sessions"][0]["tag"] == "INTERESADO"
