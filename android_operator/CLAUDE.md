# android_operator — App Operador nativa (Kotlin + Compose)

> Archivo lean: punteros y gotchas. El diseño completo está en `docs/mobile-native/documento-tecnico.html`
> (y la visión en `docs/mobile-native/vision-chat-y-ordenes.html`).

## Cómo se corre

```bash
export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"   # no hay Java del sistema
cd android_operator && ./gradlew testDebugUnitTest :core:model:test :core:sdui:test   # todos los tests locales
cd android_operator && ./gradlew :app:assembleDebug                               # APK debug
cd android_operator && ./gradlew :app:assembleRelease                             # R8 completo (lo corre la CI)
cd android_operator && ./gradlew :app:bundleRelease \
  -Phubara.configUrl=https://<dominio del dashboard>/mobile/config.json \
  -Phubara.privacyUrl=https://…                                                    # .aab para Google Play
```

- **Publicar en Google Play**: receta paso a paso con enlaces oficiales en `docs/mobile-native/publicar-en-google-play.html`;
  lo que se sube (ícono, gráficos, capturas, textos, seguridad de datos, borrador de la política de privacidad) en
  `release/`. `bundleRelease` falla cerrado (`verifyPlayRelease`): sin configuración https, sin clave de subida o sin
  política de privacidad no arma nada. La clave de subida y sus contraseñas viven en
  `~/.gradle/gradle.properties` (`hubara.upload.*`), **nunca** en el repo (es público). Cada subida sube `versionCode`.

- **TODA la app son pantallas del servidor** (Server-Driven UI): `screens/*.json` — pestañas (`app.json`), bandeja,
  Incendios, Órdenes, ficha, chat, paleta, plantillas y «Más». Un cambio sale con `frontend-deploy.yml` (a
  `<cloudfront>/mobile/screens/`) sin publicar la app. Guía: `screens/README.md`; referencia generada:
  `screens/CATALOGO.md`. Si cambias el catálogo: `./gradlew :core:sdui:test -PupdateScreens=true`. Nativo solo: login,
  versión mínima, radar, widget, notificaciones y las piezas nativas del catálogo (`chat`, `notifications_banner`).
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
| `:core:sdui` | **Motor de pantallas del servidor**, Kotlin puro: contrato (`ScreenDoc`, `Node`, `Action`), lenguaje `{{ … }}` (`Template` + `Filters`), `Catalog` (la ÚNICA lista de componentes, íconos y acciones), validación, rutas seguras, esquema JSON y `ScreensRepoTest` (valida `screens/`). |
| `:core:network` | DTOs + mapeos (el único lugar que ve JSON), Retrofit, SSE con ticket, login de Cognito. |
| `:core:database` | Room: bandeja, mensajes, outbox, borradores, burbujas, incendios. |
| `:core:data` | Repositorios (Room = única fuente de verdad), `AuthRepository`, outbox con deshacer, `SyncEngine`, y para las pantallas: `ScreenStore`, `ScreenDataClient`, las **fuentes del teléfono** (`screens/sources`: bandeja con no leídos, incendios, chat, plantillas) y el contrato `NativeActions`. |
| `:core:navigation` | TODAS las claves de navegación, `Navigator` (una pila por pestaña), deep links, hoja inferior y `ScreenRoutes` (qué pantalla arma cada clave; `fireDestination`). |
| `:core:designsystem` | **Material 3 Expressive con la marca**: paleta (`Color.kt`), Google Sans Flex (`Type.kt`), esquinas (`Shape.kt`), `Spacing`, íconos Material Symbols (`OperatorIcons`), `Avatar`, `StatusPill`, `IconTile`, `EmptyState`, `SuggestionBubble`, `segmentedShape`, y la carga (`Loading.kt`: `LoadingState`, `WorkingIndicator`, `LoadError`). |
| `:core:ui` | `RadarOverlay`/`RadarLayer`, `FireCard`, horas (`TimeLabels`), `NativeComponent` (contrato de las piezas nativas). |
| `:feature:auth` | El login (nativo: va antes de la sesión). |
| `:feature:chat` | La pieza nativa `chat` (`ChatIsland`: lo que entendió el bot, historial, deshacer, burbujas, composer) con su `ChatViewModel`. Sin entradas de navegación. |
| `:feature:screens` | **Todas las pantallas**: UN `ScreenViewModel` (MVI), el render de cada componente (`Components.kt`) y las entradas de TODAS las claves (`ScreensNavigation`: cada clave se arma con su pantalla según `ScreenRoutes`). `RealScreensTest` prueba los archivos reales del repo. |
| `:core:push` | Fuera de la app: notificaciones, el vigía (`Vigia` + `VigiaWorker`), cerrar sesión, los **avisos push** (`PushRegistrar`, `PushHandler`, `OperatorMessagingService`, `FirebasePushTransport`) y las páginas del widget (`WidgetPages.kt` + `AmbientStore`). |
| `:widget:hot` | El widget de la pantalla de inicio: `AppWidgetProvider` con una `StackView` de tres páginas (ventas calientes, incendios, humano). Solo pinta lo que dejó el vigía. |
| `:app` | `MainActivity`, `OperatorApp` (login o shell; las pestañas y sus números salen de `app.json`), radar, `AppNativeActions` (outbox, sesión, incendios). |

