# Cómo correr un análisis de ads con Paperclip (guía operador)

Para vos. El motor ya está construido y probado; esto es cómo desplegarlo como un
pod de Paperclip y pedirle un análisis.

## 0. Pre-requisitos

- El server de Paperclip corriendo (ver `.paperclip/HU-RUNBOOK.md` §0 del repo raíz:
  `cd ~/Documents/Projects/paperclip && nohup pnpm paperclipai run >~/paperclip.log 2>&1 &`).
- El motor instalado: `cd ads-analytics-engine && ./scripts/setup.sh && ./scripts/verify.sh` (verde).
- El MCP oficial de Meta agregado en Claude (ver `../mcp/README.md`). **Si tu
  cuenta no tiene el beta del MCP, no se puede traer data todavía (por diseño)** —
  NO uses tokens crudos del Graph API ni brokers de terceros (riesgo de ban de la cuenta).

## El modelo en 3 frases

- Un **pod de 3 agentes**: **Analyst** (interpreta, lidera), **Data Engineer**
  (trae datos de Meta + ingiere ventas + corre el motor), **QA Reviewer** (reconcilia
  los números, no-self-review).
- **El LLM nunca calcula.** Todos los números salen de `ads-engine`. El QA re-corre
  el motor sobre los mismos inputs y exige que coincidan exactamente.
- Un **análisis = un issue** (un rango de fechas a auditar), o la routine diaria
  `daily-pull`.

## 1. Instalar el pod (una vez)

El team custom no se instala por catálogo app-shipped; se importa. Desde el clone:

```bash
cd ~/Documents/Projects/paperclip
# valida el paquete contra el motor de catálogo de Paperclip (oracle) — debe dar 0 errores
# (lo corremos en la verificación; ver abajo)
pnpm paperclipai company import --path /ABS/PATH/ads-analytics-engine/paperclip/team
```
Anotá los IDs que devuelve (company, proyecto `ads-analytics`, agentes
`analyst` / `data-engineer` / `qa-reviewer`). Poné budgets si querés
(`budget agent:update <id> --payload-json '{"budgetMonthlyCents":N}'`).

## 2. Pedir un análisis

### Camino A — board (a clicks)
1. Entrá a la company → proyecto **Ads Analytics** → **New Issue**.
2. **Title:** "Auditar CTWA 2026-06-01..2026-06-07".
   **Description:** el rango de fechas + dónde están las ventas manuales (path al
   JSON/CSV) + qué querés saber.
   **Assignee:** **Data Engineer** (trae datos y corre el motor) o **Analyst** (si
   querés que él coordine).
   **Reviewer:** **QA Reviewer** ← activa el no-self-review.
3. **Create.** El pod arranca solo.

### Camino B — terminal (no-self-review en un paso)
Igual que en `.paperclip/HU-RUNBOOK.md` (Camino B): `issue create` + el `PATCH
/api/issues/:id` con `executionPolicy.stages[]` apuntando al QA Reviewer.

## 3. Qué hace el pod

1. **Data Engineer** llama al MCP oficial de Meta (`get_insights`), guarda el JSON, e
   `ads-engine ingest-sales <tus-ventas>`.
2. `ads-engine compute --from-file <insights.json>` → `ads-engine report`.
3. **Analyst** redacta el read-out (interpretación + el flag del diagnóstico).
4. **QA Reviewer** re-corre el motor, reconcilia byte-a-byte, corre `verify.sh`,
   y aprueba/rechaza.

## 4. Sin Paperclip (modo manual, lo mismo sin la flota)

```bash
cd ads-analytics-engine
ads-engine ingest-sales mis-ventas.json
ads-engine compute --from-file insights-de-meta.json
ads-engine report                 # tabla markdown + diagnóstico
ads-engine report --format json   # para pipear
```

## Gotchas

- **Números a mano = defecto.** Si un agente escribe un número que no salió de
  `ads-engine`, rechazalo. Ese es el punto de todo el diseño.
- **COP only.** Si Meta devuelve otra moneda, el motor se niega (a propósito).
- **Fechas sin match** se reportan, no se ocultan. Si ves el warning de `compute`,
  es esperado: significa que un día estaba en un feed y no en el otro.
- **Auto-dispatch / budgets / frenar el pod:** igual que el pod de ingeniería —
  ver `.paperclip/HU-RUNBOOK.md` (auto-dispatch al crear issue, `agent pause`, etc.).
