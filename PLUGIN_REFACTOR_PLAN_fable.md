# Plan de refactor + control de arquitectura (fable)

> **ESTADO DE EJECUCIÓN (2026-06-10): F1–F8 EJECUTADAS Y VERDES** en la branch
> `claude/relaxed-shaw-91fbdd`, un commit por fase: F1 `e1cbbed` · F2 `6fea13d`
> · F3 `b5bddb8` · F4a/b `1a60df0` · F4c `0fff924` · F5 `c4db5f8` · F6
> `3731a8f` · F7 `a79d937` · F8 (protocolo + meta-gates únicos). Decisiones
> tomadas con las opciones recomendadas: D1a (render gateado + P-21), D2a
> (build por tenant — documentado, sin refactor de shell), D3a (artefactos
> siempre con set explícito; default fail-open solo en dev), D4a (eta declara
> `depends_on: [chats]` por el ingest; D4b queda opcional a futuro).
> Los gates corren en CI (`architecture-gates.yml`) y bloquean merge a main;
> el PR de esta branch requiere el label **`architecture-change`** (tocó
> tests PROTECTED — por diseño). Verificación final: backend 419 passed +
> routing 5/5 + lint-imports 4/0; frontend tsc limpio + 19/19+1skip arch +
> 144 units + build prod. Persisten 3 fallos PRE-existentes de main en
> tests/plugins/chats (voseo + 2 watchdog), fuera del alcance (chip spawneado).

> **Qué es.** El plan ejecutable para llevar el sistema de plugins desde el
> estado real auditado en
> [PLUGIN_ISOLATION_AUDIT_fable.md](PLUGIN_ISOLATION_AUDIT_fable.md)
> (2026-06-09, HEAD `9c21fe7`) hasta el "desacople perfecto" de
> [PLUGIN_CONTRACT.md](PLUGIN_CONTRACT.md) — **incluyendo las capas que el plan
> anterior no cubría** (deploy, edge de ingest, detección real de consumo
> cross-plugin) — y el **sistema de control permanente** para que esta clase de
> errores no pueda volver a entrar.
>
> Cómo usarlo: §0 son decisiones del operador (bloquean fases). §2 son las
> fases — cada una es un PR (o serie corta) que shippa verde por sí sola. §3-§4
> son el sistema de candados. §5 el runbook operacional. Cada fase lista los
> PM-# (pre-mortem del contrato §9) que la acechan.

---

## §0. Decisiones previas del operador (bloquean fases)

| # | Decisión | Opciones (recomendada primero) | Bloquea |
|---|---|---|---|
| **D1** | **¿Cómo se gatea el deploy?** Hoy compose/k8s renderizan/corren TODO (N-1). | **(a)** `render-compose.py` acepta el set habilitado (env/flag) y renderiza SOLO esos workers + inyecta `ENABLED_PLUGINS` a api/workers; k8s por overlays por-deployment. (b) Renderizar todo pero cada worker se auto-apaga al boot (P-21) — más simple, gasta containers. Recomiendo **a + P-21 como defensa en profundidad**. | F2 |
| **D2** | **¿Frontend por tenant: build o runtime?** El gating FE es build-time (N-5). | **(a)** Build por tenant (encaja con S3+CloudFront por tenant; cero refactor del shell). (b) Registry runtime (un solo bundle, flags servidos por el backend) — más flexible, refactor mediano del shell + pierde tree-shaking de plugins apagados. Para ≤5 tenants: **a**. | F2 (doc), multitenant |
| **D3** | **Semántica de `ENABLED_PLUGINS` ausente.** Hoy fail-open (todo encendido) ×4 implementaciones (N-7). | **(a)** Mantener fail-open en dev, pero los ARTEFACTOS de deploy SIEMPRE lo setean explícito (el render falla si no se lo das) — el default nunca llega a prod. (b) Cambiar a fail-closed global (rompe dev ergonomics). Recomiendo **a**. | F1, F2 |
| **D4** | **¿Quién es dueño del edge de ingest WhatsApp?** Hoy es un router del plugin `chats` → eta sin chats queda mudo (N-2a). | **(a)** Declarar `chats` como **plugin core** (documentado + enforced: P-6 con `depends_on: [chats]` en eta, o un `core_plugins:` en config) — honesto y barato. (b) Extraer el webhook + ruteo de inbounds a `platform/ingest` (el end-state §5.1 del contrato) — correcto y más caro. Recomiendo **a ahora, b cuando entre el route registry (F6)**. | F1 (depends_on de eta), F6 |

