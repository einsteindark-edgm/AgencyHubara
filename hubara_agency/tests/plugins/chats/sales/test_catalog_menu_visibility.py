"""Lo que el cliente recibió del catálogo lo ven también el operador, Jev y
la calificación (revisión del PR #394).

* El menú de categorías deja en el historial sus filas, cuántos productos
  tiene cada una y el texto del menú, entero dentro de la línea que lee Jev
  (500 caracteres; una línea más larga se corta por el principio).
* La lista de una categoría dice cuál categoría era.
* El envelope del menú devuelve en `customer_text` el texto que leyó el
  cliente (la convención de las tarjetas que arma el código).
* El texto del menú nunca pierde la guía ni las categorías que no caben en
  la lista por un intro largo (WhatsApp corta el cuerpo por el final).
* El encabezado con el nombre de la categoría lo arma el código: no pasa por
  la guarda del texto del LLM.
* La lista de respaldo en páginas: una página que falla queda registrada y
  las citas apuntan a la primera página.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from exoclaw.agent.tools import ToolContext
from temporalio.testing import ActivityEnvironment

from src.platform.catalog.local_snapshot import LocalSnapshotCatalogClient
from src.platform.whatsapp import dtos as wa_dtos
from src.platform.whatsapp import limits, outbound
from src.plugins.chats.agent.sales.activities.flush_ui_intents import (
    _build_history_event,
    _dispatch_intent,
    flush_pending_ui_intents_activity,
)
from src.plugins.chats.agent.sales.catalog_menu import CATEGORY_MENU_GUIDE, category_menu_body
from src.plugins.chats.agent.sales.decisions.context import MAX_LINE_CHARS, customer_window
from src.plugins.chats.agent.sales.tools.ui_intents import PresentProductsTool
from tests.plugins.chats.sales.test_flush_intents_history_notes import (  # noqa: F401 — fixture
    _SESSION_ID,
    _read_history,
    _seed_metadata,
    vault,
)


def _menu_params(rows: list[tuple[str, str, int]], intro: str, more: list[str] | None = None) -> dict:
    return {
        "intro_text": intro,
        "sections": [{
            "title": "Categorías",
            "rows": [
                {"id": f"categoria:{slug}", "title": title, "description": f"{n} productos"}
                for slug, title, n in rows
            ],
        }],
        "button_label": "Ver categorías",
        "more_categories": more or [],
    }


def test_the_category_menu_note_says_what_was_offered() -> None:
    params = _menu_params(
        [("aromaticas", "Aromáticas", 8), ("religiosas", "Religiosas", 11), ("velones", "Velones", 12)],
        "Con gusto te muestro el catálogo.",
    )

    note = _build_history_event("categories", params)

    assert note["kind"] == "ui_component" and note["component_kind"] == "categories"
    content = note["content"]
    for row in ("Aromáticas (8 productos)", "Religiosas (11 productos)", "Velones (12 productos)"):
        assert row in content
    assert "Con gusto te muestro el catálogo." in content and CATEGORY_MENU_GUIDE in content


def test_the_menu_note_always_fits_whole_in_the_line_jev_reads() -> None:
    """Una línea de más de 500 caracteres Jev la lee cortada por el PRINCIPIO
    (se pierde qué se ofreció): el cuerpo del menú se recorta para que no."""
    rows = [(f"cat-{i}", f"Categoría número {i}", 3 + i) for i in range(10)]
    params = _menu_params(rows, "Con gusto te muestro nuestro catálogo completo. " * 20, [f"Otra {i}" for i in range(6)])

    note = _build_history_event("categories", params)
    window = customer_window(
        [{"role": "assistant", **note}, {"role": "user", "content": "las de la 3", "wamid": "w1"}],
        burst_wamids={"w1"},
    )

    assert len(" ".join(note["content"].split())) <= MAX_LINE_CHARS
    [line] = window.lines
    assert line.startswith("[asesor] 🗂️"), line[:40]
    assert all(f"Categoría número {i}" in line for i in range(10))


def test_with_a_long_intro_the_note_keeps_the_guide_and_the_named_categories() -> None:
    """Caso del revisor: con un texto del asesor de ~360 caracteres la nota
    cortaba el final (la guía y las categorías nombradas). Se recorta el
    texto del asesor, no la cola."""
    titles = [
        "Velas Religiosas", "Velas Aromáticas", "Velones", "Velas Decorativas", "Velas de Cumpleaños",
        "Kits de Regalo", "Difusores", "Portavelas", "Velas Zodiacales", "Otros",
    ]
    rows = [(f"c{i}", t, 3 + i) for i, t in enumerate(titles)]
    intro = "Con gusto te muestro nuestro catálogo de velas artesanales. " * 6
    note = _build_history_event("categories", _menu_params(rows, intro, ["Navidad", "Halloween", "Amor y Amistad"]))

    content = " ".join(note["content"].split())
    assert len(content) <= MAX_LINE_CHARS
    assert CATEGORY_MENU_GUIDE in content
    assert "Navidad, Halloween y Amor y Amistad" in content


def test_the_list_of_one_category_says_which_category() -> None:
    rows = [{"id": f"velon-{i}", "title": f"Velón {i}", "product_retailer_id": f"HUB-{i}"} for i in range(12)]

    note = _build_history_event(
        "products_list", {"category": "Velones", "sections": [{"title": "Velones", "rows": rows}]}
    )

    assert "Velones" in note["content"] and "12" in note["content"]


def _snapshot(tmp_path: Path, raw_products: list[dict]) -> LocalSnapshotCatalogClient:
    root = tmp_path / "catalog"
    root.mkdir()
    (root / "snapshot.json").write_text(json.dumps(raw_products), encoding="utf-8")
    (root / "manifest.json").write_text(
        json.dumps({"version": "v", "fetched_at": datetime.now(timezone.utc).isoformat(), "product_count": len(raw_products)}),
        encoding="utf-8",
    )
    return LocalSnapshotCatalogClient(root)


def _raw(handle: str, slug: str, label: str) -> dict:
    return {
        "id": f"prod_{handle}", "handle": handle, "title": handle.replace("-", " ").title(),
        "status": "published", "thumbnail": f"https://img.test/{handle}.webp",
        "variants": [{"id": f"v_{handle}", "title": "Unico", "sku": f"HUB-{handle.upper()}",
                      "prices": [{"amount": "23000", "currency_code": "cop"}]}],
        "categories": [slug], "category_labels": {slug: label},
    }


def _ok_client() -> SimpleNamespace:
    ok = SimpleNamespace(ok=True, wa_message_id="wamid.menu", error=None)
    return SimpleNamespace(
        send_interactive_list=AsyncMock(return_value=ok),
        send_product_list=AsyncMock(return_value=ok),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "intro",
    [
        "Con gusto te muestro el catálogo.",
        # Texto de reporte: la guarda del flush lo cambia por el neutro.
        "Voy a llamar a present_products para mostrarle el catálogo al cliente.",
    ],
)
async def test_the_menu_customer_text_is_only_what_the_code_wrote(
    tmp_path: Path, monkeypatch, _isolate_vault_dir: Path, intro: str
) -> None:
    """`customer_text` lleva solo lo que arma el código (la guía y las
    categorías nombradas): el texto del asesor ya lo lee la verificación de
    los argumentos (`card_texts`), y el flush puede cambiarlo por el neutro.
    Se prueba por el flush de verdad, con su guarda."""
    import src.platform.whatsapp.client as wa_client

    raw = [_raw(f"velon-{i:02d}", "velones", "Velones") for i in range(16)] + [
        _raw(f"aroma-{i:02d}", "aromaticas", "Aromáticas") for i in range(15)
    ]
    tool = PresentProductsTool(workspace=str(tmp_path), catalog=_snapshot(tmp_path, raw))
    send = AsyncMock(return_value=SimpleNamespace(ok=True, wa_message_id="wamid.menu", error=None))
    monkeypatch.setattr(wa_client, "send_interactive_list", send)
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "PID")

    out = json.loads(await tool.execute_with_context(
        ToolContext(session_key=_SESSION_ID, channel="whatsapp", chat_id="c"), intro_text=intro
    ))
    await ActivityEnvironment().run(flush_pending_ui_intents_activity, _SESSION_ID)

    sent = send.call_args.args[2].body
    assert out["kind"] == "categories"
    assert sent.endswith(out["customer_text"])
    assert CATEGORY_MENU_GUIDE in out["customer_text"]
    assert intro not in out["customer_text"]
    assert "present_products" not in sent


def test_a_long_intro_never_pushes_out_the_guide_or_the_other_categories() -> None:
    """El caso del revisor: WhatsApp corta el cuerpo por el FINAL."""
    intro = limits.truncate(("Con gusto te muestro nuestro catálogo de velas artesanales hechas a mano. " * 14).strip(), limits.MAX_LIST_BODY)
    body = category_menu_body(intro, ["Navidad", "Velas de té", "Aromaterapia"], max_len=limits.MAX_LIST_BODY)

    payload = outbound.build_interactive_list("57300", wa_dtos.InteractiveListOutbound(
        body=body, button_label="Ver categorías",
        sections=[wa_dtos.ListSection(title="Categorías", rows=[
            wa_dtos.ListRow(id="categoria:velones", title="Velones", description="12 productos")])],
    ), None)
    sent = payload["interactive"]["body"]["text"]

    assert len(body) <= limits.MAX_LIST_BODY
    assert sent == body, "nada que cortar"
    assert CATEGORY_MENU_GUIDE in sent
    assert "Navidad, Velas de té y Aromaterapia" in sent
    assert sent.startswith("Con gusto te muestro")


@pytest.mark.asyncio
async def test_the_category_header_is_code_text_and_skips_the_llm_text_guard(monkeypatch) -> None:
    """Con la guarda del texto del LLM rechazándolo todo, el intro sale neutro
    pero el encabezado sigue diciendo la categoría (lo armó el código)."""
    import src.sdk.agentkit as agentkit

    monkeypatch.setattr(agentkit, "looks_like_admin_leak", lambda _text: True)
    monkeypatch.setenv("META_CATALOG_ID", "CAT_TEST")
    client = _ok_client()
    rows = [{"id": "velon-1", "title": "Velón 1", "product_retailer_id": "HUB-1"}]

    await _dispatch_intent(
        wa_client=client, wa_dtos=wa_dtos, kind="products_list",
        params={"intro_text": "Nuestros velones:", "category": "Velones", "sections": [{"title": "Velones", "rows": rows}]},
        fallback={"prefer_native_product_list": True}, phone_number_id="PID", to_number="573000000000",
        last_inbound_message_id=None,
    )

    sent = client.send_product_list.call_args.args[2]
    assert sent.header_text == "Velones"


def test_many_names_that_do_not_fit_are_counted() -> None:
    names = [f"Categoría número {i}" for i in range(80)]

    body = category_menu_body("Hola", names, max_len=300)

    assert len(body) <= 300 and CATEGORY_MENU_GUIDE in body
    line = body.split("\n\n")[-1]
    listed, _, rest = line.removeprefix("También tenemos: ").partition(" y ")
    shown = listed.split(", ")
    more, _, tail = rest.partition(" ")
    assert shown == names[: len(shown)] and len(shown) + int(more) == 80
    assert tail == "más. Escríbeme la que quieras ver."


def test_when_not_even_one_name_fits_the_menu_says_how_many_more() -> None:
    body = category_menu_body("", ["X" * 200, "Y" * 200], max_len=150)

    assert body == f"{CATEGORY_MENU_GUIDE}\n\nTenemos 2 categorías más: escríbeme la que quieras ver."


def _rows(n: int) -> list[dict]:
    return [{"id": f"vela-{i}", "title": f"Vela {i}", "product_retailer_id": f"HUB-{i}"} for i in range(n)]


@pytest.mark.asyncio
async def test_an_exception_in_a_later_page_does_not_undo_the_first(monkeypatch) -> None:
    """Una excepción (no un `ok=False`) en la página 2 marcaba el intent
    entero como fallido aunque la página 1 había llegado."""
    monkeypatch.delenv("META_CATALOG_ID", raising=False)
    client = SimpleNamespace(send_interactive_list=AsyncMock(side_effect=[
        SimpleNamespace(ok=True, wa_message_id="wamid.page1", error=None),
        RuntimeError("timeout de Meta"),
        SimpleNamespace(ok=True, wa_message_id="wamid.page3", error=None),
    ]))
    failures: list[dict] = []

    result = await _dispatch_intent(
        wa_client=client, wa_dtos=wa_dtos, kind="products_list",
        params={"intro_text": "Nuestro catálogo:", "sections": [{"title": "Velas", "rows": _rows(25)}]},
        fallback={"prefer_native_product_list": True}, phone_number_id="PID", to_number="573000000000",
        last_inbound_message_id=None, failures=failures,
    )

    assert client.send_interactive_list.await_count == 3
    assert result.ok is True and result.wa_message_id == "wamid.page1"
    [failure] = failures
    assert failure["page"] == 2 and failure["pages"] == 3 and "timeout de Meta" in failure["error"]


@pytest.mark.asyncio
async def test_a_failed_page_is_recorded_and_quotes_point_to_the_first_page(vault, monkeypatch) -> None:  # noqa: F811
    import src.platform.whatsapp.client as wa_client

    monkeypatch.delenv("META_CATALOG_ID", raising=False)
    monkeypatch.setattr(wa_client, "send_interactive_list", AsyncMock(side_effect=[
        SimpleNamespace(ok=True, wa_message_id="wamid.page1", error=None),
        SimpleNamespace(ok=False, wa_message_id=None, error="(#131000) boom"),
        SimpleNamespace(ok=True, wa_message_id="wamid.page3", error=None),
    ]))
    _seed_metadata(vault, [{
        "id": "i-1", "kind": "products_list",
        "params": {"intro_text": "Nuestro catálogo:", "sections": [{"title": "Velas", "rows": _rows(25)}]},
        "fallback": {"prefer_native_product_list": True},
    }])

    report = await ActivityEnvironment().run(flush_pending_ui_intents_activity, _SESSION_ID)

    assert report == [{"kind": "products_list", "wamid": "wamid.page1", "ok": True}]
    metadata = json.loads((vault / _SESSION_ID / "metadata.json").read_text(encoding="utf-8"))
    [failure] = metadata["ui_intents_failures"]
    assert failure["kind"] == "products_list" and failure["page"] == 2 and failure["pages"] == 3
    assert "(#131000) boom" in failure["error"]
    [note] = _read_history(vault)
    assert note["wamid"] == "wamid.page1"
    assert "página 2 de 3" in note["content"]
