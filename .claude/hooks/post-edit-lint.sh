#!/bin/bash
# post-edit-lint.sh — PostToolUse hook
# Eleva T13 (deterministic enforcement). Reemplaza el "deberías recordar correr ruff/eslint
# después de tocar código" con un fix automático.
#
# Después de Edit/Write a:
#   - *.py    → ruff check --fix --quiet (rápido, <1s)
#   - *.ts/.tsx → eslint --fix --quiet (más lento, ~3s)
#
# Resultado a stderr para que el modelo lo vea en el próximo turno.
# NO bloquea — PostToolUse no puede.
#
# Input: JSON via stdin con {tool_name, tool_input.file_path, tool_result}
# Output: exit 0 siempre. Stderr para informar.

set +e

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // ""')

# Sin file_path = nada que linterar
if [[ -z "$FILE_PATH" ]]; then
  exit 0
fi

# Skip si el archivo no existe (edit fallido)
if [[ ! -f "$FILE_PATH" ]]; then
  exit 0
fi

# Skip archivos fuera del proyecto (path absoluto debe contener AgencyHubara)
if [[ "$FILE_PATH" != *"/AgencyHubara/"* ]]; then
  exit 0
fi

# Skip archivos generados / vendor
if [[ "$FILE_PATH" == *"/node_modules/"* ]] || \
   [[ "$FILE_PATH" == *"/.venv/"* ]] || \
   [[ "$FILE_PATH" == *"/__pycache__/"* ]] || \
   [[ "$FILE_PATH" == *"/dist/"* ]] || \
   [[ "$FILE_PATH" == *"/_schema/"* ]]; then
  exit 0
fi

REPO_ROOT="${CLAUDE_PROJECT_DIR:-/Users/edgm/Documents/Projects/AgencyHubara}"

# === Python ===
if [[ "$FILE_PATH" == *.py ]]; then
  # ruff debe estar instalado en el venv de hubara_agency
  cd "$REPO_ROOT/hubara_agency" 2>/dev/null || exit 0

  if ! command -v uv &> /dev/null; then
    exit 0  # uv no disponible, skip silently
  fi

  RUFF_OUTPUT=$(uv run --quiet ruff check --fix --quiet "$FILE_PATH" 2>&1)
  RUFF_EXIT=$?

  if [[ $RUFF_EXIT -ne 0 ]] && [[ -n "$RUFF_OUTPUT" ]]; then
    echo "🔧 ruff (post-edit hook) reportó issues en $FILE_PATH:" >&2
    echo "$RUFF_OUTPUT" >&2
    echo "(post-edit-lint.sh — fix manualmente si ruff no pudo auto-fixear)" >&2
  fi
  exit 0
fi

# === TypeScript / TSX ===
if [[ "$FILE_PATH" == *.ts ]] || [[ "$FILE_PATH" == *.tsx ]]; then
  cd "$REPO_ROOT/frontend_dashboard" 2>/dev/null || exit 0

  if [[ ! -f "node_modules/.bin/eslint" ]]; then
    exit 0  # eslint no disponible (npm install pendiente?), skip silently
  fi

  # eslint con timeout suave para no bloquear el flujo
  ESLINT_OUTPUT=$(timeout 10 ./node_modules/.bin/eslint --fix --quiet "$FILE_PATH" 2>&1)
  ESLINT_EXIT=$?

  if [[ $ESLINT_EXIT -ne 0 ]] && [[ -n "$ESLINT_OUTPUT" ]]; then
    echo "🔧 eslint (post-edit hook) reportó issues en $FILE_PATH:" >&2
    echo "$ESLINT_OUTPUT" >&2
    echo "(post-edit-lint.sh — fix manualmente si eslint no pudo auto-fixear)" >&2
  fi
  exit 0
fi

# Otros tipos: no-op
exit 0