---

## §1. Principios (no negociables durante el refactor)

1. **No romper los verdes.** Los candados existentes (P-1/2/3/4/7/12/14-forma,
   premortem ×7, orchestration-consistency ×4, FE ×16, lint-imports ×4) quedan
   verdes en CADA PR. Son el piso.
2. **Candado junto al fix, nunca después.** Cada fase agrega su guard en el
   mismo PR que cierra el hueco (o ANTES, como xfail documentado). Un fix sin
   candado es un hueco en pausa.
3. **PRs chicos, cada uno shippable.** El orden de §2 está pensado para que
   cualquier corte intermedio deje el sistema mejor que antes.
4. **Tocar PROTECTED = label `architecture-change`** (activa
   `ARCH_CHANGE_APPROVED=1` en CI — `.github/workflows/architecture-gates.yml:55`).
   Varias fases lo necesitan (tests, `.dependency-cruiser.cjs`, spinal files).
5. **Verificar comportamiento, no solo estructura** (gotcha #1 + meta-lección
   del pre-mortem). Cada fase tiene un smoke de comportamiento en su DoD, no
   solo tests AST.

---

## §2. Fases

### F1 — Simetría runtime: quick wins (esfuerzo S, ~1 día)

**Objetivo:** cerrar los huecos baratos de INV-2 en la capa código, y dejar de
medir con candados rotos.

Cambios:
1. **`validate_enabled()` (P-6)** en `src/platform/plugin_loader.py` (nuevo):
   para el set habilitado, todo `depends_on` de un plugin habilitado también
   habilitado, si no `PluginDependencyError` con mensaje accionable. La llaman
   `main.py`, `run_workers.py` y `plugins-sync.ts` (versión TS equivalente) al
   boot. Per D4a: `eta/plugin.yaml` declara `depends_on: [chats]` (la dep de
   ingest REAL — hoy oculta).
2. **Helper único de `ENABLED_PLUGINS`** (N-7): `main.py` y `run_workers.py`
   importan `plugin_manifest.enabled_plugins()` en vez de sus copias locales.
   (plugins-sync.ts queda como única otra implementación, documentada.)
3. **agents_admin filtra y honra el schema** (F7/PM-3/PM-4): `discover_agents()`
   filtra por `enabled_plugins()` y por `agentic: true`. + test **P-17**
   (`agentic ⟺ ≥1 worker con dashboard:`) + test de filtrado.
4. **Fail-fast de id-mismatch** (N-9): `main.py:112-118` pasa de warning+skip a
   `RuntimeError` (mismo trato que un import roto).
5. **Tests P-15 y P-16** (PM-6, PM-5): `dashboard.workspace` existe en disco;
   `get_task_queue("X",...)` en `plugins/X/workers/` solo con su propio id.
6. **Limpieza de rot** (N-10, label `architecture-change`): xfail P-9 reason
   re-escrito describiendo el INVARIANTE (no la lista de ofensores — lección
   PM-12); comment de `orders/api/__init__.py:432`; headers inconsistentes de
   `PLUGIN_ARCHITECTURE_TESTS.md` (B-3/B-4/B-5).

DoD: gates verdes + P-6/P-15/P-16/P-17 en CI + `ENABLED_PLUGINS=eta uv run
python run_api.py` falla con mensaje claro (falta chats).
Riesgos: PM-12 (escribir reasons por invariante), PM-4 (verificar el toggle en
TODAS las superficies tras el filtro de agents_admin).

### F2 — Paridad de deploy: el toggle de verdad (esfuerzo M, 1-2 días) — cierra N-1

**Objetivo:** que `ENABLED_PLUGINS` gobierne lo que CORRE, no solo lo que un
proceso dev cargaría.

Cambios (per D1a):
1. `render-compose.py` lee el set habilitado (env `ENABLED_PLUGINS` o
   `--enabled`); renderiza SOLO los workers de plugins habilitados e inyecta
   `ENABLED_PLUGINS=<set>` en el `environment` de `hubara-api` y de cada
   worker. Sin set explícito → **falla** (D3a: el default fail-open nunca llega
   a un artefacto).
2. **P-21 worker self-gate** (defensa en profundidad): plantilla común de
   `main()` (o check en `run_workers._run_worker` + en el entrypoint de cada
   worker) que aborta con error claro si `ENABLED_PLUGINS` está set y su plugin
   no está. Cubre containers viejos/orphans (PM-1/AP-9).
