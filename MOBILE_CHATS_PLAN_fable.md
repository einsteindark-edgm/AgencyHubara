# Plan: Chats → app móvil Android (Tauri 2) + envío de fotos del operador

> Fecha: 2026-07-08 · Autor: análisis Fable sobre worktree `jolly-feynman-35980e`
> Objetivo: convertir la sección **Chats** del dashboard en una app Android súper
> optimizada, con envío de fotos operador→cliente por WhatsApp que **no se cuelga
> nunca** en el ciclo chatear / devolver al bot / enviar fotos.

> ## Estado de ejecución (2026-07-08) — solo imágenes
> **F1 backend HECHO** (92 tests verdes, lint-imports verde): `upload_media`
> (bytes→media_id de Meta), `persist_outbound_image`, endpoint multipart
> `POST /sessions/{id}/media`, send extendido con `attachment_id`/idempotencia
> por `client_message_id` + guard de ventana 24h (fail-open), `append_human_event(image_url)`.
> **F2 frontend HECHO** (228 unit + arch + tsc + build verdes): compresión
> client-side JPEG (strip EXIF), `uploadHumanMedia` (XHR con progreso), outbox
> optimista (reducer puro + retry que reusa attachment), composer con adjuntar +
> cámara (móvil) + tira de progreso; el render outbound salió gratis por el
> adapter existente. **F3 shell móvil HECHO**: `IS_MOBILE`, `MobileChatsApp`
> (lazy chunk), `MobileChatsLayout` una-columna con back + bottom-sheet + reconexión
> SSE, CSS `.is-mobile` con `100dvh`/safe-area. **F0/F4 config HECHO**: scripts
> `tauri:android:*`, CSP con hosts device/emulador/prod, `bundle.android.minSdkVersion`,
> viewport `viewport-fit=cover`. **Pendiente (requiere Android SDK/NDK, no headless):**
> `tauri android init` + spike en device + build APK + decisión auth Cognito —
> pasos en `frontend_dashboard/MOBILE_ANDROID_RUNBOOK.md`.

---

## 0. Diagnóstico (estado real del código, verificado)

### Lo que YA existe y se reusa tal cual

| Pieza | Dónde | Estado |
|---|---|---|
| Tauri 2.10 con entrypoint mobile | `frontend_dashboard/src-tauri/Cargo.toml:14`, `lib.rs:1` (`mobile_entry_point`), crate-type `cdylib/staticlib` | ✅ listo para Android a nivel deps |
| Plugin chats FSD auto-contenido | `frontend_dashboard/src/plugins/chats/frontend/` (entities session/chat/message/handoff + features inbox/conversation/inspector) | ✅ data-layer limpio y portable |
| Realtime SSE (no polling) | `entities/session/api.ts:82` → `GET /api/dashboard/events`; token por query param (`shared/api/sse.ts:35`) | ✅ funciona en webview |
| Render de imágenes ENTRANTES | `ChatsBubble.tsx:58-72` pinta `imageUrl`; backend sirve `GET /api/dashboard/media/{session_id}/{filename}` (`dashboard.py:411`) | ✅ end-to-end |
| Envío outbound de imagen a WhatsApp | `src/platform/whatsapp/client.py:136` `send_image(ImageOutbound)` — acepta `link` HTTPS **o** `media_id` | ✅ ya implementado |
| Media store en vault + retención | `src/platform/media/store.py` (`persist_inbound_image`, `media_url_for`, `resolve_media_file` anti-traversal, `retention_class`) | ✅ patrón a clonar para outbound |
| Handoff humano completo | `handoff.py`: `intervene:183`, `send_human_message:247`, `return_to_bot:296`; composer deriva modo de `active_agent_route` (`ChatsComposer.tsx:32-48`) | ✅ |
| Auth Cognito JWT fail-closed | `src/platform/auth.py:90` + `main.py:147-158`; frontend Bearer en `shared/api/client.ts:42` | ✅ (no-op en dev) |
| Geometría ventana 24h | `src/platform/whatsapp/window.py`, `send_policy.evaluate_send` | ✅ existe pero **no se consulta** en la ruta del operador |

