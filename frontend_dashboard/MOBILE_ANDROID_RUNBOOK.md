# Runbook: app Android (Tauri 2) de la sección Chats

> Estado del código (este PR): **listo para `tauri android init`**. El árbol
> Tauri ya es 2.10 con entrypoint mobile (`src-tauri/src/lib.rs`), `crate-type`
> con `cdylib`/`staticlib`, CSP con los hosts de device/emulador/prod, scripts
> npm cableados, y el frontend arranca en modo móvil (una columna) por `IS_MOBILE`.
> Lo que **no** se puede correr headless (sin Android SDK/NDK) queda acá como
> pasos manuales — el spike F0 se corre en una máquina con el toolchain.

## Qué ya quedó hecho en código

- `frontend_dashboard/src/main.tsx` → si `IS_MOBILE`, monta `MobileChatsApp`
  (solo chats, sin el shell de escritorio) en vez de `Dashboard`.
- `src/pages/MobileChats.tsx` → shell móvil; lazy-carga el plugin chats (chunk
  aparte → arranque mínimo).
- `src/plugins/chats/frontend/MobileChatsLayout.tsx` → una columna:
  inbox → conversación (botón atrás) → inspector como bottom-sheet; reconexión
  SSE en `useInvalidateOnReconnect` (Android mata el socket en background).
- Composer con **adjuntar foto** (`<input type=file accept=image/jpeg,png>`) y
  **cámara** (`capture=environment`, solo móvil) → outbox optimista (comprime →
  sube → envía) que **nunca bloquea** el chat.
- `index.html` → `viewport-fit=cover` + `maximum-scale` + `theme-color`.
- `src/index.css` → bloque `.is-mobile` / `.mobile-*` con `100dvh` y
  `env(safe-area-inset-*)`.
- `src-tauri/tauri.conf.json` → `bundle.android.minSdkVersion: 24` + `connect-src`
  con `http://10.0.2.2:8000` (emulador), `https://98-88-237-207.sslip.io` (prod).
- `package.json` → scripts `tauri`, `tauri:android:init|dev|build`.

## Prerequisitos (una vez, en la máquina de build)

```bash
# 1. Rust + targets Android
rustup target add aarch64-linux-android armv7-linux-androideabi \
  i686-linux-android x86_64-linux-android

# 2. Android Studio (o command-line tools) → instalar:
#    - Android SDK Platform 34
#    - NDK (side-by-side)
#    - Android SDK Build-Tools
# 3. Exportar env (ajustar rutas):
export ANDROID_HOME="$HOME/Library/Android/sdk"
export NDK_HOME="$ANDROID_HOME/ndk/<version>"
export JAVA_HOME="$(/usr/libexec/java_home -v 17)"   # JDK 17
```

## F0 — Spike (des-riesga todo lo demás, ~1 día)

```bash
cd frontend_dashboard
npm install
npm run tauri:android:init      # genera src-tauri/gen/android/  (commitear)
npm run tauri:android:dev       # compila + instala en emulador/device
```

**Checklist de validación empírica (en device/emulador real):**

- [ ] `<input type=file accept=image/*>` abre el picker nativo y devuelve el File.
- [ ] `<input capture=environment>` abre la cámara (y qué permiso pide el WebView).
      Si Android exige `CAMERA`, agregarlo en el `AndroidManifest.xml` generado
      (`src-tauri/gen/android/app/src/main/AndroidManifest.xml`).
- [ ] SSE (`/api/dashboard/events`) conecta y sobrevive lock/unlock de pantalla
      (la reconexión ya invalida las queries).
- [ ] `fetch`/XHR con `Authorization: Bearer` funciona desde el WebView contra
      `http://10.0.2.2:8000` (emulador) o la IP LAN del backend (device).
- [ ] Teclado: el composer sticky no queda tapado (Android `adjustResize`).
- [ ] Subir una foto real en red **celular** (no wifi): progreso visible, sin
      cuelgue; la burbuja optimista se reconcilia con la del servidor.

> Si el file input del WebView resultara flaky, el fallback es
> `@tauri-apps/plugin-dialog` + `plugin-fs` detrás de `IS_MOBILE` en el composer
> (misma interfaz `onPickFiles`) — no requiere rediseño.

## F4 — Build productivo

```bash
# APK/AAB de release (firmar con keystore fuera del repo)
VITE_API_URL=https://98-88-237-207.sslip.io npm run tauri:android:build
# artefactos en src-tauri/gen/android/app/build/outputs/
```

Notas:
- `connect-src` de la CSP ya incluye el host prod; si cambia el dominio,
  actualizar `src-tauri/tauri.conf.json` (`csp` **y** `devCsp`).
