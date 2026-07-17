"""Etiquetas de diseño derivadas del filename de una imagen del catálogo.

Medusa preserva el filename original del upload en la URL del asset
(`leo-01KW2SQSD4....webp`). Cuando el operador nombra las fotos por diseño
(signo, motivo, color), ese nombre ES metadata de producto — esta función la
recupera de forma determinista para que las tools la expongan al LLM y las
captions de WhatsApp la muestren al cliente.

Heurística (en orden):
  1. basename de la URL, URL-decoded, sin extensión
  2. strip del sufijo de id opaco que agrega Medusa (`-<ULID/hash largo>`)
  3. strip de numeración de orden al inicio (``1. aries`` → ``aries``)
  4. strip de sufijo numérico de serie (``cancer2`` → ``cancer``)
  5. separadores ``-``/``_`` → espacio; se preserva el case interno
  6. filenames genéricos (img/foto/image...) o puramente opacos → ``None``
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import unquote, urlparse

# Sufijo de id opaco: ULIDs de Medusa (26 chars) o hashes largos.
_OPAQUE_SUFFIX = re.compile(r"[-_][A-Za-z0-9]{16,}$")
# Token que ES un id opaco completo (ULID/hash/uuid-ish).
_OPAQUE_TOKEN = re.compile(r"^[A-Za-z0-9-]{16,}$")
# Numeración de orden al inicio: "1. ", "2)", "03 - ".
_LEADING_ORDINAL = re.compile(r"^\d+\s*[.)\-]?\s*")
# Sufijo numérico de serie: "cancer2", "leo 3".
_TRAILING_SERIAL = re.compile(r"\s*\d+$")

# Filenames que no nombran un diseño — ruido, no señal.
_GENERIC_NAMES = frozenset({
    "img", "image", "imagen", "foto", "photo", "picture", "pic",
    "thumbnail", "thumb", "screenshot", "captura", "producto", "product",
})

_MAX_LABEL_LEN = 60


def derive_image_label(url: str) -> str | None:
    """Deriva una etiqueta humana del filename de la imagen, o ``None``.

    ``None`` significa "este filename no nombra un diseño" (genérico, id
    opaco, vacío) — el caller NO debe inventar una etiqueta en su lugar.
    """
    if not url:
        return None
    basename = unquote(urlparse(url).path).rsplit("/", 1)[-1]
    if not basename:
        return None
    # Sin extensión
    stem = basename.rsplit(".", 1)[0] if "." in basename else basename
    stem = stem.strip()
    if not stem:
        return None
    # Id opaco puro (el filename ES el ULID) → sin señal
    if _OPAQUE_TOKEN.match(stem) and any(ch.isdigit() for ch in stem):
        stripped = _OPAQUE_SUFFIX.sub("", stem)
        if not stripped or stripped == stem:
            return None
        stem = stripped
    else:
        stem = _OPAQUE_SUFFIX.sub("", stem)
    stem = _LEADING_ORDINAL.sub("", stem)
    stem = stem.replace("-", " ").replace("_", " ")
    stem = re.sub(r"\s+", " ", stem).strip()
    stem = _TRAILING_SERIAL.sub("", stem).strip()
    if not stem or len(stem) > _MAX_LABEL_LEN:
        return None
    if stem.lower() in _GENERIC_NAMES:
        return None
    # Capitalizar solo la primera letra; el case interno se respeta
    # ("Acuario" queda igual, "sagrado rostro" → "Sagrado rostro").
    return stem[0].upper() + stem[1:]


def fold_for_match(value: str) -> str:
    """Normaliza un label / option value para COMPARAR (no para mostrar).

    lowercase + sin tildes (NFD, drop combining marks). El operador escribe
    "Géminis" en la option de Medusa pero el filename del asset va sin tilde
    ("Geminis-*.webp") — la comparación cruda los trata como distintos y la
    variante cae silenciosamente a la foto de portada (premortem PR
    variantes, §4.7). El valor CANÓNICO para display sigue siendo el
    original; esta función es solo la llave de matching.
    """
    decomposed = unicodedata.normalize("NFD", value)
    stripped = "".join(
        ch for ch in decomposed if not unicodedata.combining(ch)
    )
    return stripped.strip().lower()
