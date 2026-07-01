#!/usr/bin/env bash
# agent-test.sh — runner de tests AISLADO y ACOTADO para agentes concurrentes.
#
# Nace de un incidente real (2026-07-01): varios agentes corriendo pytest en
# worktrees distintos + un proxy que vacía pipes + pytest bufferizado + un
# `pkill` GLOBAL que mató servers Temporal ajenos y propios en vuelo → un test
# quedó reintentando una conexión tcp por 50 minutos. Este script hace imposible
# repetir esa cadena de errores:
#
#   1. Salida SIEMPRE a archivo (nunca `| tail`) — inmune al proxy que corrompe
#      pipes. El agente lee el archivo con su Read tool.
#   2. `python -u` (unbuffered) — el progreso es visible aunque el proceso muera
#      (con buffering, matar el proceso pierde TODO el output → "0 líneas").
#   3. Timeout DURO (perl alarm) — resultado definitivo en <=N segundos, jamás
#      un cuelgue indefinido. Los tests de WorkflowEnvironment cuelgan local en
#      este host (lección institucional); acá mueren limpio y avisan.
#   4. Process group PROPIO (setsid) — el cleanup mata SOLO este árbol
#      (`kill -- -$PGID`), NUNCA un `pkill` global que toque a otro agente.
#   5. venv del worktree directo (sin `uv run`) — sin lock/sync que se cuelgue.
#
# REGLA DURA que acompaña a este script: prohibido `pkill`/`killall` global de
# `pytest`/`temporal`. Cleanup SOLO por el pgid que imprime este script, o vía
# el TaskStop del harness.
#
# Uso:
#   scripts/agent-test.sh [-t SECONDS] [-o OUTDIR] -- [pytest args...]
#   scripts/agent-test.sh [-t SECONDS] [pytest args...]
#
#     -t SECONDS   timeout duro (default 180). Al vencer: exit 142 (SIGALRM).
#     -o OUTDIR    dir de salida (default ${TMPDIR:-/tmp}/agent-test/<run_id>).
#
# Ejemplos:
#   scripts/agent-test.sh tests/test_coalesce_inbox.py -q      # rápido, no-Temporal
#   scripts/agent-test.sh -t 300 tests/                         # suite completa, 5min cap
#
# Corre desde cualquier subdir del worktree. Ver también la memoria
# `temporal_test_server_hang` / `burst_inbox_watermark`.
set -uo pipefail

TIMEOUT=180
OUTDIR=""
while getopts ":t:o:" opt; do
  case "$opt" in
    t) TIMEOUT="$OPTARG" ;;
    o) OUTDIR="$OPTARG" ;;
    :) echo "agent-test: -$OPTARG requiere argumento" >&2; exit 2 ;;
    \?) echo "agent-test: opción inválida -$OPTARG" >&2; exit 2 ;;
  esac
done
shift $((OPTIND - 1))
[ "${1:-}" = "--" ] && shift

# --- Resolver worktree, hubara_agency y el python del venv --------------------
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
AGENCY="$ROOT/hubara_agency"
[ -d "$AGENCY" ] || AGENCY="$(pwd)"
cd "$AGENCY" || { echo "agent-test: no pude cd a $AGENCY" >&2; exit 3; }

PY=""
for cand in "$ROOT/.venv/bin/python" "$AGENCY/.venv/bin/python" "$AGENCY/../.venv/bin/python"; do
  [ -x "$cand" ] && { PY="$cand"; break; }
done
[ -n "$PY" ] || { echo "agent-test: no encuentro el venv python (probé $ROOT/.venv)" >&2; exit 3; }

# --- Preparar salida ---------------------------------------------------------
RUN_ID="$(date +%Y%m%d-%H%M%S)-$$"
[ -n "$OUTDIR" ] || OUTDIR="${TMPDIR:-/tmp}/agent-test/$RUN_ID"
mkdir -p "$OUTDIR"
OUT="$OUTDIR/out.txt"

# Defaults seguros de pytest (el caller elige QUÉ correr; nosotros aislamos CÓMO).
# `-p no:cacheprovider`: sin cache compartido entre worktrees.
# `-o addopts=""`: ignora addopts del pyproject que podrían reintroducir plugins
#   ruidosos o cambiar el reporting.
PYTEST_ARGS=(-p no:cacheprovider -o "addopts=" "$@")

echo "agent-test: run_id=$RUN_ID timeout=${TIMEOUT}s py=$PY" >&2
echo "agent-test: out=$OUT" >&2

# Grupo propio + timeout duro + unbuffered, todo en un perl (macOS no tiene
# `setsid` como binario, pero sí `POSIX::setsid`). El `alarm` sobrevive al
# `exec`, y SIGALRM (default = terminate) mata al pytest al vencer el timeout.
PYTHONUNBUFFERED=1 perl -e 'use POSIX (); POSIX::setsid(); alarm shift; exec @ARGV' "$TIMEOUT" \
  "$PY" -u -m pytest "${PYTEST_ARGS[@]}" > "$OUT" 2>&1 &
CHILD=$!
# Tras POSIX::setsid el proceso es líder de su propia sesión → PGID == PID.
PGID="$CHILD"
echo "agent-test: pgid=$PGID  (cleanup SOLO propio: kill -- -$PGID)" >&2

# Si ESTE wrapper es interrumpido, matamos SOLO nuestro grupo (nunca global).
trap 'kill -- -"$PGID" 2>/dev/null || true' INT TERM

wait "$CHILD"; RC=$?
trap - INT TERM

echo "---- resumen (últimas 30 líneas de $OUT) ----" >&2
tail -n 30 "$OUT" >&2
if [ "$RC" -eq 142 ] || [ "$RC" -eq 124 ]; then
  echo "agent-test: TIMEOUT DURO a los ${TIMEOUT}s (exit=$RC). El proceso colgó" >&2
  echo "agent-test: (típico de WorkflowEnvironment local → deferí esos tests a CI)." >&2
fi
echo "agent-test: exit_code=$RC" >&2
exit "$RC"
