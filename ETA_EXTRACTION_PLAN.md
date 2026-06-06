# ETA Extraction Plan — la última extracción del refactor de aislamiento

> **Por qué existe este doc.** Para sobrevivir un `/compact`. Después del compact,
> el yo-futuro (sin memoria de esta sesión) ejecuta `eta` leyendo SOLO este doc +
> [PLUGIN_CONTRACT.md](PLUGIN_CONTRACT.md). Es la parte más difícil del refactor,
> pero **mucho menos difícil de lo que el contrato §5.1 temía** (ver §1).
>
> **Contexto mínimo.** Estamos haciendo multi-tenant: cada tenant prende/apaga
> plugins. La auditoría ([PLUGIN_ISOLATION_AUDIT.md](PLUGIN_ISOLATION_AUDIT.md))
> encontró "split plugins": backend de un plugin viviendo dentro de `chats`. Ya
> extrajimos `ads` (commit `a5bc586` — usar como TEMPLATE). `evals` se decidió
> **NO extraer** (queda per-agente). Queda **solo `eta`**.
>
> **Branch:** `claude/reverent-shirley-08c071`. **Estado:** 6 commits (docs +
> guards + dispatcher-skip + ads + decisión evals). Nada en main. PR #48 (deuda
> sales_eval) va aparte a main.

---

## §1. El hallazgo clave (corrige el miedo del contrato §5.1)

El contrato §5.1 asumía que extraer `eta` requería extraer un gran
`platform/conversation` (session runtime + vault + WhatsApp edge). **FALSO.**
Verificado leyendo `chats/workers/eta.py`:

> El worker de eta importa **`exoclaw_temporal`** (session runtime: `build_prompt`,
> `record_turn`, `llm_chat`) + **`src.platform.*`** (logging, observability,
> session_history, temporal.activities `execute_tool`, temporal.dispatcher,
> tool_extensions, tools.escalation `EscalateToHumanTool`, tools.routing
> `TransferToSalesAgentTool`, whatsapp.activities) + **solo su propio**
> `src.plugins.chats.agent.eta.*`. **NO importa nada de sales/remarketing.**

→ **El runtime conversacional compartido YA vive en `exoclaw_temporal` + `platform/`.**
No hay que crear `platform/conversation`. `eta` es import-clean: como `ads`, es un
**git mv + repoint** + manejar su worker (k8s/compose) + retargetear transitions.

**La única coupling con `chats` es el ruteo de inbounds** (`load_or_start_sales_session`)
y es **string-based + tolerante** (§3) — NO un import. No viola R-DIP.

---

## §2. Inventario exacto (qué se mueve)

**Backend (todo bajo `hubara_agency/src/`):**
```
plugins/chats/agent/eta/                 → plugins/eta/agent/
  __init__.py
  activities/{__init__,bootstrap_session,tracking}.py   (tracking.py importa platform.orders.composition.get_order_query_port — platform, OK)
  config/{__init__,env}.py
  contracts.py
  prompts.py
  workflows/{__init__,eta_session}.py     (HubaraEtaSessionWorkflow)
plugins/chats/workers/eta.py             → plugins/eta/workers/eta.py   (worker name "eta", task_queue queue-eta-agent)
plugins/chats/api/eta.py                 → plugins/eta/api/__init__.py  (router; importa platform.config + platform.constants + order query port)
plugins/eta/__init__.py (shell vacío HOY)→ reemplazar por package real
```

**Repoint de imports** (igual que ads): `src.plugins.chats.agent.eta` → `src.plugins.eta.agent`
en todos los archivos movidos (el worker `chats/workers/eta.py` importa
`src.plugins.chats.agent.eta.activities` + `...workflows.eta_session`).

**Verificar consumidores** antes de mover (gotcha ads §6): correr
`rtk proxy grep -rn "chats\.agent\.eta\|chats\.workers\.eta\|chats\.api\.eta\|chats/api/eta" hubara_agency frontend_dashboard`
y repointar TODO (incluye tests + comentarios). Caso conocido: `chats/api/eta.py`
docstring ya menciona el patrón ads (actualizar).

