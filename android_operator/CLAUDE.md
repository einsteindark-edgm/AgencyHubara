# android_operator — App Operador nativa (Kotlin + Compose)

> Archivo lean: punteros y gotchas. El diseño completo está en `docs/mobile-native/documento-tecnico.html`
> (y la visión en `docs/mobile-native/vision-chat-y-ordenes.html`).

## Cómo se corre

```bash
export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"   # no hay Java del sistema
cd android_operator && ./gradlew testDebugUnitTest :core:model:test               # todos los tests locales
cd android_operator && ./gradlew :app:assembleDebug                               # APK debug
cd android_operator && ./gradlew :app:assembleRelease                             # R8 completo (lo corre la CI)
cd android_operator && ./gradlew :app:bundleRelease -Phubara.apiUrl=https://… \
  -Phubara.cognitoClientId=… -Phubara.privacyUrl=https://…                         # .aab para Google Play
```

- **Publicar en Google Play**: receta paso a paso con enlaces oficiales en `docs/mobile-native/publicar-en-google-play.html`;
  lo que se sube (ícono, gráficos, capturas, textos, seguridad de datos, borrador de la política de privacidad) en
  `release/`. `bundleRelease` falla cerrado (`verifyPlayRelease`): sin https, sin client id de Cognito, sin clave de
  subida o sin política de privacidad no arma nada. La clave de subida y sus contraseñas viven en
  `~/.gradle/gradle.properties` (`hubara.upload.*`), **nunca** en el repo (es público). Cada subida sube `versionCode`.

- E2E en emulador contra el backend real de la rama y datos sintéticos: `e2e/qa.sh` corre los escenarios con
  guion (adb, sin LLM), igual que la compuerta de merge **QA emulador** en CI. Artemis explora a mano
  (`run_suite.py --driver artemis`). Ver `e2e/README.md`.
- Backend: `hubara.apiUrl` en `gradle.properties` (emulador → `http://10.0.2.2:8000`, el docker local).
  `hubara.cognitoClientId` vacío = modo dev sin login (el backend local tampoco exige token).
- HTTP en claro solo en debug y solo hacia `10.0.2.2`/`localhost` (`src/debug/res/xml`).

## Cómo se desarrolla una feature (el ciclo que ya nos salvó 14 veces)

1. **TDD** en el módulo: el test rojo por aserción primero (JVM/Robolectric).
2. **Explorar con Artemis en local**: `python3 e2e/run_suite.py --driver artemis --only <id>` con un `goal` en
   español. Gemini recorre la app como el operador y encuentra lo que los tests no ven. Gasta tokens y tarda
   4–5 s por paso: es para explorar, no para decidir. Instalado en `~/tools/artemis` (`e2e/README.md`, «Una vez»).
3. **Guionizar**: el flujo nuevo o lo que Artemis encontró queda como escenario en `e2e/scenarios.yaml` con
   `resumen` (qué prueba, en español llano), `script` (pasos por adb), `checks` y, si ayuda, un `shot`.
4. **`e2e/qa.sh`** corre la compuerta en local: emulador, backend real de la rama con datos sintéticos, guiones.
5. **PR**: el check requerido **QA emulador** corre todos los escenarios en GitHub y deja en el PR un comentario con
   una captura y qué probó cada uno. Si falla, el comentario dice qué chequeo.

## Mapa

