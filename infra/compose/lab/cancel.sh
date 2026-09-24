#!/bin/bash
# Cancelar una corrida del laboratorio (§3.7). Llega por SSM:
#
#   /opt/lab/cancel.sh <RUN_ID>
#
# Deja la marca CANCEL: el runner la ve entre turnos, sube lo que alcanzó a
# correr a S3 y termina. La caja se apaga sola cuando no queda trabajo.
# Idempotente. Imprime: cancel_requested.
set -euo pipefail

LAB_ROOT="${LAB_ROOT:-/lab}"
RUN_ID="${1:-}"
[[ "$RUN_ID" =~ ^[a-z0-9][a-z0-9-]{5,63}$ ]] || { echo "invalid_run_id" >&2; exit 2; }

mkdir -p "$LAB_ROOT/runs/$RUN_ID"
touch "$LAB_ROOT/runs/$RUN_ID/CANCEL"
echo "cancel_requested"
