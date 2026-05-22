"""Normalización de URLs de imagen para WhatsApp Cloud API.

Por qué existe:
  * Meta Cloud API solo acepta `image/jpeg` y `image/png` para mensajes
    `image` (los `webp` quedan reservados para stickers).
  * El catálogo Hubara guarda los assets en `.webp` (mejor compresión, mejor
    perf en frontend). Si pasamos esos URLs directo a `send_image`, Meta
    acepta la request con 200 + wa_message_id pero **nunca renderiza** la
    imagen al cliente. El operador ve "imagen enviada" en logs pero el
    cliente no recibe nada.
  * `assets.hubara.com.co` está detrás de Cloudflare y expone el feature
    "Image Resizing" (`/cdn-cgi/image/format=jpeg/...`) que sirve un JPEG
    on-the-fly. Verificado: la URL transformada devuelve `content-type: image/jpeg`.

Estrategia:
  * Detectamos URLs de `assets.hubara.com.co` (configurable vía env).
  * Si la extensión es `.webp` (o el path no termina en jpg/png), wrappeamos
    con el path de Cloudflare Images.
  * URLs de otros hosts: passthrough (no asumimos transform CDN ajeno).
  * Si el env `WHATSAPP_DISABLE_IMAGE_NORMALIZE=1`, passthrough universal.

Ubicación (DEHA): vive en `platform/whatsapp/` porque la restricción de
formato es de la API de Meta, no del dominio del catálogo. Cualquier
caller que mande `send_image` se beneficia automáticamente.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

# Hosts conocidos donde aplicamos Cloudflare Image Resizing.
# Solo activamos el transform si reconocemos el host (no queremos romper
# URLs de terceros que no soportan `cdn-cgi/image`).
_CF_IMAGES_HOSTS = frozenset(
    (os.getenv("WHATSAPP_CF_IMAGES_HOSTS") or "assets.hubara.com.co")
    .lower()
    .split(",")
)

# Formato target. JPEG por defecto (mejor compatibilidad y compresión que PNG).
# Configurable por si más adelante queremos forzar PNG para transparencia.
_TARGET_FORMAT = os.getenv("WHATSAPP_IMAGE_TARGET_FORMAT") or "jpeg"

# Width cap. WhatsApp re-comprime al recibir, así que no tiene sentido
# mandar 4K. 1024 da buen balance entre calidad y peso (Meta cap = 5MB).
_TARGET_WIDTH = int(os.getenv("WHATSAPP_IMAGE_TARGET_WIDTH") or "1024")

# Quality. 85 es estándar para JPEG en web.
_TARGET_QUALITY = int(os.getenv("WHATSAPP_IMAGE_TARGET_QUALITY") or "85")

# Kill switch.
_DISABLED = os.getenv("WHATSAPP_DISABLE_IMAGE_NORMALIZE", "").lower() in {"1", "true", "yes"}

# Formatos que WhatsApp acepta para mensajes image. Mantenemos passthrough.
_WA_NATIVE_EXTS = (".jpg", ".jpeg", ".png")


def normalize_image_url(url: str) -> str:
    """Devuelve una URL que WhatsApp Cloud API puede renderizar.

    * URL no-HTTPS: la devuelve tal cual (el caller la rechazará luego —
      esto es solo normalize de formato, no de transporte).
    * URL fuera de hosts conocidos: passthrough.
    * URL con extensión .jpg/.jpeg/.png y host conocido: passthrough.
    * URL .webp (o sin extensión clara) en host conocido: wrappea con
      `https://<host>/cdn-cgi/image/format=jpeg,width=1024,quality=85/<original_path_y_query>`.

    Idempotente: si ya está bajo `/cdn-cgi/image/...`, devuelve sin tocar.
    """
    if _DISABLED or not url:
        return url
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    host = (parsed.hostname or "").lower()
    if not host or host not in _CF_IMAGES_HOSTS:
        return url
    # Ya está transformado — idempotencia.
    if parsed.path.startswith("/cdn-cgi/image/"):
        return url
    path_lower = parsed.path.lower()
    if path_lower.endswith(_WA_NATIVE_EXTS):
        # Ya es un formato nativo de WhatsApp, no necesitamos transform.
        return url
    # Construir el path transformado. Preservamos query string original (firmas, etc.).
    transform = f"format={_TARGET_FORMAT},width={_TARGET_WIDTH},quality={_TARGET_QUALITY}"
    # Mantenemos el path EXACTO original (incluyendo % encoding) para que
    # Cloudflare sirva el mismo asset. parsed.path ya está decodificado en algunos
    # casos, así que reconstruimos desde `url` quitando `scheme://host`.
    # Cuidado: queremos lo que vino después del host (path + query + fragment).
    original_pos = url.find(parsed.netloc)
    if original_pos < 0:
        return url
    after_host = url[original_pos + len(parsed.netloc):]
    if not after_host.startswith("/"):
        after_host = "/" + after_host
    return f"{parsed.scheme}://{parsed.netloc}/cdn-cgi/image/{transform}{after_host}"