3. k8s: los `worker-*.yaml` agregan `ENABLED_PLUGINS` (ConfigMap por
   deployment). Si hay apetito: `render-k8s.py` análogo; si no, alcanza el
   invariante 4.
4. **P-20 deploy-parity test**: para el set de cada artefacto declarado
   (compose local; k8s prod), services/deployments de workers == workers de los
   plugins habilitados de ESE artefacto, y todo container worker lleva el env.
   Extiende `tests/plugins/test_premortem_invariants.py` (PROTECTED → label).
5. Runbook: regenerar compose tras cambiar el set + `docker compose up
   --remove-orphans` SIEMPRE (PM-1).

DoD: con `ENABLED_PLUGINS=chats,catalog` el compose generado NO contiene
`hubara-worker-eta-eta`; smoke: levantar el stack recortado y verificar que el
API responde y el dispatcher loggea `skipped_disabled` al disparar una
transition a eta (primer caso real donde P-7 deja de ser inerte).
Riesgos: PM-1 (orphans), PM-10 (diffear el compose regenerado — el delta debe
ser exactamente el semántico).

### F3 — Detección REAL de consumo cross-plugin (esfuerzo S-M, ~1 día) — cierra N-3 sin mover archivos

**Objetivo:** antes de migrar entities (F4), instalar la medición que hoy
falta, para que F4 tenga un "rojo que se va apagando" confiable.

Cambios:
1. **Mapa de ownership de entities** (transitorio mientras existan centrales):
   `src/entities/OWNERS.yaml` → `{order: orders, eval-trend: chats, …}` (el
   owner = el plugin dueño del API que la entity llama).
2. **P-22**: test FE — un archivo bajo `plugins/X` que importe
   `@/entities/<e>` con `owner(e) ∉ {X, shared-explícito}` falla, salvo que el
   manifest de X declare `consumes:` para ese provider. → F8/N-4 quedan ROJOS
   visibles (xfail documentado hasta F4/F5), y cualquier cadena nueva se
   bloquea YA.
3. **P-23**: reemplazo del grep de P-9 — escanear las llamadas REALES
   (`apiClient.*("…")` y `subscribeSse`) en `plugins/**` Y en
   `entities/**` (atribuidas por ownership), ignorando comentarios (AST o al
   menos strip de comments). P-9 actual queda como candado secundario.
4. dep-cruiser: regla `plugins-own-entities-only` queda redactada (entra a
   enforcement al final de F4, cuando existan entities por-plugin).

DoD: P-22/P-23 en CI con los 2 ofensores conocidos como xfail nominal
(chats→order; agents_admin→evals) y CERO ofensores nuevos posibles.

### F4 — Entities por-plugin + casts (esfuerzo L, 3-5 días en PRs por entity) — cierra F2/P-11/P-14-uso

**Objetivo:** `src/entities/` central → vacío; cada plugin posee las suyas;
cross-plugin SOLO vía `consumes:`/cast server-side.

Orden de migración (de menor a mayor acople; un PR por línea):
1. **Single-consumer, dueño obvio:** `tracked-order`→eta · `ads-campaign`→ads ·
   `agent`→agents_admin · `catalog-sync`→catalog. (Mover a
   `plugins/<id>/frontend/entities/<x>/`, actualizar imports del propio
   plugin, barrels locales.)
2. **Familia chats:** `chat`, `message`, `session`, `handoff` → chats.
3. **El caso cast #1 — `order`:** `order`→orders. `chats` define su entity
   local `order-ref` (solo los campos del canvas de pago) + bloque en su
   manifest: `depends_on: [orders]`, `consumes: [{provider: orders, contract:
   order@v1, into: order-ref, cast: …}]`. **El cast corre server-side** (el
   backend de chats consume el port de platform/orders y sirve
   `/api/chats/...`); el FE de chats deja de llamar `/api/orders` (P-23 verde
   para esta cadena).
4. **El caso cast #2 — evals** (junto con F5): `eval-trend`/`eval-candidate` →
   agents_admin, contra su PROPIO backend (ver F5).
5. Cierre: `src/entities/` queda vacío → **P-11** verde (sin allowlist);
   `plugins-own-entities-only` pasa a error; se borra `OWNERS.yaml` y P-22 se
   simplifica a "no importar entities ajenas".

