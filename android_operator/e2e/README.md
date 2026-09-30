# Pruebas E2E de la App Operador (emulador + Artemis + backend de prueba)

Recorren la app como lo haría el operador, sobre un emulador Android, contra el **código real del
backend de esta rama** con datos sintéticos. [Artemis](https://github.com/google/artemis) (Google)
maneja la app a partir de objetivos en lenguaje natural; después cada escenario confirma con hechos
deterministas lo que Artemis dice que vio: lo que el backend registró como enviado por WhatsApp,
el estado de una orden en el API y el árbol de la pantalla leído por adb.

| Pieza | Qué es |
|---|---|
| `sandbox/` | El API real (`src.main:app`, plugins chats + orders) en `127.0.0.1:8010`, con datos 100 % sintéticos. Solo se falsean los sistemas externos: WhatsApp (el FakeSend del repo, que deja todo en `sent.log`), Medusa (sobre un JSON) y Temporal. Arranca con `env -i`, aborta si ve credenciales y bloquea toda conexión saliente. |
| `scenarios.yaml` | Los escenarios: preparación (sin gastar pasos de Artemis), objetivo en español y chequeos. |
| `run_suite.py` | Corre los escenarios y deja `reporte.md` / `reporte.json` + una captura por escenario. |
| `devicekit.py` | Chequeos por adb sin LLM: árbol de UI, foco, teclado, capturas. |
| `radar_focus_check.py` | La regla del radar medida con precisión: llega un incendio grave mientras el operador escribe y la tarjeta aparece sin quitarle el foco ni cerrar el teclado. |
| `artemis.sh` | Envoltura de Artemis que toma la llave de Gemini del entorno o de un archivo, sin imprimirla. |

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
emulator -avd hubara_api30 -no-window -no-audio -no-snapshot-save &          # arranca en ~75 s
android_operator/e2e/sandbox/run_api.sh start --reset                         # backend de prueba en :8010
cd android_operator && ./gradlew :app:installDebug -Phubara.apiUrl=http://10.0.2.2:8010

export ARTEMIS_HOME=~/tools/artemis ARTEMIS_KEY_FILE=/ruta/al/.env           # archivo con GEMINI_API_KEY=
python3 android_operator/e2e/run_suite.py --out /tmp/e2e                      # o --only S02_tomar_y_enviar_aromas
android_operator/e2e/sandbox/run_api.sh stop
```

## Reglas

- **Nada real.** El backend de prueba no ve credenciales ni sale a internet; los datos son sintéticos
  (sesiones `wa_0000000001xx`, clientes «… Prueba»). Artemis sí manda capturas del emulador a Gemini:
  por eso nunca se prueba con datos de clientes.
- **Artemis explora, los chequeos deciden.** Un escenario pasa solo si Artemis termina bien Y todos
  sus chequeos deterministas pasan.
- **Lo que depende del tiempo lo hace adb.** Cada paso de Artemis tarda 4–5 s en una Mac Intel: no
  alcanza a tocar «Deshacer» dentro de los 5 s. Esos toques van en `setup` y Artemis verifica el resultado.

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
nuevo que llega por SSE en 1–2 s. `inject.py reply <sesión> "<texto>"` agrega un mensaje del cliente.