- `VITE_API_URL` de build fija a qué backend pega el bundle (envPrefix ya
  expone `VITE_*`/`TAURI_*`).

## Notificaciones de handoff (implementado — fase 1, sin tienda)

**Qué hace:** cuando una conversación pasa a manos del humano (el bot escala
con `escalate_to_human`, o alguien interviene desde otro dispositivo), la app
dispara una **notificación del sistema** ("Conversación asignada — {cliente}
necesita atención humana").

**Cómo funciona:** el inbox ya es tiempo-real por SSE (sampler del vault →
`session_updated` → refetch). `useHandoffNotifications`
(`src/plugins/chats/frontend/features/chats-inbox/model/handoff-notify.ts`)
observa esa misma data y detecta transiciones `bot → humano` (detector puro
`diffNewHandoffs`, testeado). Al detectar una y si la app NO está en foco,
notifica vía `tauri-plugin-notification` (Android/desktop) o Web Notification
(browser). Montado en el shell móvil Y en el desktop.

Reglas anti-ruido: primera carga no notifica (sería una ráfaga de handoffs
viejos al abrir la app); app visible y enfocada no notifica (la fila ya se
pintó de HUMANO sola).

**Permisos:** Android 13+ pide `POST_NOTIFICATIONS` en runtime — el primer
handoff dispara el diálogo del sistema (el plugin lo maneja). Aceptalo en la
prueba.

**Limitación conocida (aceptada para fase 1):** con la app CERRADA (proceso
muerto) no hay SSE → no hay notificación. La app en background reciente sí
suele recibirla (el WebView vive unos minutos). **Fase 2 = FCM** (push real
con app muerta): Firebase project + token FCM por device + el backend manda
el push en `_append_status(tag=HUMANO)`; no requiere Play Store (FCM funciona
en APKs sideloaded), pero sí un plugin Kotlin custom o el plugin FCM de la
comunidad. Decidir cuando la fase 1 se quede corta en la operación real.

## Login (implementado) — pantalla nativa email + contraseña

La app móvil NO usa el redirect OIDC del web (no funciona en el WebView: el
origin `http://tauri.localhost` no es un callback válido en Cognito). Usa un
**formulario nativo email+contraseña dentro de la app** que autentica contra
Cognito con el flujo `USER_PASSWORD_AUTH` (`InitiateAuth`), sin navegador ni
deep links.

Qué incluye (todo en `src/app/providers/` + `src/shared/api/cognito.ts`):
- Login, **refresh automático** del access token antes de que venza (para que
  el chat/SSE no reciban 401), **persistencia de sesión** (localStorage, sandbox
  de la app → no re-loguea en cada apertura), **cambio de contraseña temporal**
  (challenge `NEW_PASSWORD_REQUIRED` para usuarios creados por admin), y
  **logout** (botón en el topbar del inbox).
- El web/desktop sigue con su redirect OIDC; el token-store es compartido, así
  que apiClient + SSE funcionan igual en ambos.

**Requisitos en Cognito (consola):**
1. El **app client** debe ser **público (sin client secret)** — igual que el
   del dashboard web (SPA con PKCE). Si tiene secret, este flujo necesitaría
   `SECRET_HASH` (no se puede calcular seguro en el cliente) → habría que ir al
   camino deep-link.
2. Habilitar **`ALLOW_USER_PASSWORD_AUTH`** en el app client:
   App integration → App client → Authentication flows → tildar
   "ALLOW_USER_PASSWORD_AUTH". Sin esto, `InitiateAuth` responde
   `InvalidParameterException`.
3. Los operadores deben existir como usuarios del pool (email + contraseña).
   Si el admin los crea con contraseña temporal, el primer ingreso pide cambiarla
   (la app lo maneja).

**Config de build (envs `VITE_*`):**
```bash
VITE_API_URL=https://<tu-api>
VITE_COGNITO_AUTHORITY=https://cognito-idp.<region>.amazonaws.com/<userPoolId>
VITE_COGNITO_CLIENT_ID=<appClientId>
# VITE_COGNITO_REGION es opcional — se deriva del authority.
npm run tauri:android:build
```
La región y el endpoint IDP se derivan del authority. La CSP ya permite
`https://*.amazonaws.com` (el POST del login).

**MFA:** el pool sin MFA hace login en una sola llamada. Si más adelante
prenden MFA (SMS/TOTP), `InitiateAuth` devuelve un challenge extra que hoy la
app no maneja — es un incremento acotado sobre el mismo cliente.

**Dev local:** sin `VITE_COGNITO_*` (`cognitoEnabled=false`) la app abre sin
login (la API local está abierta) — ideal para el primer spike en device.
