---
description: Corre el panel determinístico de gates de AgencyHubara (§8) — backend (R-DIP, arquitectura, certificación, CLI) y/o frontend (FSD, íconos, meta-gate). Uso: /hubara-gates [backend|frontend|all].
argument-hint: "[backend|frontend|all]"
---

Corré el panel determinístico de verificación de AgencyHubara (la
definition-of-done de §8) ejecutando el script del harness:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/run-gates.sh" ${ARGUMENTS:-all}
```

Reportá el resultado de cada gate (✓/✗ con su exit code) y el veredicto final
tal cual los emite el script. Si algún gate sale ✗:

- Si es un gate de allowlist/ratchet y un repro local pasaba, sospechá
  **staleness** (L-15): CI testea el merge con main → mergeá main + regenerá el
  ratchet, no edites a mano.
- Si tocaste paths PROTECTED, los pytest necesitan el prefijo
  `ARCH_CHANGE_APPROVED=1` y el PR el label `architecture-change` (L-14).
- Los 3 fallos PRE-existentes en `tests/plugins/chats` (voseo + 2 watchdog) no
  son del cambio; cualquier OTRO rojo sí.

No apliques fixes automáticamente desde acá: reportá los hallazgos y, si el
usuario quiere, abordalos test-first (rojo → verde → refactor).
