"""Web cart capability — lectura del carrito web (Store API de Medusa v2).

Paquete espejo de `src/platform/orders/`: `port.py` (Protocol + DTOs +
NullWebCartReader), `medusa_store.py` (adapter vendor) y `composition.py`
(factory `get_web_cart_reader`). La superficie pública para plugins vive en
`src.sdk.connectorkit` (P-28/P-31: ningún plugin importa este paquete).
"""
