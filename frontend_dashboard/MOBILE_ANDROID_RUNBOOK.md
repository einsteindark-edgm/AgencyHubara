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

## Decisión abierta — auth Cognito en Android (F4.3)

`react-oidc-context` con redirect en WebView: el origin de Tauri Android
(`http://tauri.localhost`) no es un callback válido en Cognito por default.
Opciones (evaluar la A en el spike; el token store ya está desacoplado, así que
cualquiera encaja sin tocar el data-layer):

- **A** — Hosted UI de Cognito dentro del WebView con callback al origin de la app.
- **B** — system browser + deep link (`tauri-plugin-deep-link`, callback `hubara://auth`).
- **C** (interim) — pantalla de login propia contra `InitiateAuth` (solo la app
  interna de operadores).

En dev local (sin `VITE_COGNITO_*`) la API está abierta y no hace falta login.
