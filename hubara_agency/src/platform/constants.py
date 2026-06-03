"""Constantes cross-plugin (rutas, prefijos de session).

NO declarar task queues aquí. Las queues son **per-worker** y viven en el
manifest del plugin (``agent.workers[].task_queue``). Para resolver una queue
desde código, usar ``src.platform.plugin_manifest.get_task_queue(plugin_id,
worker_name)``.

Por qué: pre-PR11 las queues vivían acá como `SALES_QUEUE`, `REMARKETING_QUEUE`,
`CATALOG_SYNC_QUEUE`. Agregar un worker nuevo requería editar este archivo —
conflict de merge cuando 2 PRs (Archon agents) trabajan en paralelo. Post-PR11,
el manifest del plugin es la single source of truth y agregar workers nuevos
NO toca este archivo.

Las constantes que **sí** viven acá son cross-plugin (no per-plugin):
rutas y prefijos que múltiples plugins necesitan reconocer al leer/escribir
metadata.
"""
from __future__ import annotations

# Rutas de conversacion (active_route en metadata.json)
ROUTE_VENTAS = "ventas"
ROUTE_REMARKETING = "remarketing"
# Ruta "humano": el LLM no responde mas; el cliente queda en el inbox del humano.
# Se setea via `EscalateToHumanTool`. `LoadOrStartSalesSession` chequea esta ruta
# antes de signalear al workflow — si match, solo persiste el mensaje y vuelve.
ROUTE_HUMANO = "humano"
# Ruta "eta": la conversacion la maneja el Agente ETA (notificaciones de estado
# de pedido). Se setea cuando un pedido entra en `preparing` (orders API emite
# `OrderStageChangedEvent` → dispatcher arranca `HubaraEtaSessionWorkflow`, cuyo
# bootstrap marca `active_route=eta`). `LoadOrStartSalesSession` rutea los
# inbounds del cliente al workflow `eta-{session_id}` en vez de a Sales. El
# Agente ETA puede devolver el control a Sales (transfer_to_sales_agent →
# active_route=ventas) o escalar a humano (escalate_to_human → active_route=humano).
ROUTE_ETA = "eta"

# Prefijos de session_id por canal
WHATSAPP_SESSION_PREFIX = "wa_"
