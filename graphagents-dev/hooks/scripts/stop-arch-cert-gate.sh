#!/usr/bin/env bash
# Stop hook (plugin graphagents-dev): cuando la sesión tocó archivos de
# arquitectura/manifests/sdk/grafos de GraphAgents, corre los gates de
# certificación + arquitectura y reporta. Es la red que pediste: los tests de
# cert/arch corren SOLOS al cerrar, no dependen de que el agente se acuerde.
#
# Por defecto NO bloquea (informa por stderr, visible en el transcript). Para
# que un panel rojo BLOQUEE el stop y fuerce a resolverlo: export
# GRAPHAGENTS_STOP_GATE_BLOCK=1
set -uo pipefail

ROOT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
GA="$ROOT/GraphAgents"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Consumir stdin (payload del evento Stop) para no romper el pipe.
cat >/dev/null 2>&1 || true

[[ -d "$GA" ]] || exit 0
command -v git >/dev/null 2>&1 || exit 0

# ¿Cambió algo relevante de GraphAgents en el working tree?
changed="$(git -C "$ROOT" status --porcelain -- GraphAgents 2>/dev/null | awk '{print $2}')"
relevant="$(printf '%s\n' "$changed" | grep -E 'GraphAgents/(manifests|sdk|graphs|tools)/' || true)"
[[ -z "$relevant" ]] && exit 0   # nada de arquitectura cambió -> no corras gates

command -v uv >/dev/null 2>&1 || { printf 'graphagents-dev (Stop): `uv` no está en PATH; salteo los gates.\n' >&2; exit 0; }

printf 'graphagents-dev (Stop): cambios de arquitectura en GraphAgents — corriendo cert + arquitectura...\n' >&2
out="$(CLAUDE_PROJECT_DIR="$ROOT" bash "$SCRIPT_DIR/run-gates.sh" all 2>&1)"; rc=$?
printf '%s\n' "$out" >&2

if [[ "$rc" -ne 0 ]]; then
  if [[ "${GRAPHAGENTS_STOP_GATE_BLOCK:-0}" == "1" ]]; then
    # exit 2 -> Claude Code bloquea el Stop y devuelve stderr como motivo.
    printf '\nGRAPHAGENTS_STOP_GATE_BLOCK=1: el panel está ROJO. Resolvé los gates antes de cerrar (test-first).\n' >&2
    exit 2
  fi
  printf '\ngraphagents-dev (Stop): panel ROJO (informativo). Para bloquear el cierre con rojo: export GRAPHAGENTS_STOP_GATE_BLOCK=1\n' >&2
fi
exit 0