### Los gaps (lo que hay que construir)

1. **Upload de media a Meta NO existe.** No hay `POST /{phone_id}/media` en el repo; `build_image` acepta `link` XOR `media_id` (`outbound.py:45-55`), nunca bytes. No hay NINGÚN endpoint multipart en todo el API (grep `UploadFile|multipart` = 0).
2. **El mensaje del operador es texto puro.** `SendMessageRequest` solo `text` (`handoff.py:72-74`); `append_human_event` no soporta `image_url` (`session_history/store.py:104-123`).
3. **Guard de ventana 24h ausente en la ruta del operador.** `send_human_message` manda free-form directo sin `evaluate_send`; si la ventana está cerrada, Meta rechaza y el error se traga (`client.py:62-63` loguea sin propagar). Con fotos sería el mismo agujero silencioso.
4. **El layout de chats es desktop puro.** Tres columnas fijas (`.sidebar` 280px, `index.css:254`), **cero** `@media` queries, cero breakpoints Tailwind en `plugins/chats/frontend/`. Inutilizable en 375px.
5. **Target Android a cero.** No hay `src-tauri/gen/android/`, ni scripts npm de tauri, ni plugins nativos (solo `tauri-plugin-log`), y la **CSP `connect-src` está hardcodeada a `localhost:8000`** (`tauri.conf.json`) — bloquea la API desde device/emulador.
6. **Auth Cognito en Android sin resolver.** `react-oidc-context` con redirect en webview: el origin Tauri Android (`http://tauri.localhost`) no es un callback válido para Cognito por default → workstream propio.
7. **Storage = disco del vault** (una EC2). OK para F1; los docstrings ya anticipan S3 (`media/store.py:17-20`) — no lo bloqueamos, lo dejamos detrás del mismo adapter.

### Decisiones de arquitectura (propuestas, con justificación)

- **D1 — Backend→WhatsApp vía `media_id`, no vía link público.** Implementar `upload_media` (`POST /{phone_id}/media`) en el cliente de plataforma y enviar con `ImageOutbound(media_id=...)`. Razón: el endpoint de media del dashboard requiere auth (Meta no podría fetchear el link), el dominio prod es sslip.io interino, y exponer el vault públicamente es un riesgo innecesario. Meta hostea el archivo (media_id válido 30 días, sobra para un send inmediato).
- **D2 — Upload en DOS fases** (multipart primero, send después):
  `POST /sessions/{id}/media` (multipart → persiste en vault + sube a Meta → devuelve `attachment_id`) y luego `POST /sessions/{id}/messages` extendido con `{text?, attachment_id?, client_message_id}`. Razón anti-cuelgue: un retry del *send* nunca re-sube los bytes; el paso pesado (upload) es idempotente y re-intentable por separado; el composer queda libre apenas la foto entra en cola.
- **D3 — Compresión CLIENT-SIDE antes de subir.** `createImageBitmap` (con `imageOrientation:'from-image'`) + canvas → **JPEG** q≈0.8, lado máximo 1600px. Razón: una foto de cámara Android son 3–12 MB; comprimida queda en 150–500 KB → upload 10-20× más rápido en red celular, y WhatsApp igual recomprime del lado de Meta. JPEG (no WebP): WhatsApp trata `image/webp` como sticker; el builder ya normaliza webp→jpeg para links (`outbound.py:63-76`), mantenemos la misma regla. El re-encode por canvas además **stripea EXIF/GPS** (privacidad gratis).
- **D4 — Picker nativo SIN plugin custom como camino primario.** `<input type="file" accept="image/*">` (+ `capture="environment"` para cámara) dispara el picker/cámara nativos vía System WebView en Tauri 2 Android, y funciona idéntico en el browser (paridad dev). Fallback preparado: `@tauri-apps/plugin-dialog` + `plugin-fs` detrás de `IS_DESKTOP`/`IS_MOBILE` si el file input del WebView resulta flaky en el spike (F0 lo valida antes de comprometer).
- **D5 — Un solo codebase, shell móvil por runtime flag.** Nada de app separada: `IS_MOBILE` (detección de plataforma Tauri + viewport) monta `MobileChatsShell` (una columna, stack inbox→conversación, inspector como bottom-sheet) que reusa **las mismas** entities/features del plugin chats. El dashboard desktop no cambia.
- **D6 — Optimistic UI + cola de envío con reintento.** La burbuja aparece al instante con blob URL local y estado `pending → sent | failed(retry)`. `client_message_id` (UUID del cliente) viaja al backend y dedupea reintentos (idempotencia real, no solo UI).
- **D7 — El guard de 24h se agrega a TODA la ruta del operador** (texto y foto) devolviendo 409 con razón legible ("ventana de servicio cerrada — usa plantilla") en vez del fallo silencioso actual. Es fix de un bug latente, no solo parte del feature.