**Manifests (`frontend_dashboard/src/plugins/`):**
- `chats/plugin.yaml`: REMOVER el worker `eta` de `agent.workers[]` (líneas ~380-434,
  el bloque `- name: eta` con su `dashboard:`/`workflow_classes:`/`deployment:`/`compose:`)
  + REMOVER el legacy_router `{ module: src.plugins.chats.api.eta, prefix: /api/chats, tags: [ETA] }`.
- `eta/plugin.yaml`: AGREGAR `api:` (python_module: src.plugins.eta.api, prefix: /api/eta, tags: [ETA])
  + `agent:` con el worker `eta` (module: src.plugins.eta.workers.eta, task_queue: queue-eta-agent,
  workflow_classes: [HubaraEtaSessionWorkflow], + el `dashboard:` block del agente ETA, + `deployment`/`compose`
  copiados del chats manifest). Declarar `agent.owns_route: eta` (§3 opción B) si se hace el route registry.
  Hoy `eta/plugin.yaml` es frontend-only (igual que ads lo era — el manifest ads ya anticipaba su `api:`).

**Orders transitions (`frontend_dashboard/src/plugins/orders/plugin.yaml`):**
- Las 5 transitions targetean `target_plugin: chats, target_worker: eta`. Cambiar a
  `target_plugin: eta, target_worker: eta` (el worker name sigue "eta"). NO agregar
  `orders.depends_on: [eta]` — las transitions son SOFT y el dispatcher las skipea si
  `eta` está apagado (P-SKIP, commit `7d02080`). Esto es justo lo que valida el dispatcher-skip.

**Frontend (`frontend_dashboard/src/`):**
- El plugin `eta/frontend` ya existe (frontend-only). El que llama a la API es la entity
  central `entities/tracked-order` (la eta manifest dice "datos vienen de entities/tracked-order").
  Buscar `/api/chats/eta` en `src/entities/tracked-order/*` (+ donde sea) y cambiar a `/api/eta`.
  (Patrón idéntico a `entities/ads-campaign` en la extracción ads.)
- Regenerar `npm run plugins:sync` (el registry no debería cambiar — eta ya estaba).

**K8s + compose (la diferencia vs ads — eta TIENE worker):**
- `hubara_agency/k8s/aws-produccion/worker-eta.yaml`: cambiar el `command` de
  `hubara_agency.src.plugins.chats.workers.eta` → `hubara_agency.src.plugins.eta.workers.eta`.
- Regenerar compose: `cd hubara_agency && uv run python scripts/render-compose.py`
  (el service `hubara-worker-chats-eta` → `hubara-worker-eta-eta` o similar; el render usa
  `(plugin, worker)` del manifest). Verificar `pytest tests/plugins/test_premortem_invariants.py`.
- ⚠️ OJO: el premortem invariant infiere `(plugin, worker)` del command con regex
  `src\.plugins\.([a-z_]+)\.workers\.([a-z_]+)`. Con el nuevo command da `(eta, eta)` → debe matchear
  el worker `eta` declarado en el manifest `eta`. Coherente.

---

## §3. La coupling real: el ruteo de inbounds (string-based, tolerante)

`plugins/chats/agent/sales/use_cases/load_or_start_sales_session.py` (líneas ~178-191)
es el entrypoint que rutea cada inbound de WhatsApp al agente dueño de `active_route`:

```python
if active_route == ROUTE_ETA:                    # ROUTE_ETA de platform.constants
    workflow_id = f"eta-{session_id}"            # ← string template, NO import de la clase
    handle = client.get_workflow_handle(workflow_id)
    desc = await handle.describe()
    if desc.status != WorkflowExecutionStatus.RUNNING:
        raise RuntimeError(...)                   # → except → active_route = ROUTE_VENTAS (fallback a Sales)
```

**Clave:** NO importa `HubaraEtaSessionWorkflow` (solo está en un comentario). Signal
por string (`send_message`). **Es tolerante**: si el workflow eta no corre (eta apagado),
cae a Sales. → **`eta` es toggleable AUNQUE este ruteo se quede en chats.**