| Módulo | Qué tiene |
|---|---|
| `:core:model` | Dominio en Kotlin puro. Los ids (`SessionId`, `OrderId`, `FireId`) solo se construyen validados. |
| `:core:network` | DTOs + mapeos (el único lugar que ve JSON), Retrofit, SSE con ticket, login de Cognito. |
| `:core:database` | Room: bandeja, mensajes, outbox, borradores, burbujas, incendios. |
| `:core:data` | Repositorios (Room = única fuente de verdad), `AuthRepository`, outbox con deshacer, `SyncEngine`. |
| `:core:navigation` | TODAS las claves de navegación, `Navigator` (tres pilas), deep links, hoja inferior. |
| `:core:designsystem` | **Material 3 Expressive con la marca**: paleta (`Color.kt`), Google Sans Flex (`Type.kt`), esquinas (`Shape.kt`), `Spacing`, íconos Material Symbols (`OperatorIcons`), `Avatar`, `StatusPill`, `IconTile`, `EmptyState`, `SuggestionBubble`, `segmentedShape`. |
| `:core:ui` | `RadarOverlay`/`RadarLayer`, `FireCard`, `OrderStepper`, horas (`TimeLabels`), `listContent` (cargando/vacía/error). |
| `:feature:*` | auth, inbox, chat, fires, orders. Cada una registra sus entradas con `@IntoSet`. |
| `:app` | `MainActivity`, `OperatorApp` (login o shell), `NavigationSuiteScaffold` + `NavDisplay` + radar. |

## Reglas