Schema: agregar `consumes:` a `_schema/plugin.schema.yaml` (P-14 ya valida la
forma).
Riesgos: PM-8 (paths de API NO cambian en esta fase — solo se mueven archivos
TS), PM-11 (los archivos de entity figuran como PROTECTED en el CLAUDE.md FE
pero el meta-gate real no los protege — resolver el set real en F8 ANTES de
esta fase o aceptar el estado actual documentándolo), PM-9 (declarar el % de
aislamiento honestamente en cada PR).

### F5 — Evals server-side (esfuerzo M, 1-2 días) — saca P-9/P-23 de xfail

**Objetivo:** el plano de gestión (agents_admin) deja de llamar `/api/chats`.

Cambios: el backend de agents_admin agrega los evals igual que
`discover_agents()` (escanea manifests buscando workers con eval; HOY: proxy
fino al storage de evals del agente sales) y sirve
`/api/agents/evals/{history,candidates}`. El FE de agents_admin apunta a su
propio prefix. El worker `sales_eval` se queda donde está (decisión per-agente
intacta).
DoD: quitar `@pytest.mark.xfail` de P-9 (label `architecture-change`) → P-9 y
P-23 verdes ESTRICTOS. Smoke: "Calidad LLM" funciona con `chats` habilitado y
muestra empty-state limpio (no 404 roto) con chats apagado.

### F6 — Route registry + edge de ingest (esfuerzo M-L, 2-3 días) — cierra N-2/PM-2

**Objetivo:** que un agente conversacional nuevo NO toque `platform/constants.py`
ni `chats`, y que el handoff de rutas sea declarativo.

Cambios:
1. Schema: `agent.owns_route: <route>` + `agent.workflow_id_prefix: "<p>-"`.
2. `platform/routing.py` (nuevo): construye `route → (plugin, worker,
   workflow_id_template)` desde los manifests (filtrado por enabled). El
   ruteo de inbounds de chats consulta ese registry; muere el `if active_route
   == ROUTE_ETA` (`load_or_start_sales_session.py:178-179`) y `ROUTE_ETA` sale
   de `platform/constants.py` (label `architecture-change` — spinal).
3. **Hasta que esto entre**, guard transitorio **P-18**: el prefijo de
   workflow_id hardcodeado en el ruteo de chats ⊆ los
   `workflow_id_template` declarados en manifests (ata las dos copias de
   `eta-{…}`).
4. (D4b, opcional/cuando toque) mover webhook ingest a `platform/ingest` —
   entonces `eta.depends_on: [chats]` (de F1) se elimina y eta pasa a ser
   togglable de verdad.

DoD: P-18 reemplazado por "el ruteo no hardcodea ningún `<plugin>-{…}`"; smoke
de comportamiento (PM-13): inbound de un cliente con `active_route=eta` llega
al workflow `eta-{session}` con chats+eta habilitados.

### F7 — Contribution points del frontend (esfuerzo S-M, 1-2 días) — cierra F4-íconos/F9

1. **Íconos contribuidos**: el plugin trae sus glifos en su `frontend/`
   (`icons.tsx` exportando `{name: componente}`); `plugins-sync.ts` los merge
   al registry generado; `Icon.tsx` queda como base. P-12 ya valida contra el
   set efectivo.
2. **pluginProps → contrato**: reemplazar el bag de `Dashboard.tsx:106-119`
   por un `PluginHostContext` genérico (`selection: Record<string,string|null>`
   + setter) o selection-key namespaced por plugin. Plugin nuevo con estado de
   selección NO edita `Dashboard.tsx` (test: snapshot del shell sin nombres de
   plugin, o simplemente P-8 "diff fuera de plugins/ = 0" en el checklist de
   plugin nuevo).

### F8 — Proceso y meta-gates: una sola verdad de PROTECTED (esfuerzo S, ~1 día) — cierra N-8/PM-11

1. **Una fuente**: `hubara_agency/.hubara/spinal-files.yaml` pasa a ser LA
   lista; ambos meta-gates (`tests/architecture/conftest.py:126` y
   `src/test/architecture/helpers.ts:82`) la LEEN en vez de duplicarla; los
   CLAUDE.md referencian, no listan. Decidir ahí el destino de los paths hoy
   en disputa (¿`src/platform/constants.py` y `test_premortem_invariants.py`
   entran al gate? recomiendo sí; ¿entity files? se vuelven per-plugin en F4 y
   salen).
2. **Doc-estado generado, no mantenido**: script chico que emite la tabla
   P-# → estado desde los tests reales (nombre/xfail/skip) → se pega en
   `PLUGIN_ARCHITECTURE_TESTS.md` (mata la clase B-3/B-4/B-5 de rot).
