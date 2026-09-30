# android_operator — App Operador nativa (Kotlin + Compose)

> Archivo lean: punteros y gotchas. El diseño completo está en `docs/mobile-native/documento-tecnico.html`
> (y la visión en `docs/mobile-native/vision-chat-y-ordenes.html`).

## Cómo se corre

```bash
export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"   # no hay Java del sistema
cd android_operator && ./gradlew testDebugUnitTest :core:model:test               # todos los tests locales
cd android_operator && ./gradlew :app:assembleDebug                               # APK debug
cd android_operator && ./gradlew :app:assembleRelease                             # R8 completo
```

- E2E en emulador contra el backend real de la rama y datos sintéticos: `e2e/qa.sh` corre los escenarios con
  guion (adb, sin LLM), igual que la compuerta de merge **QA emulador** en CI. Artemis explora a mano
  (`run_suite.py --driver artemis`). Ver `e2e/README.md`.
- Backend: `hubara.apiUrl` en `gradle.properties` (emulador → `http://10.0.2.2:8000`, el docker local).
  `hubara.cognitoClientId` vacío = modo dev sin login (el backend local tampoco exige token).
- HTTP en claro solo en debug y solo hacia `10.0.2.2`/`localhost` (`src/debug/res/xml`).

## Mapa

| Módulo | Qué tiene |
|---|---|
| `:core:model` | Dominio en Kotlin puro. Los ids (`SessionId`, `OrderId`, `FireId`) solo se construyen validados. |
| `:core:network` | DTOs + mapeos (el único lugar que ve JSON), Retrofit, SSE con ticket, login de Cognito. |
| `:core:database` | Room: bandeja, mensajes, outbox, borradores, burbujas, incendios. |
| `:core:data` | Repositorios (Room = única fuente de verdad), `AuthRepository`, outbox con deshacer, `SyncEngine`. |
| `:core:navigation` | TODAS las claves de navegación, `Navigator` (tres pilas), deep links, hoja inferior. |
| `:core:designsystem` · `:core:ui` | Tema (colores del dashboard), `SuggestionBubble`, `RadarOverlay`, `FireCard`, `OrderStepper`. |
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
    proceso: se despierta con un broadcast explícito de acción propia al receptor del widget
    (APPWIDGET_UPDATE es protegido y el shell no puede mandarlo).
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

## Endpoints

Existentes: `/api/dashboard/sessions[/{id}]`, `/intervene`, `/return-to-bot`, `/messages`, `/sse-ticket`,
`/events`, `/api/orders/orders[/{id}]`, `PATCH /api/orders/orders/{id}/stage`.
Nuevos: `GET /api/chats/mobile/suggestions/{id}`, `GET /api/chats/mobile/fires`,
`GET /api/chats/mobile/hot`, `POST /api/chats/mobile/devices`, `GET /api/chats/catalog`,
`POST /api/chats/session-actions/{id}/tools/{tool}`.