---

## 1. Fases de ejecución

Cada incremento sigue el harness hubara-dev: **test rojo → verde → refactor**, gates
de §8 (`/hubara-gates backend|frontend`) antes de cerrar. Comandos siempre con
`cd hubara_agency &&` / `cd frontend_dashboard &&`.

### F0 — Spike Android (1 día, des-riesga todo lo demás)

Objetivo: APK debug del dashboard actual corriendo contra el backend local, con
verificación empírica de los 4 riesgos técnicos ANTES de escribir features.

- [ ] `cd frontend_dashboard && npx tauri android init` → genera `src-tauri/gen/android/` (commitear).
- [ ] Agregar scripts npm: `tauri`, `tauri:android:dev`, `tauri:android:build`.
- [ ] CSP: parametrizar `connect-src` (dev: `10.0.2.2:8000` emulador + IP LAN para device; ver F4 para prod). `devUrl` del `tauri.conf.json` debe servir con `host: 0.0.0.0` (vite ya lo tiene).
- [ ] **Checklist de validación en device/emulador real:**
  - `<input type="file" accept="image/*">` abre el picker nativo y devuelve el File (riesgo D4).
  - `<input capture="environment">` abre la cámara (y qué permiso pide el WebView).
  - SSE (`/api/dashboard/events`) conecta y sobrevive lock/unlock de pantalla.
  - fetch a la API con Bearer funciona desde el WebView.
  - Comportamiento del teclado con un input fijo abajo (adjustResize vs adjustPan).
- [ ] Documentar hallazgos en este archivo (§4 riesgos) — si el file input falla, D4 flippea al fallback de plugins ANTES de F2.

**Verificación:** APK instala, dashboard renderiza, checklist con evidencia (screenshots/logcat).

### F1 — Backend: foto del operador end-to-end (independiente del móvil; sirve YA al desktop)

Orden TDD por incremento (cada uno = test que falla primero):

- [ ] **F1.1 `upload_media` en el cliente WhatsApp.**
  `src/platform/whatsapp/client.py`: `upload_media(phone_number_id, content: bytes, mime_type) -> media_id` (`POST graph.facebook.com/{v}/{phone_id}/media`, multipart `type+file+messaging_product=whatsapp`). Timeouts httpx explícitos, error propagado (NO el patrón swallow de `send_message` legacy `client.py:62`). Test: mock httpx, asierta multipart shape + media_id + propagación de 4xx/5xx.
- [ ] **F1.2 Media store outbound.**
  `src/platform/media/store.py`: `persist_outbound_image(session_id, content, mime) -> filename` (mismo vault `<session>/media/`, prefijo `out-`, `retention_class` reusa `retention_class_for`). El `GET /api/dashboard/media/...` existente lo sirve sin cambios. Test: persiste, resuelve, anti-traversal.
