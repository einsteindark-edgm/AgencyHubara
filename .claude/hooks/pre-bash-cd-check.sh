#!/bin/bash
# pre-bash-cd-check.sh — PreToolUse hook
# Eleva T13 (deterministic enforcement). Sustituye la regla "el modelo debe recordar
# cd hubara_agency && / cd frontend_dashboard &&" del project-context.md §2.
#
# Intercepta Bash y bloquea si:
#   - command contiene `uv run` y NO contiene `cd hubara_agency`
#   - command contiene `npm `, `npx `, `tsc ` (top-level) y NO contiene `cd frontend_dashboard`
#
# Permisivo con casos informacionales (`uv --version`, `npm --version`).
# Permisivo con RTK wrappers (`rtk uv ...`, `rtk npm ...`) — el wrapper resuelve cwd.
#
# Input: JSON via stdin con {tool_name, tool_input.command}
# Output:
#   - exit 0: allow
#   - exit 2 + JSON denial a stdout: block con razón
#
# Docs: https://code.claude.com/docs/en/hooks.md

set -e

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // ""')

# No command = nada que validar
if [[ -z "$COMMAND" ]]; then
  exit 0
fi

deny() {
  local REASON="$1"
  jq -n --arg reason "$REASON" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $reason
    }
  }'
  exit 2
}

# === Permisivos (allow sin verificación) ===

# RTK wrappers — el wrapper ya resuelve cwd
if echo "$COMMAND" | grep -qE '^[[:space:]]*rtk[[:space:]]+'; then
  exit 0
fi

# Informacionales (version / help)
if echo "$COMMAND" | grep -qE '\b(uv|npm|npx|tsc)[[:space:]]+--(version|help)'; then
  exit 0
fi

# Comandos que ya tienen `cd hubara_agency` o `cd frontend_dashboard` correcto
if echo "$COMMAND" | grep -qE 'cd[[:space:]]+hubara_agency[[:space:]]*&&'; then
  exit 0
fi

if echo "$COMMAND" | grep -qE 'cd[[:space:]]+frontend_dashboard[[:space:]]*&&'; then
  exit 0
fi

# === Verificaciones de denegación ===

# F3 fix: detectar bypass via subshell (bash -c / sh -c / pipe a sh / xargs)
# Si command embed `uv run` o `npm` dentro de un subshell, también enforce.

# uv run sin cd hubara_agency (incluye embedded en bash -c)
if echo "$COMMAND" | grep -qE '\buv[[:space:]]+run\b'; then
  deny "El comando 'uv run' debe ir con prefijo 'cd hubara_agency &&'. Convención del project-context.md §2. Reescribí: cd hubara_agency && $COMMAND"
fi

# npm / npx / tsc top-level sin cd frontend_dashboard
if echo "$COMMAND" | grep -qE '^[[:space:]]*(npm|npx|tsc)[[:space:]]+'; then
  deny "El comando '$(echo "$COMMAND" | awk '{print $1}')' debe ir con prefijo 'cd frontend_dashboard &&'. Convención del project-context.md §2. Reescribí: cd frontend_dashboard && $COMMAND"
fi

# F3 fix: npm/npx/tsc embedded en bash -c / sh -c sin cd
if echo "$COMMAND" | grep -qE '(bash|sh)[[:space:]]+-c[[:space:]]+["'\''].*\b(npm|npx|tsc)[[:space:]]+'; then
  if ! echo "$COMMAND" | grep -qE 'cd[[:space:]]+frontend_dashboard'; then
    deny "Bypass detectado: 'npm/npx/tsc' embedded en subshell (bash -c) sin 'cd frontend_dashboard &&'. NO bypaseés la convención usando subshells."
  fi
fi

# pipe a sh / xargs sh con uv run / npm
if echo "$COMMAND" | grep -qE '\|[[:space:]]*(sh|bash|xargs[[:space:]]+(sh|bash))\b'; then
  if echo "$COMMAND" | grep -qE '\b(uv[[:space:]]+run|npm|npx|tsc)\b'; then
    deny "Bypass detectado: comando con uv/npm/tsc piped a sh/bash. NO bypaseés la convención. Usá el comando directo con su prefijo cd correcto."
  fi
fi

# Default: allow
exit 0
