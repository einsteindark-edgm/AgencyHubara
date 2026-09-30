"""El laboratorio vuelve a leer las fotos del cliente con la visión de hoy.

Hasta el 2026-09-29 el laboratorio reusaba la descripción que la visión de
PRODUCCIÓN escribió en su momento: un cambio en la visión (el texto que se lee
en la foto, la identificación contra el catálogo) no se podía medir. Ahora el
banco trae las fotos de producto y el sandbox las vuelve a pasar por lo MISMO
que el ingest de producción: la visión, el identificador, el texto con que la
foto entra a la conversación y la nota del turno. Lo que la visión dijo de
cada foto se guarda en el banco para que los brazos vean lo mismo.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from src.plugins.chats.agent.sales.use_cases.photo_product import PhotoIdentifier, PhotoProduct, photo_reentry_text
from src.plugins.chats.agent.sales_lab.sandbox.photos import LabPhotoStep
from src.sdk.catalogkit import CatalogPriceDTO, CatalogProductDTO, CatalogVariantDTO
from src.sdk.connectorkit import VISION_KIND_PRODUCT_PHOTO, VisibleText, VisionResult

SID = "wa_573009876543"
OLD = "[el cliente envió una foto: vela gris con detalles dorados en forma de cruz y rostro de Jesús]"
NEW_DESCRIPTION = "vela gris en forma de cruz con rostro y corona dorada"
SACRIFICIO = CatalogProductDTO(
    id="prod_sacrificio", handle="sacrificio-de-amor", title="Sacrificio de Amor", status="published",
    variants=[CatalogVariantDTO(id="v1", title="Unico", sku="HUB-SACRIFICIO",
                                prices=[CatalogPriceDTO(amount="20000", currency_code="cop")])],
)


def _jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (40, 30), (120, 120, 120)).save(out, "JPEG")
    return out.getvalue()


class _Vision:
    def __init__(self, result: VisionResult) -> None:
        self.result = result
        self.calls = 0

    async def describe_image(self, image_bytes: bytes, mime_type: str) -> VisionResult:
        self.calls += 1
        return self.result


class _Catalog:
    async def search(self, q: str, *, limit: int = 10, category: str | None = None) -> Any:
        class _Result:
            results = [SACRIFICIO]

        return _Result()


SCREENSHOT = VisionResult(
    description=NEW_DESCRIPTION, ok=True, kind=VISION_KIND_PRODUCT_PHOTO,
    visible_text=VisibleText(product_name="Sacrificio de Amor", price="COP 20,000"),
)


def _step(tmp_path: Path, vision: _Vision, *, with_file: bool = True) -> LabPhotoStep:
    media = tmp_path / "bench" / "vault" / SID / "media"
    media.mkdir(parents=True)
    if with_file:
        (media / "2348323652689569.jpg").write_bytes(_jpeg())
    identifier = PhotoIdentifier(catalog=_Catalog(), index=None, embedder=None, matcher=None)
    return LabPhotoStep(media_dir=media, vision=vision, identifier=identifier, cache_dir=tmp_path / "bench" / "photo_reads")


def _message(text: str) -> dict[str, Any]:
    return {"text": text, "image": "2348323652689569.jpg", "caption": "tienes esta?", "kind": "text"}


async def test_the_photo_enters_as_the_vision_of_today_reads_it(tmp_path: Path) -> None:
    step = _step(tmp_path, _Vision(SCREENSHOT))

    reread = await step.reread(_message(f'{OLD} con el texto: "tienes esta?"'))

    assert reread is not None
    product = PhotoProduct("sacrificio-de-amor", "Sacrificio de Amor", "nombre", "Sacrificio de Amor")
    assert reread.apply(f'{OLD} con el texto: "tienes esta?"') == (
        photo_reentry_text(NEW_DESCRIPTION, product) + ' con el texto: "tienes esta?"'
    )
    assert reread.note is not None and "«Sacrificio de Amor» (handle sacrificio-de-amor)" in reread.note
    assert reread.product == {"handle": "sacrificio-de-amor", "how": "nombre"}


async def test_what_the_ingest_added_around_the_photo_stays(tmp_path: Path) -> None:
    """La foto puede venir envuelta (el banner del anuncio): solo cambia ella."""
    step = _step(tmp_path, _Vision(SCREENSHOT))
    banner = "[el cliente vino desde un anuncio: Velas religiosas]\n"

    reread = await step.reread(_message(banner + OLD))

    assert reread is not None
    assert reread.apply(banner + OLD).startswith(banner + "[el cliente envió una foto: " + NEW_DESCRIPTION)


async def test_a_photo_production_could_not_see_is_read_now(tmp_path: Path) -> None:
    step = _step(tmp_path, _Vision(SCREENSHOT))
    blind = "[el cliente envió una imagen que no pude ver bien]"

    reread = await step.reread(_message(blind))

    assert reread is not None and reread.apply(blind).startswith("[el cliente envió una foto: " + NEW_DESCRIPTION)


async def test_without_the_photo_in_the_bench_the_production_text_stays(tmp_path: Path) -> None:
    vision = _Vision(SCREENSHOT)
    step = _step(tmp_path, vision, with_file=False)

    assert await step.reread(_message(OLD)) is None
    assert await step.reread({"text": "hola"}) is None
    assert vision.calls == 0


async def test_when_the_vision_fails_the_production_text_stays(tmp_path: Path) -> None:
    step = _step(tmp_path, _Vision(VisionResult(description="", ok=False, error="provider_error")))

    assert await step.reread(_message(OLD)) is None


async def test_every_arm_sees_the_same_reading_of_the_photo(tmp_path: Path) -> None:
    """La visión corre una vez por foto: lo que dijo queda en el banco y el
    otro brazo (otro proceso) lo reusa, así los brazos se comparan con la
    misma foto leída igual."""
    vision = _Vision(SCREENSHOT)
    first = await _step(tmp_path, vision).reread(_message(OLD))
    other_process = LabPhotoStep(
        media_dir=tmp_path / "bench" / "vault" / SID / "media",
        vision=vision,
        identifier=PhotoIdentifier(catalog=_Catalog(), index=None, embedder=None, matcher=None),
        cache_dir=tmp_path / "bench" / "photo_reads",
    )

    again = await other_process.reread(_message(OLD))

    assert vision.calls == 1 and first == again


@pytest.mark.parametrize("name", ["../fuera.jpg", ".oculta.jpg", ""])
async def test_only_a_plain_file_name_is_read(tmp_path: Path, name: str) -> None:
    vision = _Vision(SCREENSHOT)
    step = _step(tmp_path, vision)

    assert await step.reread({**_message(OLD), "image": name}) is None and vision.calls == 0


async def test_inside_the_case_the_step_only_reads_what_the_run_left(tmp_path: Path) -> None:
    """Candado del sandbox (plan §3.5): un caso no abre conexiones fuera de la
    caja ni escribe fuera de su carpeta. La visión corre al preparar la
    corrida; dentro del caso el paso solo LEE lo que quedó en el banco."""
    await _step(tmp_path, _Vision(SCREENSHOT)).reread(_message(OLD))  # la preparación de la corrida
    media = tmp_path / "bench" / "vault" / SID / "media"
    reads = tmp_path / "bench" / "photo_reads"
    before = sorted(p.name for p in reads.iterdir())
    inside = LabPhotoStep(media_dir=media, vision=None, identifier=None, cache_dir=reads)

    prepared = await inside.reread(_message(OLD))
    (media / "otra.jpg").write_bytes(_jpeg())
    unprepared = await inside.reread({**_message(OLD), "image": "otra.jpg"})

    assert prepared is not None and prepared.product == {"handle": "sacrificio-de-amor", "how": "nombre"}
    assert unprepared is None
    assert sorted(p.name for p in reads.iterdir()) == before


async def test_the_run_reads_every_product_photo_of_the_bench_once(tmp_path: Path) -> None:
    from src.plugins.chats.agent.sales_lab.sandbox.photos import read_bench_photos

    bench = tmp_path / "bench"
    for sid in (SID, "wa_573001234567"):
        (bench / "vault" / sid / "media").mkdir(parents=True)
        (bench / "vault" / sid / "media" / f"{sid[-4:]}.jpg").write_bytes(_jpeg())
    vision = _Vision(SCREENSHOT)
    identifier = PhotoIdentifier(catalog=_Catalog(), index=None, embedder=None, matcher=None)

    first = await read_bench_photos(bench, vision=vision, identifier=identifier)
    again = await read_bench_photos(bench, vision=vision, identifier=identifier)

    assert (first, again) == (2, 0) and vision.calls == 2
    assert sorted(p.name for p in (bench / "photo_reads").iterdir()) == [
        "wa_573001234567__4567.jpg.json", "wa_573009876543__6543.jpg.json",
    ]
