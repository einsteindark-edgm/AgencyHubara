"""Cuánto lee una consulta de TODO el catálogo (premortem del 2026-10-09).

Las lecturas que validan o arman vocabulario contra el catálogo completo
(`set_order_slot`, el selector, los botones rápidos, la lista de opciones de
`send_reply`, la guarda de listas) pedían 30 productos y el catálogo ya tenía
31 el 2026-10-06: el producto 31 no se validaba ni traía sus opciones únicas.
Es la copia local del catálogo (el snapshot), no la API de Meta: leerla
entera es barato. Mostrar productos al cliente tiene su propio tope (Meta:
30 por lista, `whatsapp.limits`).
"""
from __future__ import annotations

#: Tope de la lectura del catálogo completo de la copia local.
WHOLE_CATALOG = 10_000
