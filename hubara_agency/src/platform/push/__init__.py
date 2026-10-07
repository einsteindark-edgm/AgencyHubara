"""Platform — avisos push a la App Operador (Firebase Cloud Messaging, API HTTP v1).

* `ports.PushPort` — el puerto (Protocol); `send` nunca lanza.
* `ports.PushMessage` / `PushOutcome` / `FirebaseClientOptions` — DTOs.
* `adapters.fcm_v1.FcmV1PushAdapter` — FCM de verdad (JWT de la cuenta de servicio → OAuth → `messages:send`).
* `adapters.fake.FakePushAdapter` / `adapters.null.NullPushAdapter` — falso y nulo.
* `composition.get_push_port` — la fábrica (`PUSH_PROVIDER`).

Los plugins lo piden por `src.sdk.connectorkit` (P-28). Doc: `docs/_sdk/07-connectorkit.md` §Avisos push.
"""