- [ ] **F1.3 Endpoint multipart `POST /api/dashboard/sessions/{id}/media`.**
  En `src/plugins/chats/api/handoff.py` (mismo router → hereda auth). Valida: route == humano (409), mime ∈ {jpeg, png} (415), size ≤ 5 MB post-compresión (413, límite de imagen de WhatsApp). Hace: `persist_outbound_image` + `upload_media` → devuelve `{attachment_id, media_ref, expires_hint}`. `attachment_id` = registro en `metadata["media_index"]` con el `meta_media_id`. Test: FastAPI TestClient multipart, casos 409/413/415/502-Meta-down.
- [ ] **F1.4 Extender el send del operador.**
  `SendMessageRequest` → `{text: str | None, attachment_id: str | None, client_message_id: str | None}` (al menos uno de text/attachment). Handler:
  1. **Guard 24h**: `evaluate_send(...)`/`is_in_service_window` → 409 con `rationale` legible (aplica también a texto — fix del bug latente).
  2. Idempotencia: si `client_message_id` ya está en el índice de enviados de la sesión → 200 replay (no re-envía).
  3. Con attachment: `send_image(phone_id, to, ImageOutbound(media_id=..., caption=text))` — error de Meta → 502 con detalle (el cliente reintenta el send, NO el upload).
  4. Persistencia: `append_human_event(session_id, text, image_url=media_ref)`.
  Tests: ventana cerrada, replay idempotente, send con caption, Meta 500 → 502.
- [ ] **F1.5 Histórico + clasificador.**
  `session_history/store.py::append_human_event` acepta `image_url?`; `get_session_history` (`dashboard.py:344-366`) ya proyecta `human_message` — asegurar que `image_url` fluye al shape del mensaje (mismo campo que usan los inbound, `contracts.ts:37` del frontend ya lo parsea). SSE `session_updated` ya invalida el detail → la burbuja aparece en otros clientes sin trabajo extra. Test: history round-trip con imagen + snapshot del shape API.

**Verificación F1:** `cd hubara_agency && python3 -m pytest tests/plugins/chats -x` + `/hubara-gates backend` + smoke real: curl multipart → send → foto llega al WhatsApp de prueba y aparece en el histórico del dashboard. (Lección de memoria: **verificar que el backend EMITE, no solo que el schema permite** — el smoke real contra Meta es obligatorio, cf. caso LeadSubmitted.)

### F2 — Frontend: composer con fotos (desktop + web primero, móvil lo hereda)

- [ ] **F2.1 Utilidad de compresión** en `src/shared/lib/image-compress.ts`: `compressImage(file, {maxSide: 1600, quality: 0.8}) -> Blob JPEG` con `createImageBitmap(file, {imageOrientation:'from-image'})`; test vitest con fixture.
- [ ] **F2.2 Entities handoff**: contratos Zod nuevos (`mediaUploadResponseSchema`), `useUploadMediaMutation` con **XMLHttpRequest** (progreso real de upload — fetch no lo expone en WebView) y `useSendHumanMessageMutation` extendido `{text?, attachment_id?, client_message_id}`.
- [ ] **F2.3 Cola de envío optimista** (`features/chats-conversation/model/useOutbox.ts`):
  - Estado por item: `compressing → uploading(pct) → sending → sent | failed`.
  - Burbuja optimista inmediata con `URL.createObjectURL` (revocar al confirmar); al llegar el `session_updated` del SSE, el item local se reconcilia por `client_message_id`.
  - Retry con backoff (reusa `attachment_id` — nunca re-sube bytes); botón reintentar en la burbuja `failed`.
  - El composer NUNCA se bloquea: mandar foto no deshabilita el textarea; múltiples fotos encolan.
  - 409 de ventana cerrada → banner claro en el composer, no burbuja fantasma.
- [ ] **F2.4 UI del composer** (`ChatsComposer.tsx`): botón adjuntar (clip) + botón cámara (solo `IS_MOBILE`), `<input type="file" accept="image/jpeg,image/png" multiple hidden>`, tira de previews con caption opcional antes de enviar, indicador de progreso. `ChatsBubble` ya pinta `imageUrl` — el render de salida es casi gratis (estado pending/failed se agrega como overlay).
- [ ] **F2.5 Tests**: vitest de la cola (transiciones de estado, retry, reconciliación SSE), arch tests FSD verdes, Playwright e2e: adjuntar → progreso → burbuja sent.

