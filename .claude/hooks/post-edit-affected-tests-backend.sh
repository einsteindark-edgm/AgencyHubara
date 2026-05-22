#!/bin/bash
# post-edit-affected-tests-backend.sh — PostToolUse hook (opt-in)
# Eleva T16 (selección de tests afectados vía hook, determinístico).
#
# Después de Edit/Write a un *.py bajo hubara_agency/src/, corre los tests "relacionados":
#   - Heuristic mapping: src/path/foo.py → tests/path/test_foo.py
#   - Imports inversos: tests que tienen `from src.module.path` apuntando al edit
#
# Es OPT-IN: solo corre si env var CLAUDE_AFFECTED_TESTS=1 está set.
# Razón: correr pytest en cada edit interactivo agrega 5-30s de latencia. El operador
# decide cuándo activarlo (e.g., al final de una task antes de marcar como done).
#
# Output: stderr con resultado. NO bloquea (PostToolUse no puede).

set +e

# === Gate: opt-in ===
if [[ "${CLAUDE_AFFECTED_TESTS:-0}" != "1" ]]; then
  exit 0
fi

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // ""')

# Skip non-Python
if [[ "$FILE_PATH" != *.py ]]; then exit 0; fi
# Skip test files themselves
if [[ "$FILE_PATH" == *"/tests/"* ]]; then exit 0; fi
# Solo bajo hubara_agency/src/
if [[ "$FILE_PATH" != *"/hubara_agency/src/"* ]]; then exit 0; fi
# Skip __pycache__ y .pyc
if [[ "$FILE_PATH" == *"/__pycache__/"* ]] || [[ "$FILE_PATH" == *.pyc ]]; then exit 0; fi

REPO_ROOT="${CLAUDE_PROJECT_DIR:-/Users/edgm/Documents/Projects/AgencyHubara}"
cd "$REPO_ROOT/hubara_agency" || exit 0

if ! command -v uv &> /dev/null; then exit 0; fi

# === Resolver candidatos de tests ===
TESTS_TO_RUN=()

# 1) Heuristic mapping: src/A/B/C.py → tests/A/B/test_C.py
REL_FROM_HUBARA="${FILE_PATH#*hubara_agency/}"  # e.g., src/plugins/chats/agent/tools/foo.py
REL_FROM_SRC="${REL_FROM_HUBARA#src/}"            # e.g., plugins/chats/agent/tools/foo.py
BASE_NAME="$(basename "$REL_FROM_SRC" .py)"       # e.g., foo
DIR_PATH="$(dirname "$REL_FROM_SRC")"             # e.g., plugins/chats/agent/tools

HEURISTIC_TEST="tests/${DIR_PATH}/test_${BASE_NAME}.py"
if [[ -f "$HEURISTIC_TEST" ]]; then
  TESTS_TO_RUN+=("$HEURISTIC_TEST")
fi

# Algunos plugins drop el "/agent/" intermedio en sus tests
# src/plugins/chats/agent/tools/foo.py → tests/plugins/chats/tools/test_foo.py
HEURISTIC_TEST_ALT="tests/$(echo "$DIR_PATH" | sed 's|/agent/|/|')/test_${BASE_NAME}.py"
if [[ "$HEURISTIC_TEST_ALT" != "$HEURISTIC_TEST" ]] && [[ -f "$HEURISTIC_TEST_ALT" ]]; then
  TESTS_TO_RUN+=("$HEURISTIC_TEST_ALT")
fi

# 2) Imports inversos: tests que importan este módulo
MODULE_PATH=$(echo "${REL_FROM_HUBARA%.py}" | tr '/' '.')  # e.g., src.plugins.chats.agent.tools.foo
EXTRA=$(grep -lrE "from[[:space:]]+${MODULE_PATH//./\\.}\b" tests/ 2>/dev/null | head -5)
if [[ -n "$EXTRA" ]]; then
  while IFS= read -r line; do
    if [[ -n "$line" ]]; then
      TESTS_TO_RUN+=("$line")
    fi
  done <<< "$EXTRA"
fi

# Dedup
if [[ ${#TESTS_TO_RUN[@]} -eq 0 ]]; then
  echo "🧪 affected-tests (backend): no tests heurísticos encontrados para $FILE_PATH" >&2
  exit 0
fi

# Sort + uniq
DEDUPED=()
while IFS= read -r line; do
  DEDUPED+=("$line")
done < <(printf "%s\n" "${TESTS_TO_RUN[@]}" | sort -u)

# === Run with timeout ===
echo "🧪 affected-tests (backend): corriendo ${#DEDUPED[@]} test file(s) para $(basename "$FILE_PATH")..." >&2

TEST_OUTPUT=$(timeout 60 uv run pytest "${DEDUPED[@]}" --tb=short --no-header -q -x 2>&1)
TEST_EXIT=$?

if [[ $TEST_EXIT -eq 124 ]]; then
  echo "⏱️  affected-tests (backend): timeout 60s — corré manualmente: uv run pytest ${DEDUPED[*]}" >&2
elif [[ $TEST_EXIT -ne 0 ]]; then
  echo "❌ affected-tests (backend): FAILED tras edit a $FILE_PATH" >&2
  echo "$TEST_OUTPUT" | tail -30 >&2
else
  echo "✅ affected-tests (backend): ${#DEDUPED[@]} tests pasaron" >&2
fi

exit 0
