"""Constantes cross-cutting (queues, rutas, prefijos de session).

Antes vivian dispersas como string literals en service.py / worker.py / tools.
Centralizar aqui evita typos y facilita renombres.
"""
from __future__ import annotations

# Task queues
SALES_QUEUE = "queue-sales-agent"
REMARKETING_QUEUE = "queue-remarketing-agent"
CATALOG_SYNC_QUEUE = "queue-catalog-sync"

# Rutas de conversacion (active_route en metadata.json)
ROUTE_VENTAS = "ventas"
ROUTE_REMARKETING = "remarketing"

# Prefijos de session_id por canal
WHATSAPP_SESSION_PREFIX = "wa_"
