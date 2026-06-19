---
description: Corre el panel determinístico de gates de GraphAgents — manifests (cli check), tools (per-tool TCK), arquitectura (G-*), certificación (TCK C0–C3), determinismo (golden-replay) e integración (runtime + recovery). Uso: /graphagents-gates [tools|arch|cert|graphs|integration|manifests|all].
argument-hint: "[tools|arch|cert|graphs|integration|manifests|all]"
---

Corré el panel determinístico de verificación de GraphAgents (la
definition-of-done) ejecutando el script del harness:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/run-gates.sh" ${ARGUMENTS:-all}
```

Reportá el resultado de cada gate (✓/✗ con su exit code) y el veredicto final tal
cual los emite el script. Si algún gate sale ✗:

- **No apliques fixes automáticamente** desde acá: reportá los hallazgos y, si el
  usuario quiere, abordalos test-first (rojo → verde → refactor).
- Para una **capability** roja, el rojo correcto es un golden-replay (fixture →
  output exacto); para el **SDK/manifest**, el caso negativo del check del TestKit.
- Recordá: **tests verdes ≠ feature viva** — un cambio de comportamiento se
  verifica corriendo el grafo real sobre AgentSpan (recovery por `execution-id`,
  HUMAN tasks de las acciones con `approval_required`).
