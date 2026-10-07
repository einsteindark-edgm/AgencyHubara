#!/usr/bin/env bash
# Corre Artemis (github.com/google/artemis) con la llave de Gemini solo en el entorno del proceso:
# nunca se imprime ni se copia a disco. Uso: artemis.sh run "<objetivo>" -p flash -s emulator-5554
#   ARTEMIS_HOME      clon de Artemis ya sincronizado (por defecto ~/tools/artemis)
#   GEMINI_API_KEY    si ya está en el entorno se usa tal cual; si no, se lee de ARTEMIS_KEY_FILE
#   ARTEMIS_KEY_FILE  archivo con una línea GEMINI_API_KEY=… (p. ej. el .env del checkout principal)
#   ARTEMIS_DAEMON_PORT  puerto del servicio de Artemis (por defecto 8030; nunca 8000, el de la API local)
set -euo pipefail
ARTEMIS_HOME="${ARTEMIS_HOME:-$HOME/tools/artemis}"
if [ -z "${GEMINI_API_KEY:-}" ]; then
  [ -n "${ARTEMIS_KEY_FILE:-}" ] && [ -f "$ARTEMIS_KEY_FILE" ] || {
    echo "Falta GEMINI_API_KEY: expórtala o apunta ARTEMIS_KEY_FILE a un archivo que la tenga." >&2; exit 2; }
  _k=$(grep -E '^GEMINI_API_KEY=' "$ARTEMIS_KEY_FILE" | head -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")
  export GEMINI_API_KEY="$_k"; unset _k
fi
SDK="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
# Artemis levanta su servicio en 127.0.0.1:8000 si no se le dice otra cosa: ahí vive la API local de Docker
# (local-hubara-api, *:8000 por IPv6) y lo que pidiera 127.0.0.1:8000 le llegaba a Artemis. Siempre en otro puerto.
export ARTEMIS_DAEMON_PORT="${ARTEMIS_DAEMON_PORT:-8030}"
export PATH="$SDK/platform-tools:$PATH" PYTHONUNBUFFERED=1
PY="$ARTEMIS_HOME/.venv/bin/python"
[ -x "$PY" ] || { echo "Artemis no está sincronizado en $ARTEMIS_HOME (ver README)." >&2; exit 2; }
cd "$ARTEMIS_HOME"
exec "$PY" -m artemis "$@"