- **Jev nunca se llama desde el teléfono.** Las burbujas e incendios los decide el backend (motor del PR #372)
  y llegan por REST/SSE. La app solo pinta y ejecuta lo que el operador toca.
- **El radar no le quita el foco al teclado.** Capa en la misma ventana, `focusProperties { canFocus = false }`,
  sin Dialog/Popup/FocusRequester, toque inactivo 0,5 s. Lo protegen `core/ui/.../RadarOverlayTest`,
  `RadarLayerTest` y los escenarios S05/S14.
- **Todo flujo nuevo de la app entra con su escenario en `e2e/scenarios.yaml`** (con `script` y chequeos):
  esos escenarios son la base de la QA y la compuerta de merge los corre en cada PR que toca la app o su backend.
- **Todo envío pasa por el outbox** (`OutboxRepository`): Room primero, WorkManager después, `client_action_id`
  idempotente. Deshacer solo si el envío no empezó (`OutboxDao.claim/undo`).
- **Nada de teléfonos reales en tests** (forge gate): usa `wa_test_*` y números de ceros.
- **Textos en español con tú** (nunca voseo).
- **TDD**: primero el test que falla por aserción (un error de compilación no cuenta como rojo).
- **Diseño (Material 3 Expressive)**: los colores salen de `MaterialTheme.colorScheme` u `OperatorTheme.colors`
  (nunca `Color(0x…)` en una pantalla), el espaciado de `Spacing` (grilla de 4 dp, margen 16 dp), la letra de
  `MaterialTheme.typography` (los `*Emphasized` para lo que se ve primero) y los íconos de `OperatorIcons`. Toda pantalla nueva:
  `XRoute(vm)` que junta el estado + `XScreen(ui, callbacks)` sin ViewModel, con su `@Preview`.
  `ThemeContrastTest` exige 4,5:1 a todo texto: si cambias la paleta, cámbiala en `Color.kt` (ahí dice cómo se generó).

## Gotchas que ya nos quemaron

1. **compileSdk 37**: OkHttp 5.5 lo exige (target sigue en 36). AGP baja la plataforma sola.
2. **Robolectric fijo en SDK 36** (`src/test/resources/robolectric.properties`): el runtime de 37 no existe.
3. **Robolectric en JDK 21** necesita `--add-opens`/`--add-exports` (están en las convenciones de build-logic).
4. **Gradle 9 falla si hay recursos de test y ningún test**: no dejes un `robolectric.properties` solo.
5. **`activity-compose` 1.13+** en todo módulo con Compose: sin eso no hay `NavigationEventDispatcher`
   y `NavigationBackHandler` revienta en los tests.
6. **AGP 9**: `BuildConfig` por `androidComponents.onVariants`, KSP (no kapt), sin plugin `kotlin-android`.
7. **Tests de ViewModel**: el `Main` de prueba y el colector con `UnconfinedTestDispatcher(testScheduler)`.
8. **kotlinx no serializa los valores por defecto** salvo `encodeDefaults = true` (está en `OperatorJson`):
   `target_route = "ventas"` salía como `{}` y «Devolver al bot» daba 422. Lo encontró Artemis, no los tests.
9. **Números de pedido**: órdenes manda `"#41"` y la bandeja `"41"`; los mapeos quitan el `#` y la UI lo pone una vez.
10. **Sin esperas escondidas antes del radar**: nada de `debounce` en la recarga de incendios; `refreshOnSignal`
    recarga con la primera señal y junta las siguientes (una recarga cada 1,5 s como mucho).
11. **Hojas largas abren completas** (`bottomSheet(expanded = true)`): a media altura la acción quedaba escondida.
12. **Emulador**: los enlaces `hubara://` van con intent explícito (`am start -n …/.MainActivity -d …`); el manifest
    no tiene filtro VIEW. El vigía (periódico) no se adelanta con `cmd jobscheduler run -f`: corre al arrancar el
    proceso: se despierta con un broadcast explícito a `E2eWakeReceiver`, que solo existe en el build debug
    (el receptor del widget no está exportado y APPWIDGET_UPDATE es protegido).
13. **Nada que cambie cada segundo en la semántica**: la cuenta de «Deshacer» se ve pero va con
    `clearAndSetSemantics {}`, y la región viva es solo «Enviando «…»». Si no, TalkBack la repite cada segundo
    y uiautomator/Artemis nunca ven la pantalla quieta («could not get idle state»).
14. **No leídos como el dashboard web (#384)**: el backend manda `inbound_count` (TOTAL de mensajes del cliente) y la
    app recuerda cuántos había al abrir cada chat (`SeenCounts` + `SeenRepository`, DataStore). La primera bandeja
    cuenta todo como visto; con el chat en pantalla lo que llega queda leído. Room v2 renombró la columna
    (AutoMigration con `@RenameColumn`): cambios de esquema siempre con versión nueva y migración.
15. **Ráfagas de incendios**: los graves nuevos se juntan (`RadarBurst`: el del mensaje más reciente arriba, máx. 3
    y «Ver N más») y se pliegan juntos al chip 4 s después del último. Nunca bajan del `RadarFloor` (en el chat:
    deshacer, burbujas y composer). Antes cada tarjeta reemplazaba a la anterior y la primera se veía 2 s (S14).
16. **Material 3 Expressive va en material3 1.5 alpha** (`libs.versions.toml`: `material3 = "1.5.0-alpha29"`, fuera
    del BOM): `MaterialExpressiveTheme` con `MotionScheme.expressive()`, `Shapes.largeIncreased…`,
    `typography.*Emphasized`, `LoadingIndicator`, `ButtonDefaults.shapes()`. Arrastra compose-foundation
    1.13.0-alpha01. Volver a estable = borrar esa versión y sus `version.ref` (y recuperar los equivalentes propios
    del commit «sistema de diseño Material 3 Expressive»). Con la 1.5 estable: solo cambiar la versión.
17. **El enlace de una notificación se aplica una sola vez**: `MainActivity` lo lee solo si `savedInstanceState == null`
    y no viene de Recientes. Si no, cada giro o cambio de tema devolvía al chat del enlace (`DeepLinkRecreateTest`).
    No le cambies el intent a la actividad (`setIntent(...setData(null))`): `ActivityScenario` la sigue por su intent
    y `recreate()` se cuelga para siempre en Robolectric.
18. **`ExtendedFloatingActionButton(icon = …, text = …)` llega a accesibilidad sin texto** (material3 1.4): uiautomator
    lo marca `NAF` y TalkBack dice solo «botón». Usa la variante con contenido (`{ Icon(); Spacer(); Text() }`). Lo
    encontró el escenario S05 del rediseño.

## Endpoints

Existentes: `/api/dashboard/sessions[/{id}]`, `/intervene`, `/return-to-bot`, `/messages`, `/sse-ticket`,
`/events`, `/api/orders/orders[/{id}]`, `PATCH /api/orders/orders/{id}/stage`.
Nuevos: `GET /api/chats/mobile/suggestions/{id}`, `GET /api/chats/mobile/fires`,
`GET /api/chats/mobile/hot`, `POST /api/chats/mobile/devices`, `GET /api/chats/catalog`,
`POST /api/chats/session-actions/{id}/tools/{tool}`.
