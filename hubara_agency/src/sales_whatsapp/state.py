"""Shim de backward-compat — la clase se movio a `src.platform.state`.

Por que existe el shim: `FilesystemMetadataStore` vivia aqui cuando solo
`sales_whatsapp` lo usaba. Cuando `dashboard/handoff.py` empezo a leer/escribir
el mismo `metadata.json` (handoff humano), la canonical location pasa a ser
`platform/` — regla DEHA multi-agent: estado compartido entre componentes
viaja por `platform/`, no por imports cross-agent. La regla mas estricta
("R-DIP, agentes independientes") prohibe `dashboard → sales_whatsapp`.

Mantenemos el re-export aqui para no romper imports historicos y tests
externos. Una iteracion futura puede borrar este archivo cuando todos los
callers migren a `from src.platform.state import ...`.
"""
from __future__ import annotations

from src.platform.state import FilesystemMetadataStore

__all__ = ["FilesystemMetadataStore"]
