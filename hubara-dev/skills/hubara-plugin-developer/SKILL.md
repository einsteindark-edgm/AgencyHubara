---
name: hubara-plugin-developer
description: |
  Harness de desarrollo full-stack para el monorepo AgencyHubara (backend
  Python DEHA/Temporal + frontend React/TS Feature-Sliced + plugin system +
  Platform SDK). Úsalo SIEMPRE que la tarea toque este repo: implementar o
  modificar un plugin, una activity/workflow/tool de un agente, una entity o
  feature del dashboard, el SDK (kits, certificación, CLI), los manifests, o
  cuando haya que correr los gates de arquitectura. Programa con TDD
  obligatorio (rojo→verde→refactor) y verifica contra el panel determinístico
  de §8. Dispara aunque el usuario no diga "skill": "agrega un plugin",
  "arregla este bug del agente sales", "nueva feature del dashboard",
  "implementá esta HU", "por qué falla el gate", "certificá el plugin",
  "refactor del workflow". NO uses este skill para repos ajenos a AgencyHubara.
---

# hubara-plugin-developer

El skill para programar en AgencyHubara **bien y de forma determinística**. La
fuente de verdad del conocimiento es `ARCHITECTURE_FINAL_fable.md` (en la raíz
del repo) + `docs/_sdk/`. Este skill no duplica ese conocimiento: te dice **el
método**, **cuándo leer qué**, y **a quién delegar** — y el harness (hooks +
subagents + comando) lo hace cumplir.

## La regla que gobierna todo: TDD primero (no negociable)

**No escribís una línea de código de producción sin un test que falla primero.**
Rojo → Verde → Refactor, en pasos de minutos. Esto no es ceremonia: es lo que
hace que el cambio sea correcto por construcción y que los gates de §8 pasen
sin sorpresas. El detalle completo (las 3 leyes, qué harness usa cada capa,
qué NO es TDD) está en **`references/00-tdd-law.md`** — léelo antes de tocar
código si no lo tenés fresco. El hook `tdd-guard` te lo va a recordar en cada
edición de producción; el hook `affected-tests` corre el test afectado tras
cada edit y te muestra 🔴/🟢. No pelees con ellos: son tu red.

## El bucle de trabajo (cada tarea pasa por acá)

1. **Orientate** — entendé QUÉ vas a cambiar y QUÉ se rompería. Para mapear un
   subsistema antes de editar, delegá en el subagent **`hubara-explorer`**
   (read-only, no contamina tu contexto). Para la receta del cambio, mirá
   `references/02-recipes.md` (apunta a §4).
2. **ROJO** — escribí el test del siguiente incremento de comportamiento y
   **velo fallar con un assert con sentido** (no un ImportError). Si dudás del
   test, delegá en **`hubara-tdd-author`**. El harness por capa (dominio,
   activity, workflow, tool, entity, feature, gate) está en `00-tdd-law.md`.
3. **VERDE** — el mínimo código de producción para pasar ese test. Nada más.
4. **REFACTOR** — limpiá test y producción con la red de seguridad puesta,
   manteniendo verde.
5. **VERIFICÁ** — corré el panel determinístico (`/hubara-gates` o
   `references/03-command-panel.md`). Antes de "terminado", la
   definition-of-done de §8 tiene que estar verde.

## Antes de editar: las reglas duras (qué te frena cada gate)

El repo tiene gates que bloquean violaciones de arquitectura (aislamiento de
plugins, FSD, DEHA, manifests). Si vas a tocar imports cross-plugin, entities,
manifests, workers o deploy, leé **`references/01-hard-rules.md`** primero —
te ahorra el ciclo "editar → gate rojo → deshacer". Tabla completa en §3.

## Cuándo leer cada referencia

| Necesitás… | Leé |
|---|---|
| El método TDD + harness por capa | `references/00-tdd-law.md` (§3.5) |
| Saber qué gate te va a frenar y el fix | `references/01-hard-rules.md` (§3) |
| La receta paso-a-paso de un cambio típico | `references/02-recipes.md` (§4) |
| Los comandos exactos de verificación | `references/03-command-panel.md` (§8) |
| Si esto ya nos mordió antes (qué NO repetir) | `references/04-lessons.md` (§9, L-0..L-15) |
| La superficie del SDK (kits, certificación, CLI) | `references/05-sdk-surface.md` (docs/_sdk) |

Cuando una de estas referencias contradiga al código vivo, **gana el código
vivo** — y esa contradicción es una lección nueva para §9 de la semilla.

## Los subagents del harness (cuándo delegar)

- **`hubara-explorer`** — mapea un subsistema antes de que edites. Read-only;
  devuelve el mapa, no toca nada. Usalo cuando no conocés la zona.
- **`hubara-tdd-author`** — escribe el test que falla primero (fase roja) para
  un incremento que vos definís. Usalo cuando el test no es obvio o querés
  presión de diseño antes de implementar.
- **`hubara-gate-reviewer`** — corre el panel §8 y audita el diff contra las
  reglas duras (§3) y las lecciones (§9). Usalo antes de cerrar/PR.

Delegar te ahorra contexto y trae una perspectiva fresca. No delegues lo
trivial; sí lo que requiere barrer muchos archivos o una mirada independiente.

## Cerrar un cambio

Corré `/hubara-gates` (o el bloque de §8.1). Verde los dos planos (backend +
frontend) = mergeable por arquitectura. Si tocaste paths PROTECTED, el PR lleva
el label `architecture-change` (cómo lo ve CI: lección L-14). Cambio de
comportamiento visible ⇒ verificá contra el stack Docker real — tests verdes ≠
feature viva (gotcha #1, §8.7).
