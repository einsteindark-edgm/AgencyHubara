"""Labels de diseño derivados del filename de las imágenes del catálogo.

Caso real (sesión wa_573125671604, pedido order_01KX29MMREV14JART3EYR1AAXZ):
las fotos del Duo Zodiacal en Medusa YA llevan el signo en el filename
(`leo-01KW2SQSD4....webp`, `Acuario-01KW...webp`) pero nada del sistema las
leía — el LLM respondió "no tengo variantes por cada signo" y el pedido se
registró con un diseño adivinado. `derive_image_label` convierte esa URL en
una etiqueta humana determinista que las tools exponen al LLM.
"""
from __future__ import annotations

import pytest

from src.sdk.mediakit import derive_image_label


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # Casos reales del snapshot prod del duo-zodiacal
        (
            "https://assets.hubara.com.co/leo-01KW2SQSD4RP0KSM9HTJ38QPEF.webp",
            "Leo",
        ),
        (
            "https://assets.hubara.com.co/Acuario-01KW2SQN75E2KRYVDPA2Z42MHM.webp",
            "Acuario",
        ),
        # URL-encoded + numeración de orden ("1. aries")
        (
            "https://assets.hubara.com.co/1.%20aries-01KW2SQMAPCTQD74M3HK8DQ3AB.webp",
            "Aries",
        ),
        # Sufijo numérico de "segunda foto del mismo diseño"
        (
            "https://assets.hubara.com.co/cancer2-01KW2SQPZTRRZ9B27G9KDXTSGG.webp",
            "Cancer",
        ),
        # Multi-palabra con guiones
        (
            "https://assets.hubara.com.co/sagrado-rostro-01KW2SQXPD5945Q2XE1T4XBRYM.webp",
            "Sagrado rostro",
        ),
    ],
)
def test_derive_image_label_from_real_catalog_urls(url: str, expected: str):
    assert derive_image_label(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        # Filenames genéricos: no aportan diseño — None, no ruido
        "https://assets.hubara.com.co/img1.webp",
        "https://assets.hubara.com.co/image-01KW2SQXPD5945Q2XE1T4XBRYM.webp",
        "https://assets.hubara.com.co/foto2.jpg",
        # Filename que es SOLO un id opaco
        "https://assets.hubara.com.co/01KW2SQXPD5945Q2XE1T4XBRYM.webp",
        # Inputs degenerados
        "",
        "https://assets.hubara.com.co/",
    ],
)
def test_derive_image_label_generic_or_opaque_returns_none(url: str):
    assert derive_image_label(url) is None