3. **Checklist de extracción** (§9.1 del contrato) como command del pipeline
   (`.archon/commands/hubara-extraction-check.md`) para que el enforcement no
   dependa de que alguien recuerde leer el doc.
4. Guards menores N-12: **P-26** (todo dir `src/plugins/<id>/` con código tiene
   manifest), **P-25** (`wiring_intents.env_vars_required` ⊆ env del
   compose/k8s renderizado — cierra PM-10 de contenido), **P-13** (ids
   coherentes cross-stack), fix regex N-11.

---

## §3. El sistema de candados completo (estado final)

| Candado | Qué hace imposible | Vive en | Estado hoy → final |
|---|---|---|---|
| P-1 P-SELF | manifest declara módulos ajenos | `test_plugin_contract.py:38` | 🟢 |
| P-2 P-PARITY | manifest con backend sin código | `test_plugin_contract.py:76` | 🟢 |
| P-3 P-NOXIMPORT | import cross-plugin backend | `test_plugin_contract.py:48` | 🟢 |
| P-4 P-PLATFORM | platform→plugins | `test_plugin_contract.py:66` | 🟢 |
| P-6 P-ENABLED | habilitar X sin su dep dura | `plugin_loader.py` + test | 🔴 → F1 |
| P-7 P-SKIP | transition a plugin apagado dispara al vacío | `dispatcher.py:179` | 🟢 (inerte) → operativo en F2 |
| P-9/P-23 own-API | plugin consume API ajena (incl. lavado vía entities, sin contar comentarios) | reescrito F3 | 🟡 frágil → F3/F5 |
| P-10 cruiser | plugins→features / cross-plugin / pages-app | `.dependency-cruiser.cjs` | 🟢 (+`plugins-own-entities-only` al cierre de F4) |
| P-11 P-ENTITY | entity de dominio en central | vitest | 🔴 → F4 |
| P-12 P-ICON | ícono de manifest sin resolver | `test_plugin_icons.arch.test.ts` | 🟢 |
| P-13 parity ids | asimetría de ids cross-stack | nuevo | 🔴 → F8 |
| P-14 P-CAST | consumo de datos sin cast declarado | `test_plugin_contract.py:89` | 🟢forma → uso en F4 |
| P-15 workspace | `dashboard.workspace` stale (PM-6) | nuevo | 🔴 → F1 |
| P-16 self-queue | worker con `(plugin,worker)` ajeno (PM-5) | nuevo | 🔴 → F1 |
| P-17 agentic | schema miente sobre el código (PM-3) | nuevo + fix service | 🔴 → F1 |
| P-18 route template | drift del template de ruteo (PM-2) | nuevo, transitorio | 🔴 → F6 (luego se reemplaza) |
| P-19 estático | transition no resuelve al runtime del worker | `test_manifest_orchestration_consistency.py` (¡ya existe!) | 🟢 + smoke funcional en F6 |
| **P-20 deploy parity** | artefacto de deploy con workers de plugins apagados / sin env | premortem invariants | **nuevo** → F2 |
| **P-21 worker self-gate** | container stale/orphan sirviendo un plugin apagado | entrypoint workers | **nuevo** → F2 |
| **P-22 entity ownership** | plugin importa entity de otro dueño sin `consumes:` | vitest + OWNERS.yaml | **nuevo** → F3 (transitorio hasta F4) |
| **P-25 wiring↔env** | env requerido por manifest ausente en compose/k8s (PM-10) | premortem invariants | **nuevo** → F8 |
| **P-26 dir huérfano** | código backend sin manifest | arch test | **nuevo** → F8 |
| **P-27 PROTECTED único** | meta-gate ≠ docs (PM-11/N-8) | spinal-files.yaml como fuente | **nuevo** → F8 |
| Premortem ×7 | queue dup, k8s parity, compose drift, naming | `test_premortem_invariants.py` | 🟢 |
| CI | todo lo anterior bloquea merge | `architecture-gates.yml` | 🟢 (ya corre ambos stacks + label-gate) |

## §4. Matriz "error histórico → candado que lo vuelve imposible"

