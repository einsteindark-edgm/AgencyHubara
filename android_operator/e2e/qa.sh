#!/usr/bin/env bash
# QA en emulador de la App Operador: lo mismo que corre la compuerta de merge (.github/workflows/qa-emulador.yml).
#
#   android_operator/e2e/qa.sh                  # APK nuevo + backend de prueba + todos los escenarios con guion
#   android_operator/e2e/qa.sh --only S14_varios_incendios_mientras_escribes
#   android_operator/e2e/qa.sh --skip-build     # usa el APK ya compilado (así lo llama CI)
#   android_operator/e2e/qa.sh --build-only     # solo compila el APK (CI lo hace antes de arrancar el emulador)
#
# Si no hay un emulador conectado arranca el AVD $QA_AVD (por defecto hubara_api30) sin ventana y lo deja
# prendido para la próxima corrida (apagarlo: adb emu kill). El reporte queda en $QA_OUT.
set -euo pipefail

E2E="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$E2E/../.." && pwd)"
OUT="${QA_OUT:-${RUNNER_TEMP:-${TMPDIR:-/tmp}}/qa-emulador}"
SERIAL="${QA_SERIAL:-emulator-5554}"
AVD="${QA_AVD:-hubara_api30}"
SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Library/Android/sdk}}"
ADB="$SDK/platform-tools/adb"
[ -x "$ADB" ] || ADB="$(command -v adb)"
APK="$REPO/android_operator/app/build/outputs/apk/debug/app-debug.apk"

build=1
build_only=0
only=""
while [ $# -gt 0 ]; do
  case "$1" in
    --skip-build) build=0 ;;
    --build-only) build_only=1 ;;
    --only) only="$2"; shift ;;
    *) echo "opción desconocida: $1" >&2; exit 2 ;;
  esac
  shift
done
mkdir -p "$OUT"

if [ "$build" = 1 ]; then
  # Respaldo muerto (:9) + configuración remota del backend de prueba: si la app llega a la bandeja, tomó la
  # dirección del config.json (como en producción la toma del CloudFront del dashboard).
  echo "▶ APK con la configuración remota del backend de prueba (10.0.2.2:8010)" >&2
  (cd "$REPO/android_operator" && ./gradlew :app:assembleDebug -Phubara.apiUrl=http://10.0.2.2:9 \
    -Phubara.configUrl=http://10.0.2.2:8010/__sandbox/mobile/config.json --console=plain -q)
fi
[ "$build_only" = 1 ] && exit 0

if ! "$ADB" -s "$SERIAL" get-state >/dev/null 2>&1; then
  echo "▶ arrancando el emulador $AVD (la primera vez tarda unos minutos)" >&2
  nohup "$SDK/emulator/emulator" -avd "$AVD" -no-window -no-audio -no-snapshot-save -no-boot-anim >"$OUT/emulator.log" 2>&1 &
fi
"$ADB" -s "$SERIAL" wait-for-device
for _ in $(seq 1 150); do
  [ "$("$ADB" -s "$SERIAL" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ] && break
  sleep 2
done
"$ADB" -s "$SERIAL" install -r "$APK" >/dev/null

"$E2E/sandbox/run_api.sh" start --reset
# Al salir: los logs del backend de prueba van junto al reporte (CI los sube como artefacto) y se apaga.
trap 'cp "$E2E"/sandbox/*.log "$OUT"/ 2>/dev/null || true; "$E2E/sandbox/run_api.sh" stop >/dev/null' EXIT

args=(--driver script --serial "$SERIAL" --retries "${QA_RETRIES:-1}" --out "$OUT")
[ -n "$only" ] && args+=(--only "$only")
# El conductor solo necesita PyYAML: usa el mismo entorno del backend (convención: cd hubara_agency).
cd "$REPO/hubara_agency" && ADB="$ADB" uv run --no-sync python "$E2E/run_suite.py" "${args[@]}"
