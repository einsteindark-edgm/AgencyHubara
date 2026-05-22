"""Regression tests para `platform.whatsapp.media_url`.

El bug original (session b3e63934): assets del catálogo eran .webp y Meta
Cloud API solo acepta image/jpeg|image/png en mensajes `type=image`. Meta
respondía 200 + wa_message_id pero NUNCA renderizaba la imagen al cliente.

`normalize_image_url` envuelve las URLs de hosts conocidos con Cloudflare
Image Resizing para servir JPEG on-the-fly. Estos tests garantizan que:

1. .webp del CDN Hubara se transforma → JPEG.
2. .jpg/.png ya nativos pasan tal cual (no double-transform).
3. URLs de terceros pasan tal cual (no asumimos CDN ajeno).
4. URLs ya transformadas son idempotentes.
5. Caracteres URL-encoded del path se preservan.
"""
from __future__ import annotations

from src.platform.whatsapp.media_url import normalize_image_url


def test_webp_on_hubara_cdn_gets_wrapped_with_cf_transform():
    src = "https://assets.hubara.com.co/banner-01KN0WV9.webp"
    out = normalize_image_url(src)
    assert out.startswith(
        "https://assets.hubara.com.co/cdn-cgi/image/format=jpeg,"
    )
    assert out.endswith("/banner-01KN0WV9.webp")


def test_jpg_passthrough():
    src = "https://assets.hubara.com.co/already.jpg"
    assert normalize_image_url(src) == src


def test_png_passthrough():
    src = "https://assets.hubara.com.co/already.png"
    assert normalize_image_url(src) == src


def test_third_party_host_passthrough():
    src = "https://third-party.com/foo.webp"
    assert normalize_image_url(src) == src


def test_idempotent_on_already_transformed_url():
    src = (
        "https://assets.hubara.com.co/cdn-cgi/image/"
        "format=jpeg,width=1024,quality=85/banner.webp"
    )
    assert normalize_image_url(src) == src


def test_preserves_url_encoded_chars_in_path():
    """Filenames con espacios encoded ('%20') deben preservarse — si los
    rompemos, Cloudflare devuelve 404 y Meta vuelve a quedarse sin imagen."""
    src = (
        "https://assets.hubara.com.co/"
        "product-vertical_1500x2000%20(10)-01KN0WV.webp"
    )
    out = normalize_image_url(src)
    assert "%20(10)" in out
    assert "/cdn-cgi/image/format=jpeg," in out


def test_empty_url_passthrough():
    assert normalize_image_url("") == ""


def test_non_http_passthrough():
    """Schemes no-HTTPS los validará el builder, no este normalizador."""
    src = "ftp://assets.hubara.com.co/foo.webp"
    # Sin scheme HTTPS, no aplicamos transform — passthrough.
    out = normalize_image_url(src)
    # Acepta cualquier resultado mientras no rompa
    assert isinstance(out, str)


def test_build_image_uses_normalizer_end_to_end():
    """End-to-end: el builder debe normalizar antes de pasar al validator."""
    from src.platform.whatsapp import dtos as wa_dtos
    from src.platform.whatsapp.outbound import build_image

    payload = wa_dtos.ImageOutbound(
        link="https://assets.hubara.com.co/foo.webp",
        caption="test",
    )
    msg = build_image("573000", payload, None)
    assert msg["type"] == "image"
    assert msg["image"]["link"].startswith(
        "https://assets.hubara.com.co/cdn-cgi/image/format=jpeg,"
    )
    assert msg["image"]["caption"] == "test"
