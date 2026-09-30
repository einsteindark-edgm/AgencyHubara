# Pruebas E2E de la App Operador (emulador + backend de prueba)

Recorren la app como lo haría el operador, sobre un emulador Android, contra el **código real del
backend de esta rama** con datos sintéticos. Los escenarios de `scenarios.yaml` son la **base de la QA
de la app**: cada PR que toca la app o el backend que ella consume tiene que pasarlos para poder
mergear (workflow `QA emulador`).

Cada escenario prepara el estado, un **conductor** recorre la app y al final **chequeos
deterministas** deciden: lo que el backend registró como enviado por WhatsApp, el estado de una orden
en el API y el árbol de la pantalla leído por adb. Hay dos conductores para los mismos escenarios y
los mismos chequeos:

| Conductor | Qué hace | Cuándo |
|---|---|---|
| `script` (por defecto) | Sigue los pasos de `script:` por adb: siempre igual, sin LLM, sin llaves, ~1 min por escenario. | La compuerta de merge (CI) y `qa.sh` en tu Mac. |
| `artemis` | [Artemis](https://github.com/google/artemis) (Google, sobre Gemini) persigue el `goal:` en lenguaje natural: explora y encuentra lo que nadie guionizó. | A mano, cuando cambia un flujo o para buscar fallas nuevas. Gasta tokens. |

| Pieza | Qué es |
|---|---|
| `sandbox/` | El API real (`src.main:app`, plugins chats + orders) en `127.0.0.1:8010`, con datos 100 % sintéticos. Solo se falsean los sistemas externos: WhatsApp (el FakeSend del repo, que deja todo en `sent.log`), Medusa (sobre un JSON) y Temporal. Arranca con `env -i`, aborta si ve credenciales y bloquea toda conexión saliente. |
| `scenarios.yaml` | Los escenarios: preparación, guion (`script`), objetivo para Artemis (`goal`) y chequeos. |
| `run_suite.py` | Corre los escenarios con el conductor elegido y deja `reporte.md` / `reporte.json` + una captura por escenario (y lo que había en pantalla si falla). Sale con código 1 si alguno falla. |
| `qa.sh` | La compuerta en una línea: APK nuevo, emulador (lo arranca si no hay), backend de prueba y la suite con guion. CI llama a este mismo script. |
| `devicekit.py` | Manejo y chequeos por adb sin LLM: árbol de UI, toques, foco, teclado, qué tapa a qué, capturas. |
| `radar_focus_check.py` | La regla del radar medida con precisión: llega un incendio grave mientras el operador escribe y la tarjeta aparece sin quitarle el foco ni cerrar el teclado. |
| `artemis.sh` | Envoltura de Artemis que toma la llave de Gemini del entorno o de un archivo, sin imprimirla. |

## La compuerta de merge (`.github/workflows/qa-emulador.yml`)

- Corre en **todo PR**. Si no toca `android_operator/`, los plugins `chats`/`orders`, `src/platform`,
  `src/sdk`, `src/main.py` ni las dependencias, pasa en segundos. Si los toca, levanta el emulador y corre
  todos los escenarios con guion (`--retries 1`: un escenario que pasa al segundo intento queda marcado
  en el reporte).
- Corre en los runners de GitHub: gratis en un repo público, con KVM para el emulador y **sin secretos**.
  El código de un PR nunca corre en una máquina con credenciales (por eso no es un runner en la Mac).
- El reporte sale en el resumen de la corrida; capturas, pantallas de las fallas y logs del backend de
  prueba quedan en el artefacto `qa-emulador`.
- Para que bloquee el merge, el check **«QA emulador — App Operador»** tiene que estar en los checks
  requeridos de `main` (Settings → Branches).

## Agregar un escenario

1. Escríbelo en `scenarios.yaml` con `setup`, `script` y `checks` (y `goal` si Artemis también puede
   recorrerlo). Pasos disponibles en `Run.step` de `run_suite.py`: `tap`, `type`, `expect`, `expect_gone`,
   `expect_all` (varios textos a la vez, en orden y sin tapar el campo con foco), `inject`, `back`,
   `notifications`, `assert`… Un texto con «=» adelante se busca exacto.
2. Córrelo solo: `android_operator/e2e/qa.sh --only <id>`.
3. Si nació de una falla, que el primer rojo quede anotado en el comentario del escenario.

## Una vez

```bash
# Emulador (Mac Intel: imagen x86_64; Android 11 = el Motorola del operador)
sdkmanager "system-images;android-30;google_apis;x86_64"
avdmanager create avd -n hubara_api30 -k "system-images;android-30;google_apis;x86_64" -d medium_phone

# Artemis (en una Mac Intel usar el Python administrado por uv: el de Anaconda rechaza las ruedas de pyarrow)
git clone https://github.com/google/artemis.git ~/tools/artemis
cd ~/tools/artemis && UV_PYTHON_PREFERENCE=only-managed uv sync --python 3.12
```

## Cada corrida

```bash
android_operator/e2e/qa.sh                                                   # la compuerta completa, como en CI
android_operator/e2e/qa.sh --only S14_varios_incendios_mientras_escribes     # un escenario
```

Con Artemis (explorar; gasta tokens de Gemini):

```bash
emulator -avd hubara_api30 -no-window -no-audio -no-snapshot-save &          # arranca en ~75 s
android_operator/e2e/sandbox/run_api.sh start --reset                         # backend de prueba en :8010
cd android_operator && ./gradlew :app:installDebug -Phubara.apiUrl=http://10.0.2.2:8010
export ARTEMIS_HOME=~/tools/artemis ARTEMIS_KEY_FILE=/ruta/al/.env           # archivo con GEMINI_API_KEY=
python3 android_operator/e2e/run_suite.py --driver artemis --out /tmp/e2e    # o --only S02_tomar_y_enviar_aromas
android_operator/e2e/sandbox/run_api.sh stop
```

## Reglas

- **Nada real.** El backend de prueba no ve credenciales ni sale a internet; los datos son sintéticos
  (sesiones `wa_0000000001xx`, clientes «… Prueba»). Artemis sí manda capturas del emulador a Gemini:
  por eso nunca se prueba con datos de clientes.
- **El conductor recorre, los chequeos deciden.** Un escenario pasa solo si el conductor termina bien
  (todos los pasos del guion, o Artemis reporta éxito) Y todos sus chequeos deterministas pasan.
- **Lo que depende del tiempo lo hace adb.** Cada paso de Artemis tarda 4–5 s en una Mac Intel: no
  alcanza a tocar «Deshacer» dentro de los 5 s ni a ver una ráfaga de incendios que se pliega en 4 s. Esos
  toques van en `setup` (o el escenario es solo de guion) y Artemis verifica el resultado.
- **Lo enviado se cuenta desde el `reset`**, no desde el fin de la preparación: si un «Deshacer» de
  `setup` falla, el envío sale ahí mismo y el chequeo lo ve.
- **Con la máquina muy cargada, S03 puede fallar por tiempo.** El deshacer dura 5 s. En una Mac con
  Docker y otras sesiones encima, una lectura de pantalla tardó ~3 s y un toque hasta 4 s, así que el
  «Deshacer» llega tarde. En CI no pasa. Si pasa en local, mira la carga (`uptime`) antes de sospechar de la app.

## El backend de prueba (`sandbox/`)

`run_api.sh start --reset` siembra los datos (las horas son relativas a «ahora»: usa siempre `--reset`)
y levanta el API en segundo plano; `stop`, `status`, `check` (solo arranque + rutas) y `bash verify.sh`
(recorre todos los endpoints que usa la app y deja la transcripción).

**Real (código de la rama, sin tocar):** el cargador de plugins y todas las rutas de chats y órdenes,
`mobile.py` + `mobile_rules.py`, `operator_tools.py` (tools del bot, flush acotado por id, historial
firmado `sender: human`), intervenir / devolver / mensajes / plantillas, el SSE con su muestreador,
`OrderFactsStore` y la máquina de etapas de órdenes, el catálogo por snapshot.

**Falso (solo lo externo):**

| Sistema | Cómo |
|---|---|
| WhatsApp | `WHATSAPP_ACCESS_TOKEN` vacío → corre el FakeSend del repo; cada envío queda en `sent.log` con un wamid único. |
| Medusa | Subclase del cliente real que responde las llamadas Admin sobre `data/medusa/store.json`. |
| Temporal | Cliente falso que anota en `temporal.log` (intervenir sí «termina» el workflow de la sesión). |

**Datos** (todo sintético; los chats se ven por número en la bandeja):

| Sesión | Cliente | Para qué |
|---|---|---|
| `wa_000000000101` | Laura | bot atiende, pregunta por aromas → burbuja «Enviar aromas» |
| `wa_000000000102` | Sofía | pidió un humano hace 12 min → incendio GRAVE |
| `wa_000000000103` | Camilo | venta caliente en cierre → widget |
| `wa_000000000104` | Andrés | pedido #41 atrasado 4 días → incendio GRAVE y botón «Pedido #41» |
| `wa_000000000105` | Valentina | ventana de 24 h cerrada → solo plantillas |
| `wa_000000000106` | Daniela | comprobante de pago por verificar → incendio HOY |

`python3 sandbox/inject.py fire` crea a Mateo (`wa_000000000107`) pidiendo un humano: un incendio grave
nuevo que llega por SSE en 1–2 s (`--name "Lucía Prueba" --session wa_000000000108` para otro cliente).
`inject.py reply <sesión> "<texto>"` agrega un mensaje del cliente.
