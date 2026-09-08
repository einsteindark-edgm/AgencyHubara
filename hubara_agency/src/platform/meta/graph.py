"""Graph API de Meta — host y versión ÚNICOS para todo el backend.

Por qué existe (auditoría CAPI 2026-09-08): había 6 módulos con su propia
versión hardcodeada (v18/v21/v23/v25). La v18 de CAPI venció el 2026-01-26 y
seguía "funcionando" solo porque Meta auto-promociona las versiones vencidas
a la más vieja viva — un comportamiento silencioso del que no queremos
depender. Cada cliente (WhatsApp, CAPI, catálogo, media, ads) construye ahora
sus URLs con :func:`graph_url`; la guarda
``tests/platform/test_meta_graph_central.py`` impide que vuelva a aparecer
el host o una versión propia fuera de este módulo.

Política de versión:
  * Una sola versión para toda la superficie (Meta versiona el Graph API
    completo, incluida la WhatsApp Cloud API y la Conversions API).
  * Default = la última que este código fue probado contra. Meta mantiene
    cada versión ~2 años; subirla es un cambio deliberado (PR + smoke), no
    un side effect de otro desarrollo.
  * Override por entorno ``META_GRAPH_API_VERSION`` (ej. ``v24.0``) para
    volver atrás en prod sin rebuild si una versión nueva rompe algo.

Este módulo es stdlib-puro a propósito: lo re-exporta ``src.sdk.connectorkit``
(lazy) y el guard ``test_sdk_lazy_surface`` exige que el kit no arrastre
vendors al importarse.
"""
from __future__ import annotations

import os
import re

#: Host del Graph API. Nunca lo escribas en otro módulo — usá ``graph_url``.
META_GRAPH_BASE_URL: str = "https://graph.facebook.com"

#: Versión por defecto (v25.0 — publicada 2026-02-18, viva hasta ~2028).
_DEFAULT_VERSION: str = "v25.0"

_VERSION_RE = re.compile(r"^v\d{1,3}\.\d{1,2}$")


def resolve_graph_api_version() -> str:
    """Versión efectiva: ``META_GRAPH_API_VERSION`` del entorno o el default.

    Valida el formato ``vNN.N`` para que un typo en SSM no genere URLs
    inválidas que Meta responde con 400 en TODOS los clientes a la vez.
    """
    raw = (os.getenv("META_GRAPH_API_VERSION") or "").strip()
    if not raw:
        return _DEFAULT_VERSION
    if not _VERSION_RE.match(raw):
        raise ValueError(
            f"META_GRAPH_API_VERSION={raw!r} no tiene formato vNN.N (ej. 'v25.0')"
        )
    return raw


#: Versión resuelta al importar (una vez por proceso, como el resto de config).
META_GRAPH_API_VERSION: str = resolve_graph_api_version()


def graph_url(*segments: str, version: str | None = None) -> str:
    """Arma ``https://graph.facebook.com/<version>/<seg1>/<seg2>...``.

    Los segmentos se unen con ``/`` y se les recortan barras sobrantes; se
    admiten placeholders ``{...}`` para constantes-template (``"{phone_number_id}"``)
    que el caller formatea después. Sin segmentos devuelve la raíz versionada
    (sin barra final). Un segmento vacío es un bug del caller → ``ValueError``.
    """
    v = version or META_GRAPH_API_VERSION
    parts: list[str] = []
    for seg in segments:
        cleaned = seg.strip().strip("/")
        if not cleaned:
            raise ValueError(f"graph_url: segmento vacío en {segments!r}")
        parts.append(cleaned)
    root = f"{META_GRAPH_BASE_URL}/{v}"
    return root if not parts else f"{root}/{'/'.join(parts)}"


__all__ = [
    "META_GRAPH_BASE_URL",
    "META_GRAPH_API_VERSION",
    "graph_url",
    "resolve_graph_api_version",
]