| Error que ya pasó (o casi) | Candado |
|---|---|
| Split plugin (backend de X dentro de Y) — F1 original | P-1 + P-2 + P-23 |
| Apagar un plugin y que su worker siga corriendo (orphan / deploy) — PM-1, N-1 | P-20 + P-21 + runbook §5 |
| Prender un plugin sin su dependencia (eta sin chats) — N-2a, F3 | P-6 + (D4) |
| Consumo cross-API invisible (lavado vía entity / comments) — N-3, F8 | P-22 + P-23 |
| UI rota al apagar un provider — N-4 | P-22/P-14 (cast con empty-state) + smoke F5 |
| Agente nuevo edita spinal central (ruta/ícono) — N-2b, F4 | F6 route registry + F7 íconos + P-27 |
| Template de workflow_id drifteado entre plugins — PM-2 | P-18 → route registry |
| `agentic`/schema decorativo — PM-3 | P-17 |
| Card de agente de un plugin apagado — PM-4, F7 | fix F1 + test de filtrado |
| Self-reference `(plugin,worker)` stale al extraer — PM-5 | P-16 |
| `dashboard.workspace` stale — PM-6 | P-15 |
| Doc contradice al gate (anatomía, estados P-#) — PM-7, B-3..B-5 | F8 doc-generado + checklist como command |
| k8s/compose drift de contenido — PM-10 | P-20 + P-25 |
| PROTECTED de papel — PM-11, N-8 | P-27 |
| xfail reason podrida — PM-12 | regla F1.6 (reasons por invariante) + doc generado |
| Wiring consistente pero dispatch muerto — PM-13 | P-19 (existente) + smokes F2/F6 |
| Manifest con id typo apaga un plugin en silencio — N-9 | fail-fast F1.4 |

## §5. Runbook de toggle (post-F2)

Apagar/prender un plugin en un deployment:
1. Editar el set del deployment (env file del compose / ConfigMap k8s) — único
   lugar.
2. `cd hubara_agency && ENABLED_PLUGINS=<set> uv run python scripts/render-compose.py`
   (falla si el set viola `depends_on` — P-6).
3. Diffear el artefacto regenerado (PM-10: el delta debe ser solo el esperado).
4. `docker compose -f docker-compose.local.yml up -d --remove-orphans` (PM-1 —
   SIEMPRE con `--remove-orphans`).
5. Frontend: re-build con el mismo `ENABLED_PLUGINS` (D2a) — el codegen y el
   backend deben recibir EL MISMO set (si difieren: secciones muertas o
   features fantasma).
6. Verificar TODAS las superficies (PM-4): sección FE fuera · card de agente
   fuera · `skipped_disabled` en logs del dispatcher al disparar transitions
   hacia él · su worker no corre (`docker ps`) · in-flight workflows de su
   queue drenados o aceptada su pérdida.

## §6. Qué NO romper (el piso verde)

Loader auto-discovery + fail-fast de imports (`main.py:151-234`) · dispatcher
genérico string-based + skip (`dispatcher.py`) · queues en manifests
(`plugin_manifest.get_task_queue`) · cero imports cross-plugin (ambos stacks) ·
`agents_admin` descubriendo por bloque `dashboard:` genérico · shell
data-driven (`Dashboard.tsx:43-45`) · registry codegen gitignored · CI
architecture-gates con label-gate · pre-mortem §9 del contrato como doctrina de
extracción.

## §7. Orden y esfuerzo

```
F1 quick wins runtime        S    ~1d   ← empezar acá (desbloquea poco, paga mucho)
F2 deploy parity (D1,D3)     M    1-2d  ← REQ-2 real; primera vez que P-7 trabaja
F3 detección real (N-3)      S-M  ~1d   ← instala el "rojo confiable" antes de mover nada
F4 entities + casts          L    3-5d  ← en PRs por entity, orden §2-F4
F5 evals server-side         M    1-2d  ← P-9 verde estricto
F6 route registry + ingest   M-L  2-3d  ← muere ROUTE_ETA; eta independiente (con D4b)
F7 íconos + pluginProps      S-M  1-2d
F8 proceso + meta-gates      S    ~1d   ← puede adelantarse si PM-11 estorba en F4
```

Total ≈ 2-3 semanas calendario en PRs chicos. Tras F2 el sistema ya cumple
REQ-2 operacionalmente; tras F4+F5 cumple REQ-1 en frontend; tras F6+F7 un
plugin nuevo (incluso agente con ruta e íconos propios) entra sin tocar UN
archivo central — y §3/§4 hacen que quedarse así no dependa de la memoria de
nadie.

---

**Fin.** Este plan extiende (no reemplaza) el §4 de la auditoría previa y el
contrato: mismas metas, más las capas que faltaban — deploy, ingest, detección
y proceso. El código vivo gana sobre este doc también.
