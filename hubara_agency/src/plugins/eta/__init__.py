"""Plugin `eta` — Agente de seguimiento de envíos (notificaciones de estado).

Plugin self-contained (extraído de `chats` — PLUGIN_CONTRACT.md §5.2). Tres capas:

- ``agent/``   — workspace + workflow + activities del sub-agente ETA.
- ``workers/`` — worker Temporal ``eta`` (queue ``queue-eta-agent``).
- ``api/``     — router FastAPI (``/api/eta/tracked-orders``) que sirve la sección
  ETA del dashboard derivando los pedidos en fulfillment del order query port
  (platform) + el ``metadata.eta_tracking`` per-sesión del vault.

Es un TARGET de orquestación declarativa: el dispatcher lo arranca/signalea
cuando `orders` emite ``OrderStageChangedEvent`` (ver las transitions del
manifest de `orders`, target_plugin=eta). NO importa de ningún plugin sibling —
solo `exoclaw_temporal` (session runtime) + `src.platform.*` (ports compartidos).

Manifest: `frontend_dashboard/src/plugins/eta/plugin.yaml`.
"""