## Reglas

- **Jev nunca se llama desde el teléfono.** Las burbujas e incendios los decide el backend y llegan por REST/SSE; la
  app solo pinta (`decided_by`, orden, `prominence`, gravedad, tipo) y ejecuta lo que el operador toca. En el backend:
  las reglas (`chats/shared/mobile_rules.py`) arman lo legal y son la regla de cada capacidad; qué burbuja va
  primero (`burbuja`) y cómo se clasifica un incendio de chat (`incendio`) lo decide el **motor de decisiones
  oficial** (PR #372) con el paquete `operador-2` (`chats/shared/operator/decisions/`; la v1 `operador` queda publicada): el resolutor
  `registry.foreign_capability` (`BundledCapability` con los builtins del paquete) + `decide_for_session`
  (`chats/api/mobile_decisions.py`). Se encienden como las de ventas: POR COMANDO (`infra/scripts/bot_control.sh`
  → `decisions/control.py capacidad burbuja shadow`; off | shadow | canary | on dentro del techo
  `SALES_CAPABILITIES_CEILING`, con la vara al subir). El panel «Motor de decisiones» solo las muestra.
  Cambiar una pregunta o un umbral = versión nueva del paquete (`decisions check` + huella en
  `test_decision_bundles_published.py`), nunca un `if` en la ruta ni un interruptor propio.
- **El radar no le quita el foco al teclado.** Capa en la misma ventana, `focusProperties { canFocus = false }`,
  sin Dialog/Popup/FocusRequester, toque inactivo 0,5 s. Lo protegen `core/ui/.../RadarOverlayTest`,
  `RadarLayerTest` y los escenarios S05/S14.
- **Todo flujo nuevo de la app entra con su escenario en `e2e/scenarios.yaml`** (con `script` y chequeos):
  esos escenarios son la base de la QA y la compuerta de merge los corre en cada PR que toca la app o su backend.
- **Los avisos push no llevan datos de clientes** (pasan por Google): solo `{"type": "sync" | "test", "reason"}`.
  Un push = una vuelta del vigía en el momento (`PushHandler` → `Vigia`; lo que no cabe en 8 s va a WorkManager).
  Firebase NO va en el APK ni en el repo: el teléfono pide `GET /api/chats/mobile/push` y arranca Firebase con esas
  opciones (`PushRegistrar`; las guarda para el próximo arranque en frío). El backend decide cuándo despertar
  (`chats/api/mobile_push.py`). Activarlo es trabajo del operador: `docs/mobile-native/activar-avisos-push.html`.
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

- **Pantallas del servidor**: un componente, filtro, ícono, fuente del teléfono o acción nueva entra a `Catalog.kt` (+ su
  render o implementación y su test) y sube `Catalog.VERSION`; la pantalla que lo use declara `"requires"`. Nunca una
  pantalla con código, rutas absolutas o `http://`: la validación lo rechaza y `ScreenDataClient` lo vuelve a revisar.
- **Una pantalla nueva o un cambio de pantalla no es código**: se edita `screens/*.json`. Código solo para lo que el
  catálogo no tiene (y entonces sí es una versión nueva de la app).

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
12. **Emulador**: los enlaces `hubara://` van con intent explícito
    (`am start -n com.acktos.operator/com.hubara.operator.MainActivity -d …`); el manifest
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
19. **La dirección del backend NO va en el APK** (Server-Driven): lo único fijo es `hubara.configUrl`
    (`<cloudfront del dashboard>/mobile/config.json`, lo publica `frontend-deploy.yml` con `api_url` + Cognito +
    `tenants.*.mobile` de Terraform). `ServerConfigStore` arranca con la última buena (o la del build) y la renueva al
    abrir y al volver a primer plano; `ApiConfig` lee siempre la vigente y Retrofit nace con `PLACEHOLDER_BASE_URL`, que
    el interceptor cambia por el backend del momento: una IP nueva no obliga a publicar otra versión. Solo se acepta
    https (en debug, http hacia 10.0.2.2/localhost). `min_version_code` > versionCode → «Hay una versión nueva».
    El APK de prueba trae un respaldo muerto (`10.0.2.2:9`) y la configuración del sandbox (`/__sandbox/mobile/config.json`):
    si un escenario llega a la bandeja, la remota funcionó (S15; S16 = versión mínima).

20. **Pantallas del servidor (SDUI)**: toda la app; la app solo trae fijo el catálogo; las pantallas bajan de `screens_url` de
    config.json o, si no viene, de `screens/` junto a él (`ServerConfigStore.screensBase()`). `ScreenStore` guarda la
    última válida (o usa la del APK, `assets/screens/`) y nunca la pisa con un 404 ni con la SPA del CDN; los datos
    van por `ScreenDataClient` (mismo OkHttp: token solo a nuestro backend) y lo último de cada fuente queda en
    `FileScreenDataCache` (se borra al cerrar sesión). Las pestañas de `app.json` se aplican al SIGUIENTE arranque
    (cambiarlas en caliente reiniciaría la navegación); un `app.json` con menos de 2 pestañas se descarta (queda el del APK).
    Las claves de siempre (`InboxKey`, `ChatKey`, `OrderSheetKey`…) siguen: las usan enlaces, radar y tablet, y
    `ScreenRoutes` dice qué pantalla arma cada una. S17–S19 en el emulador; S18 cambia una pantalla y publica una nueva
    con `inject.py screen` sin reinstalar la app. Los 20 escenarios recorren la app entera armada desde JSON.
21. **Los endpoints de pedidos rechazan con `200` + `"success": false`** (confirm-payment, stage…): `ScreenDataClient`
    lo trata como error y muestra `error_detail` sin el código (`invalid_state: …`). Sin eso, la pantalla decía
    «Pago confirmado» al confirmar el pago de un borrador (lo encontró el diseño de S19).
22. **En Kotlin, «barra + asterisco» DENTRO de un comentario abre otro comentario anidado.** Un glob escrito en la
    KDoc de una tarea de `app/build.gradle.kts` se tragó en silencio el bloque `dependencies {}`: el classpath de la
    app quedó sin ningún módulo y KSP decía que no encontraba `HiltWorkerFactory`. No escribas globs en comentarios.
23. **El sandbox del emulador va en 8010 salvo que esté ocupado**: `QA_SANDBOX_PORT=8020 e2e/qa.sh` (otra sesión puede
    tener un contenedor en 8010; no se toca).
24. **Un componente que se toca fuera de la pantalla de prueba no recibe el toque**: en los tests de Compose (320 px)
    `performClick` sobre algo debajo del borde cae afuera y la acción nunca llega. Desliza primero
    (`performScrollToNode` sobre `SCREEN_LIST_TAG`), como en `RealScreensTest`.
25. **La compuerta «QA emulador» roja sin reporte = no llegó a correr escenarios.** Mira el log del paso: el caso
    visto (run 36925839845) fue `INSTALL_FAILED_UPDATE_INCOMPATIBLE` (AVD en caché con la app firmada por otro
    runner). `qa.sh` ya desinstala y reinstala; si vuelve, sube la `key` de la caché del AVD en `qa-emulador.yml`.
26. **La app nunca espera a Jev**: la burbuja lo espera 2,5 s como mucho y los incendios nada; lo que no llegó sale por
    reglas y el veredicto queda en la caché del proceso para la MISMA versión del chat y el mismo modo (la
    siguiente consulta lo usa; un deploy la vacía). Los incendios de PEDIDOS (retraso, pago sin verificar) son hechos:
    no son una decisión. El motor deja cada decisión en `<vault>/<sid>/evals/decisions.jsonl` (`stage: "operador"`),
    sus métricas (la vara para subir), los desacuerdos y el costo (`jev_usage`). El backend de prueba del emulador
    corre Jev falso (`PERCEPTION_PROVIDER=fake`, sin red) con las dos decisiones en sombra (`seed.py` escribe
    `_rollout/decisions.json`; techo `shadow`): cada escenario recorre el motor sin cambiar lo que ve la app.
27. **Firebase arranca a mano, sin `google-services.json`** (el repo es público y cada tienda tiene su proyecto):
    `FirebaseInitProvider` está quitado del manifest de `:core:push`; `OperatorApplication.onCreate` llama
    `PushRegistrar.startFromCache()` (las últimas opciones del servidor, SharedPreferences síncronas) para que un push
    que despierta el proceso se pueda entregar. La app por defecto de Firebase no se rearma en el mismo proceso: si el
    servidor cambia de proyecto, vale desde el próximo arranque. En el emulador de CI no hay Firebase: el backend de
    prueba usa el avisador falso (`PUSH_PROVIDER=fake`, deja cada push en `sandbox/data/pushes.jsonl`) y el paso
    `relay_push` lo entrega a la app por `E2eWakeReceiver` (`E2E_PUSH`), el mismo `PushHandler` de un push real (S20).

28. **La app es `com.acktos.operator`; el código, `com.hubara.operator`** (2026-10-07, decisión del operador: así se
    llama en Firebase y en Google Play). `applicationId` ≠ `namespace`: `pm`/`am` usan el primero y las clases
    el segundo, así que un componente se escribe COMPLETO (`com.acktos.operator/com.hubara.operator.E2eWakeReceiver`;
    `…/.MainActivity` apuntaría a `com.acktos.operator.MainActivity`, que no existe). El backend elige ese cliente
    de `google-services.json` (`DEFAULT_ANDROID_PACKAGE`; `MOBILE_ANDROID_PACKAGE` lo cambia).

29. **El traspaso del bot no es una respuesta** (prueba del operador, 2026-10-07): «necesito hablar con alguien» →
    el bot responde «te comunico…» (`tools_used: ["escalate_to_human"]`) y pasa a humano. Ese mensaje contaba como
    respuesta: cero sin responder, sin incendio y sin aviso. Ahora `_is_reply` lo excluye y `handoff_pending` lo
    marca: el chat es incendio GRAVE al instante y sale el push (decisión del operador: aviso inmediato en todo
    traspaso). Cuando el operador responde, vuelve la regla por tiempo (2 min «hoy», 10 min grave). El paquete
    `operador-2@2` no deja que Jev lo baje (ni una espera de horas). S21 en el emulador.

30. **Cargando solo sin nada que mostrar** (2026-10-08): `LoadingState` (el `LoadingIndicator` expresivo, «Cargando…» fijo
    para TalkBack, aparece a los 300 ms para no parpadear) va donde la pantalla no tiene NADA: lo guardado (Room,
    `FileScreenDataCache`) se muestra y la recarga va callada; el indicador de «tirar para actualizar» solo cuando lo
    pidió el operador (`ScreenUi.pullIndicator`: tirar o una acción `refresh`). Una fuente del teléfono vacía (Room recién
    instalada) no cuenta como dato hasta su primera carga buena: antes la bandeja decía «No hay chats» mientras llegaban.
    Si falla sin nada guardado: `LoadError` con «Reintentar», nunca un indicador eterno. Una llamada (`call`) o acción
    nativa marca `busy` + `working` (el `origin` del botón que la lanzó: ese muestra `WorkingIndicator`; las del menú,
    la línea de `BusyBar`) y un doble toque no sale dos veces. El chat: `HistoryView` (cargando / error / mensajes). S22–S23.

31. **El widget es `AppWidgetProvider` + `StackView`, no Glance** (2026-10-08): Glance 1.2 no tiene páginas que se
    deslicen. Las páginas van ADENTRO del RemoteViews con `RemoteViewsCompat.setRemoteAdapter(RemoteCollectionItems)`:
    en Android 12+ la API nativa y en el Android 11 del operador el `RemoteViewsCompatService` de `core-remoteviews`
    (protegido con `BIND_REMOTEVIEWS`), sin servicio ni fábrica propios. La clase se sigue llamando
    `HotSalesWidgetReceiver` para que los widgets ya puestos no se rompan. Trampas que vio S24: StackView mide cada
    tarjeta con AT_MOST (sin `minWidth`/`minHeight` grandes una página de filas cortas queda angosta y asoma la de
    atrás), las filas que faltan van INVISIBLE (no GONE) y deslizar HACIA ABAJO avanza. Solo `home_screen` (nunca la
    pantalla bloqueada); filas sin mensajes ni esperas que se pongan viejas; cada fila abre `hubara://chat/<sesión>`
    (un pedido sin chat, su ficha) con una plantilla `PendingIntent` explícita y MUTABLE solo para el fill-in.

## Endpoints

Existentes: `/api/dashboard/sessions[/{id}]`, `/intervene`, `/return-to-bot`, `/messages`, `/sse-ticket`,
`/events`, `/api/orders/orders[/{id}]`, `PATCH /api/orders/orders/{id}/stage`.
Nuevos: `GET /api/chats/mobile/suggestions/{id}`, `GET /api/chats/mobile/fires`,
`GET /api/chats/mobile/hot`, `GET /api/chats/mobile/human` (página «Humano» del widget), `GET /api/chats/mobile/push`, `POST /api/chats/mobile/devices`,
`DELETE /api/chats/mobile/devices/{token}`, `POST /api/chats/mobile/devices/test` («Probar avisos»), `GET /api/chats/catalog`,
`POST /api/chats/session-actions/{id}/tools/{tool}`.
Pantallas del servidor (las de `screens/`): `/api/orders/orders`, `PATCH /api/orders/orders/{id}/confirm-payment`,
`/api/marketing/campaigns[/{id}[/stats]]`, `/api/dashboard/sessions`.