### Dos niveles (decidir al ejecutar):

- **Opción A — MÍNIMO (recomendado para el primer PR):** dejar el ruteo como está. El
  `ROUTE_ETA` constant (platform.constants — vocabulario compartido) + el template
  `eta-{session_id}` se quedan en chats. Es soft (string) + tolerante. `eta` se vuelve
  plugin self-contained y toggleable. **Esto desbloquea el objetivo multi-tenant.**
  Cuando `eta` está apagado: orders→eta transition skipeada (P-SKIP) → el workflow eta
  nunca arranca → `active_route` nunca se vuelve `eta` → el branch ROUTE_ETA nunca corre.
  Cero breakage.

- **Opción B — LIMPIO (route registry, PR siguiente, opcional):** mover el mapping
  `active_route → (plugin, workflow_id_template)` a un registry declarativo leído de
  manifests (cada agente declara `agent.owns_route`). `load_or_start_sales_session` deja de
  hardcodear `ROUTE_ETA → eta-{session}`. Es la visión del contrato §5.1, pero **NO es
  bloqueante** para toggleability. Diferir hasta que haya un 2º agente con ruta propia.

> Recomendación: **Opción A** en el PR de extracción de eta. Anotar B como follow-up.

---

## §4. Plan paso a paso (cada paso mantiene tests verdes — patrón ads `a5bc586`)

1. **`grep` de consumidores** de `chats.{agent.eta, workers.eta, api.eta}` (todo el repo,
   py+yaml+ts). Anotar para repointar TODO (incl. tests + comentarios).
2. **`git mv`** los 3 grupos a `plugins/eta/{agent,workers/eta.py,api/__init__.py}` + crear
   `plugins/eta/__init__.py` real (hoy es shell vacío). `git mv` preserva historia.
3. **Repoint imports** en los archivos movidos: `src.plugins.chats.agent.eta` → `src.plugins.eta.agent`.
   ⚠️ Gotcha ruff (§6): si agregás un import nuevo que aún no se usa, el hook lo borra entre edits.
4. **Manifests:** chats pierde worker eta + router eta; eta gana `api:` + `agent.workers[eta]`
   (con dashboard/deployment/compose copiados). Ver §2.
5. **Orders transitions:** retarget `chats/eta` → `eta/eta` (×5). Sin depends_on.
6. **K8s + compose:** repoint command en `worker-eta.yaml` + `render-compose.py`.
7. **Tests backend:** reubicar tests de eta a `tests/plugins/eta/` (buscar `tests/**eta**`
   + tests que importen los módulos eta) + repointar.
8. **Frontend:** `entities/tracked-order` (+ donde llame) `/api/chats/eta` → `/api/eta`;
   `plugins:sync`.
9. **VERIFICAR (todo verde):**
   ```
   cd hubara_agency && uv run lint-imports                                  # R-DIP: eta self-contained, 0 broken
   cd hubara_agency && uv run pytest tests/plugins/eta tests/plugins/test_premortem_invariants.py \
       tests/architecture/test_plugin_contract.py -q                       # eta tests + invariants + harness
   rtk proxy grep -rn "chats\.agent\.eta\|chats\.workers\.eta\|chats\.api\.eta\|/api/chats/eta" hubara_agency frontend_dashboard  # = VACÍO
   cd frontend_dashboard && npx tsc -b && npm run test:arch                 # tsc limpio + arch gates
   ```
   Smoke import del sales worker con dummy Medusa env (gotcha §6): confirmar que sacar
   eta de chats no rompió nada de chats.
10. **Commit** en la branch del refactor. Mensaje estilo `a5bc586`.

---

## §5. Definition of done

- `eta` es un plugin self-contained: todo su código bajo `plugins/eta/`, imports solo
  exoclaw+platform+propio (lint-imports 0 broken; P-3 verde).
