"""Las tools del asesor de ventas que SOLO LEEN (turno 1 de …7392, 2026-10-08).

No le muestran nada al cliente ni cambian el estado: le devuelven un dato al
modelo (el catálogo, una ficha, las categorías, las promociones, el estado del
pedido, una guía). Dos cosas se apoyan en eso:

  * el reinicio del turno (el cliente escribió mientras el modelo pensaba)
    conserva sus resultados en vez de volver a consultarlos;
  * la nota del contrato distingue una fila que pide un DATO (una de estas)
    de una que pide MOSTRARLE algo al cliente.

Cada clase lo declara también (`read_only = True`); una prueba exige que esta
lista y las clases digan lo mismo. Módulo puro: lo importa el workflow.
"""
from __future__ import annotations

READ_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "search_products",
        "get_product_by_handle",
        "list_categories",
        "list_promotions",
        "check_order_status",
        "load_skill",
    }
)
