#!/bin/sh
# Entrypoint del container GraphAgents — prepara git/gh para el 'publicar ▸' del explorer.
#
# El publish (commit quirúrgico → push → PR) corre DENTRO del container contra el
# checkout bind-monteado (ver docker-compose.yml: la RAÍZ del monorepo en /workspace).
# Sin GH_TOKEN el explorer levanta igual: explorar/editar/guardar funcionan, y el
# botón publicar degrada honesto al apretar (push/PR fallan con error legible).
set -e

if command -v git >/dev/null 2>&1; then
  # el bind-mount llega con dueño distinto al uid del container → git se rehúsa a
  # operar ("dubious ownership"); en un container de dev el mount es confiable.
  git config --global --add safe.directory '*' 2>/dev/null || true
fi

if [ -n "${GH_TOKEN:-}" ] && command -v gh >/dev/null 2>&1; then
  # UN solo token para las dos herramientas: gh (crear el PR) + git push por HTTPS
  # (setup-git registra a gh como credential helper de git).
  gh auth setup-git 2>/dev/null || true
fi

exec "$@"