**Verificación F2:** `cd frontend_dashboard && npm test && npx tsc -b && npm run build` + `/hubara-gates frontend` + e2e Playwright + prueba visual en el stack Docker (`localhost:5174`, bind-mount de main — coordinar con el operador si estamos en worktree).

### F3 — Shell móvil (una columna, cero regresión desktop)

- [ ] **F3.1 `IS_MOBILE`** en `shared/lib/runtime.ts` (plataforma Tauri android/ios vía `@tauri-apps/api`; fallback `matchMedia(max-width: 768px)` para probar en browser).
- [ ] **F3.2 `MobileChatsShell`** (`src/app/` o `pages/MobileChats.tsx`): monta SOLO el plugin chats (nada de Toolbar/statusbar/registry completo → bundle y memoria mínimos). Navegación por estado: `inbox → conversación` con back (botón + interceptar back-gesture de Android), inspector como bottom-sheet lazy. Reusa `ChatsInbox`/`ChatsConversation`/`ChatsComposer` sin tocarlos (o con props mínimas) — regla FSD intacta.
- [ ] **F3.3 CSS móvil**: clase raíz `.is-mobile` (no `@media` sueltos, para no afectar el desktop de 3 columnas): columnas apiladas, `100dvh`, `env(safe-area-inset-*)` + `viewport-fit=cover` en `index.html`, touch targets ≥ 44px, composer sticky sobre el teclado (validado en F0), `-webkit-overflow-scrolling` en la lista.
- [ ] **F3.4 Ciclo de vida móvil**: al `resume`/`online` → reconectar SSE + `invalidateQueries` (Android mata sockets en background); estado "reconectando" visible. Imágenes con `loading="lazy"` + `decoding="async"`.
- [ ] **F3.5 Performance**: lazy-import del shell móvil, `content-visibility:auto` en burbujas viejas; virtualización de la lista SOLO si el histórico real lo exige (medir primero, no especular).

**Verificación F3:** desktop pixel-identical (Playwright existente verde), shell móvil navegable en browser 375px + en el APK debug.

### F4 — Empaquetado Android productivo

- [ ] **F4.1 Config**: `bundle > android` (minSdk 24+), identifier `com.hubara.dashboard` ya válido, iconos adaptive, splash.
- [ ] **F4.2 CSP/API por entorno**: `connect-src` desde env de build (`TAURI_*` ya está en `envPrefix`); build prod apunta a `https://98-88-237-207.sslip.io` (y el dominio definitivo cuando exista). El vault de media se sirve por la misma API → sin orígenes extra.
- [ ] **F4.3 Auth Cognito en Android** (workstream con decisión abierta — ver §3):
  opción A: hosted UI dentro del WebView con callback al origin de la app; opción B: system browser + deep link (`tauri-plugin-deep-link`, callback `hubara://auth`); opción C (interim, menor fricción): pantalla de login propia contra `InitiateAuth` de Cognito (ROPC) solo para la app interna de operadores. Decidir en F0/F4 con una prueba de 2 horas de la opción A.
  - `AuthGate` ya centraliza el token (`auth-token.ts`) → cualquier opción encaja sin tocar el data-layer.
- [ ] **F4.4 Permisos**: el Photo Picker moderno no requiere permiso; `capture` puede requerir `android.permission.CAMERA` en el manifest generado — validado en F0.
- [ ] **F4.5 Firma + build AAB/APK**, keystore fuera del repo, script `tauri:android:build`.
- [ ] **F4.6 Smoke E2E en device real contra prod**: login → inbox en vivo (SSE) → intervenir → texto → **foto con red celular (no wifi)** → devolver al bot → verificar histórico + WhatsApp del cliente de prueba.

### F5 — Hardening (post-launch, priorizar con data)