- Apagar `eta` (sacarlo de `ENABLED_PLUGINS`) no rompe nada: el worker no arranca, las
  transitions orders→eta se skipean (P-SKIP), el ruteo ROUTE_ETA cae a Sales tolerante.
- Prender `eta` enciende su worker + api + frontend.
- Premortem invariants verdes (k8s parity + compose), plugin-contract harness verde.
- `P-9` (test_plugin_contract): tras eta extraído, el único rojo que queda es
  `agents_admin → /api/chats/evals` (el consumo per-agente de evals — open design point,
  NO un split). Documentar o aceptar.

---

## §6. Gotchas de esta sesión (NO repetir)

- **ruff borra imports entre edits:** el hook `post-edit-lint` corre `ruff --fix` por edit;
  si agregás `import X` en un edit y su uso en OTRO edit, ruff borra `import X` (estaba
  "unused" en ese instante). Síntoma: `NameError` en runtime/tests. Fix: agregar import +
  uso juntos, o re-agregar el import después. (Me pasó con `import os` en plugin_manifest.)
- **`grep -v "X.py:"` filtra de más:** `grep -v "list_ads_campaigns.py:"` también esconde
  `test_list_ads_campaigns.py:`. Usar patrón de path preciso al buscar consumidores.
- **2 premortem invariants fallan PRE-EXISTENTE en main** (`chats/sales_eval` sin k8s +
  compose drift, del merge de evals). PR #48 los arregla en main. Si fallan, confirmar QUÉ
  worker falta antes de culpar tu cambio (`git stash` o leer el assert).
- **cd hook literal:** los hooks exigen el substring literal `cd hubara_agency &&` /
  `cd frontend_dashboard &&` antes de `uv run`/`npm`/`npx tsc`. El cwd persiste entre Bash
  calls → resetear: `cd <worktree-root> && cd hubara_agency && uv run ...`.
- **RTK proxy vacía git/ls:** `git diff`/`status`/`ls` salen vacíos por el proxy Tamp/RTK.
  Usar `rtk proxy git ...` / `/bin/ls` / `rtk proxy find` para datos reales.
- **`tests/architecture/**` + `.dependency-cruiser.cjs` son PROTECTED** (meta-test Capa-3):
  tocarlos exige `ARCH_CHANGE_APPROVED=1` al correr `pytest -m architecture` / `test:arch`.
  Agregar tests ahí es sancionado (fortalece gates) pero necesita ese env.
- **Sales worker importa Medusa a nivel módulo:** para smoke-import del sales worker poné
  `MEDUSA_BASE_URL=http://x MEDUSA_ADMIN_TOKEN=x` (dummies).
- **node_modules en el worktree:** ya instalado (`npm install` corrido). `plugins:sync`
  genera el registry (gitignored) — correr antes de `tsc -b`.

---

## §7. Referencias

- **Template de extracción:** commit `a5bc586` (`refactor(ads): extract ads backend…`) —
  `git show a5bc586` muestra el patrón exacto (git mv + repoint + manifests + entity URL + tests).
- **[PLUGIN_CONTRACT.md](PLUGIN_CONTRACT.md)** §5.2 (extracción), §3 (4 canales), §4 (reglas P-#).
- **[PLUGIN_ARCHITECTURE_TESTS.md](PLUGIN_ARCHITECTURE_TESTS.md)** — P-3/P-9/P-7 relevantes.
- **Dispatcher-skip (P-SKIP):** commit `7d02080` — `dispatch_envelope_with_client` skipea
  transitions a plugins no habilitados (por eso orders→eta no necesita depends_on).
- **Archivos clave a leer al reanudar:** `plugins/chats/workers/eta.py` (qué importa),
  `plugins/chats/agent/sales/use_cases/load_or_start_sales_session.py` líneas ~178-191 (ruteo),
  `frontend_dashboard/src/plugins/{chats,eta,orders}/plugin.yaml` (manifests).

---

**Fin.** Con este doc + `git show a5bc586` (el template ads), la extracción de eta es
ejecutable de cabo a rabo sin la memoria de esta sesión.
