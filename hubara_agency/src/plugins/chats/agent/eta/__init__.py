"""Sub-agente ETA del plugin ``chats``.

Tercer agente conversacional del plugin (hermano de ``sales`` y
``remarketing``). Su único trabajo es **notificar al cliente los cambios de
estado de su pedido** por WhatsApp (desde ``preparing`` en adelante) y, ante
preguntas que se salen de su rol, **escalar a un humano** o transferir a Ventas.

Vive bajo ``chats`` (y no en un plugin propio) porque reusa el webhook de
WhatsApp y el ruteo inbound (``LoadOrStartSalesSession``) del plugin ``chats``;
rutear inbound a un agente de otro plugin sería un import cross-plugin
prohibido (R-DIP #10).

Se activa de forma declarativa (ADR-2026-05-20): la orders API emite
``OrderStageChangedEvent`` y el dispatcher arranca/signalea
``HubaraEtaSessionWorkflow`` según el manifest.
"""