- Telemetría OTel del funnel de upload (compress ms, upload ms, send ms, fallos por tipo) — el frontend ya tiene OTel opcional.
- Outbox persistente (IndexedDB) para sobrevivir kill de la app con fotos en cola.
- Thumbnails server-side si los históricos con muchas fotos pesan.
- Migración del vault media a S3 detrás del adapter existente (ya anticipado en `store.py:17-20`).
- Ratchet: test de presupuesto de bundle móvil (como `test_prompt_budget`).

---

## 2. Secuencia y paralelismo

```
F0 (spike, 1 día) ──┬── F1 backend (2-3 días) ──┐
                    └── F3 shell móvil (2 días) ─┼── F2 composer (2-3 días, necesita F1.3/F1.4)
                                                 └── F4 empaquetado (2 días, necesita F0+F3)
                                                        └── F5 hardening
```

- F1 y F3 son paralelizables total (backend vs CSS/shell, cero archivos compartidos) → aptos para el pipeline multi-plugin.
- F2 depende de los contratos de F1.3/F1.4 (congelarlos primero: shapes de request/response en la HU).
- **Valor incremental**: al cerrar F1+F2 el envío de fotos ya funciona en el dashboard DESKTOP actual — el móvil (F3+F4) no bloquea ese valor.

## 3. Decisiones abiertas (resolver con el operador)

1. **Auth Android (F4.3)**: ¿hosted UI en webview, deep link + system browser, o login propio ROPC para la app interna? Recomendación: probar A en el spike; si Cognito rechaza el origin del WebView, ir a C como interim (app interna de operadores) y B como definitivo.
2. **¿Solo imágenes o también documentos/video?** El plan cubre imagen (jpeg/png). `send_document`/`send_video` ya existen en el cliente (`client.py:156,166`) — extender después es sumar mime types al mismo camino, no rediseñar.
3. **Dominio definitivo**: la CSP prod y el callback de Cognito quedan mejor cuando exista dominio propio (hoy sslip.io interino).
4. **Distribución del APK**: ¿sideload interno (MDM/link directo) o Play Store? Afecta firma y cadencia de releases, no el código.

## 4. Riesgos y mitigaciones

| Riesgo | Prob. | Mitigación |
|---|---|---|
| File input / cámara flaky en WebView Android | Media | F0 lo valida ANTES de construir; fallback `plugin-dialog`+`plugin-fs` detrás de la misma interfaz del composer |
| Cognito no acepta el origin del WebView | Media | 3 opciones en F4.3; el token store ya está desacoplado |
| Ventana 24h cerrada → operador no puede mandar la foto | Segura (pasa hoy con texto, silencioso) | D7: 409 con razón legible; futuro: ofrecer template utility (cf. ETA window-aware) |
| Upload lento/colgado en celular | Media | D2 dos fases + D3 compresión (10-20× menos bytes) + XHR con progreso + timeout + retry sin re-subir |
| Doble envío por retry | Media | `client_message_id` idempotente server-side (F1.4.2), no solo dedupe de UI |
| Meta 5MB / tipos de imagen | Baja | Compresión client-side + validación 413/415 server-side; JPEG siempre (webp=sticker) |
| Regresión del dashboard desktop | Baja | Shell móvil aditivo (clase raíz `.is-mobile`, entry propio); Playwright desktop existente como ratchet |
| SSE muerto tras background | Alta en Android | F3.4 reconexión en resume/online + refetch |
| Deploy stale / vault en una EC2 | Conocido | Smoke real post-deploy (memoria: rebuild container); S3 en F5 |

## 5. Definition of Done

- Operador en Android: abre app → login → ve inbox en vivo → interviene → manda texto y fotos (con progreso, retry, sin congelar UI) → devuelve al bot — todo en red celular.
- La foto llega al WhatsApp real del cliente y queda en el histórico (visible también desde el dashboard desktop y otros clientes vía SSE).
- Ventana 24h cerrada = error claro y accionable, nunca fallo silencioso.
- Gates verdes: `/hubara-gates all`, Playwright desktop sin regresión, smoke E2E device real documentado.
