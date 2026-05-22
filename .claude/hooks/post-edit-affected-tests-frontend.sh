#!/bin/bash
# post-edit-affected-tests-frontend.sh — PostToolUse hook (opt-in)
# Eleva T16. Análogo del backend pero usa `vitest related` (built-in de Vitest)
# que traza imports transitivos para encontrar test files.
#
# OPT-IN: solo corre si CLAUDE_AFFECTED_TESTS=1.

set +e

if [[ "${CLAUDE_AFFECTED_TESTS:-0}" != "1" ]]; then
  exit 0
fi

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // ""')

# Solo *.ts y *.tsx
if [[ "$FILE_PATH" != *.ts ]] && [[ "$FILE_PATH" != *.tsx ]]; then exit 0; fi
# Skip test files (no test the tests)
if [[ "$FILE_PATH" == *.test.* ]] || [[ "$FILE_PATH" == *.spec.* ]]; then exit 0; fi
# Solo bajo frontend_dashboard/src/
if [[ "$FILE_PATH" != *"/frontend_dashboard/src/"* ]]; then exit 0; fi
# Skip _schema (codegen)
if [[ "$FILE_PATH" == *"/_schema/"* ]]; then exit 0; fi

REPO_ROOT="${CLAUDE_PROJECT_DIR:-/Users/edgm/Documents/Projects/AgencyHubara}"
cd "$REPO_ROOT/frontend_dashboard" || exit 0

if [[ ! -f "node_modules/.bin/vitest" ]]; then
  exit 0  # vitest no disponible, skip silently
fi

# `vitest related <file>` corre solo los tests que importan transitivamente <file>.
# Built-in de Vitest, ideal para esto.
echo "🧪 affected-tests (frontend): vitest related para $(basename "$FILE_PATH")..." >&2

VITEST_OUTPUT=$(timeout 60 ./node_modules/.bin/vitest related --run --reporter=default "$FILE_PATH" 2>&1)
VITEST_EXIT=$?

if [[ $VITEST_EXIT -eq 124 ]]; then
  echo "⏱️  affected-tests (frontend): timeout 60s — corré manualmente: npx vitest related --run $FILE_PATH" >&2
elif [[ $VITEST_EXIT -ne 0 ]]; then
  echo "❌ affected-tests (frontend): FAILED tras edit a $FILE_PATH" >&2
  echo "$VITEST_OUTPUT" | tail -30 >&2
else
  # Vitest exit 0 puede significar "no tests found" — chequear output
  if echo "$VITEST_OUTPUT" | grep -q "No test files found"; then
    echo "🧪 affected-tests (frontend): no tests relacionados a $FILE_PATH" >&2
  else
    echo "✅ affected-tests (frontend): tests relacionados pasaron" >&2
  fi
fi

exit 0
