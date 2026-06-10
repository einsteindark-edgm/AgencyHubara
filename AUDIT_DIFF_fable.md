# Diff de auditorías — qué encontré que la auditoría anterior NO encontró (fable)

> **Qué es.** Comparación honesta entre la auditoría previa
> ([PLUGIN_ISOLATION_AUDIT.md](PLUGIN_ISOLATION_AUDIT.md), 2026-06-05, +
> [PLUGIN_CONTRACT.md](PLUGIN_CONTRACT.md) /
> [PLUGIN_ARCHITECTURE_TESTS.md](PLUGIN_ARCHITECTURE_TESTS.md)) y mi auditoría
> independiente ([PLUGIN_ISOLATION_AUDIT_fable.md](PLUGIN_ISOLATION_AUDIT_fable.md),
> 2026-06-09, sobre HEAD `9c21fe7`). Tres secciones: (A) lo que el otro
> desarrollador NO vio, (B) lo que en sus docs quedó stale o es impreciso hoy,
> (C) lo que verifiqué y está correcto — crédito donde corresponde.
>
> Contexto temporal importante: su auditoría es PRE-refactor y los otros dos
> docs se actualizaron DURANTE el refactor (PR #49). Parte del delta es
> evolución legítima del código; eso va en (B) como "stale", no como error.
> Lo que va en (A) son clases enteras de problemas que su método no miró.

---

## A. Lo que NO encontró (hallazgos nuevos, con por qué su método no los vio)

| # | Hallazgo (detalle en mi auditoría §2) | Severidad | Por qué se le escapó |
|---|---|---|---|
| **N-1** | **El toggle no existe en la capa de deploy**: `render-compose.py` renderiza TODOS los workers sin leer `ENABLED_PLUGINS`; los containers corren `python -m <module>` directo (sin pasar por `run_workers.py`); ni compose ni k8s setean `ENABLED_PLUGINS` → `enabled_plugins()=None` en prod → **P-7 está inerte donde importa**. | 🔴 | Auditó los **loaders** (main.py / run_workers.py / plugins-sync) y declaró REQ-2 según ellos. Nunca siguió la cadena hasta el artefacto que corre: ¿QUÉ proceso arranca el worker en el stack real y QUÉ env ve? `run_workers.py` es dev-only — su gating no protege producción. |
| **N-2a** | **`eta` apagable ≠ `eta` independiente**: el webhook de WhatsApp es un router del plugin `chats` (`chats/plugin.yaml:29`); sin `chats`, el agente ETA manda notificaciones pero **no puede recibir respuestas** (el inbound muere en 404). Dependencia funcional dura eta→chats(ingest) que ni `depends_on` ni P-7 cubren. | 🔴 | El contrato modela el ruteo de inbounds como "residuo de Opción A" (PM-2: duplicación de template). Miró el coupling como *strings duplicados*, no como *flujo*: nunca preguntó "¿por dónde ENTRA un mensaje del cliente con chats apagado?". |
| **N-2b** | **`ROUTE_ETA` vive en `src/platform/constants.py:34` — spinal file PROTECTED**. Agregar un plugin-agente con ruta propia = editar un archivo central protegido + el use case de chats. Espejo backend exacto de F4 (íconos). | 🟠 | El contrato (§5.1) describe el hardcode "en chats". La constante real está en platform. Detalle de ubicación con consecuencia grande: el remedio no es "limpiar chats" sino "crear el contribution point que falta" (route registry), y mientras tanto cada agente nuevo viola INV-1. |
| **N-3** | **P-9 mide comentarios, no código**: hoy sus únicos matches son docstrings JSDoc (`AgentsQuality.tsx:9`, `EvalTrendChart.tsx:89`). Borrarlos = P-9 verde con el coupling vivo. Y es ciego al canal real: *plugin → entity central → API ajena* (`chats→@/entities/order→/api/orders`; `agents_admin→eval-*→/api/chats/evals`, incl. POST/DELETE). | 🟠 | Diseñó P-9 como grep literal bajo `plugins/` y no lo confrontó con el código: ¿DÓNDE viven los fetch de verdad? Viven en `src/entities/*/api.ts` (fuera del scope del grep) — su propia F2 (entities centrales) invalida el supuesto de su P-9. Las dos piezas estaban en su informe; no las cruzó. |
| **N-4** | **Mapa runtime de roturas por toggle**: apagar `orders` rompe el canvas de pago de chats (404); apagar `chats` rompe "Calidad LLM" de agents_admin. Tabla completa entity→endpoint→importadores en mi §2 N-4. | 🟠 | Encontró F8 (chats importa entity order) como "coupling declarativo". No lo tradujo a consecuencia operacional de REQ-2 (qué UI muere al apagar qué). |
| **N-5** | **FE gating es BUILD-time** (`predev`/`prebuild`, `process.env` no-VITE) vs backend RUN-time → multitenant implica build por tenant o registry runtime. Decisión sin tomar. | 🟠 | Verificó que plugins-sync filtra; no preguntó **cuándo** corre ni qué implica para "cada tenant = distinto set". |
| **N-6** | **El manifest único vive en el árbol del frontend y el backend depende de él en runtime** (`plugin_manifest.py:36-37`, `Dockerfile:27` copia `frontend_dashboard/src/plugins/` a la imagen backend). Contrato implícito sin documentar. | 🟡 | Lo usó (sus helpers leen de ahí) sin marcarlo como frontera/riesgo. |
| **N-7** | `ENABLED_PLUGINS`: parseo **duplicado 4×** + semántica fail-open (unset → todo encendido) sin decisión explícita de política para tenants. | 🟡 | Verificó la semántica de cada lector por separado; no miró la duplicación ni el default como riesgo. |
| **N-8** | **El meta-gate backend tampoco coincide con su CLAUDE.md**: `conftest.py:126-136` no protege `src/platform/{constants,contracts,registries,tool_extensions}.py` ni `tests/plugins/test_premortem_invariants.py` (declarados PROTECTED). PM-11 documentó esto SOLO para frontend. | 🟡 | Encontró el patrón (PM-11) y no lo generalizó al otro stack. |
| **N-9** | `main.py:112-118`: manifest `id` ≠ dirname → **silent skip** con warning (vs plugins-sync que falla duro). Un typo apaga un plugin en silencio. | 🟢 | Catalogó main.py como "fail-fast" — cierto para imports, falso para id-mismatch. |
| **N-11** | Regex de `_find_temporal_workflow_name` (`[a-z_]+`) no admite dígitos aunque el naming oficial los permite → gate con falla confusa para un futuro `ads2`. | 🟢 | Bug latente en un test que sus docs ni inventarían (ver B-3). |
| **N-12** | Guards menores ausentes: dir backend huérfano sin manifest; `wiring_intents.env_vars_required` vs env real de compose/k8s (PM-10 sigue 100% abierto); P-13 inexistente. | 🟢 | Inventarió varios; estos quedaron fuera. |

**Patrón general de los misses:** su método fue *estructural* (imports,
manifests, AST, file layout) y ahí fue excelente. Las clases que se le
escaparon son las *operacionales*: ¿qué proceso corre con qué env? ¿qué flujo
entra por dónde? ¿qué mide de verdad el test que diseñé? Es, irónicamente, su
propia meta-lección del pre-mortem ("verde estructural no prueba
comportamiento") sin aplicársela a los gates y al deploy.

---

## B. Lo que quedó STALE o impreciso en sus docs (a corregir)

| # | Doc · lugar | Dice | Realidad 2026-06-09 |
|---|---|---|---|
| B-1 | `PLUGIN_CONTRACT.md` §5.1 | "chats sigue conteniendo `ROUTE_ETA`" | La constante vive en `src/platform/constants.py:34` (PROTECTED). Chats contiene el USO (`load_or_start_sales_session.py:178`). Cambia el remedio (N-2b). |
| B-2 | `PLUGIN_CONTRACT.md` §9 / `PLUGIN_ARCHITECTURE_TESTS.md` P-9 | El xfail de P-9 "caza" el coupling de agents_admin | Lo caza **por comentarios** (N-3). El señalador del DoD es frágil en ambos sentidos. |
| B-3 | `PLUGIN_ARCHITECTURE_TESTS.md` (todo el doc) | P-19 "🔴 propuesto" | **Ya existe en gran parte**: `tests/architecture/test_manifest_orchestration_consistency.py` (4 checks: workflow_classes⇔AST, on_event∈emits, targets resuelven, eventos importables). El doc no lo registra; solo falta el smoke funcional. |
| B-4 | `PLUGIN_ARCHITECTURE_TESTS.md` §1 P-2 | Header dice "🔴" | El test existe y pasa (su propio resumen §4 dice 🟢). Inconsistencia interna. |
| B-5 | `PLUGIN_ARCHITECTURE_TESTS.md` §2 P-10 | "🟢 hecho" (implica ambas reglas nuevas) | Solo `plugins-no-features` existe (`.dependency-cruiser.cjs:126`). **`plugins-own-entities-only` NO se agregó** (consistente con que no hay entities por-plugin, pero el doc lo da por puesto). |
| B-6 | `PLUGIN_ARCHITECTURE_TESTS.md` §5 / `PLUGIN_CONTRACT.md` §5.4 | "P-7 cierra REQ-2" | Cierto en código, **inerte en los deployments reales** (N-1: nadie setea `ENABLED_PLUGINS` en compose/k8s). REQ-2 no está cerrado operacionalmente. |
| B-7 | xfail P-9 en `test_plugin_contract.py:123-131` | "…eta sigue split… Verde cuando … + eta extraído" | eta ya está extraído. Mitad de la razón es historia (la instancia viva de su propio PM-12). Requiere PR con label `architecture-change` para corregirla. |
| B-8 | `orders/api/__init__.py:432` | "HubaraEtaSessionWorkflow del plugin chats" | Es del plugin `eta` post-extracción. Comment rot. |
| B-9 | `PLUGIN_CONTRACT.md` §6 checklist item 6 | "consume `platform/conversation`, declara `agent.owns_route`" | Ninguno de los dos mecanismos existe (§5.1 del mismo doc lo aclara, pero el checklist guía al implementer a usar algo inexistente — exactamente su PM-7: doc que contradice al gate). |
| B-10 | `PLUGIN_ISOLATION_AUDIT.md` §2 F1/F3, §3 scorecard | eta/ads split; orders→chats/eta | Cerrado por las extracciones; transitions ahora target `eta/eta` (`orders/plugin.yaml:50-119`). Esperable (doc fechado pre-refactor) — listado para que nadie lo lea como estado actual. |
| B-11 | `PLUGIN_ARCHITECTURE_TESTS.md` §"Relación con gates" | "hoy hay 3 capas" (importlinter, dep-cruiser, premortem) | Hay bastante más superficie real que el doc nunca inventarió: 10 archivos de arch-tests frontend (registry, fetch-isolation, env/urls, zod, tokens/css, naming, tsc, meta-gate) + `test_manifest_orchestration_consistency` + CI `architecture-gates.yml` con label-gate `ARCH_CHANGE_APPROVED`. El mapa de defensa existente está sub-documentado. |

---

## C. Lo que verifiqué y está CORRECTO (crédito)

Para que este diff no lea como demolición: la mayor parte de su trabajo es
sólido y lo confirmé pieza por pieza.

1. **El mecanismo es sólido tal como lo describió.** Loaders auto-discovery
   gateados, fail-fast en imports, dispatcher 100% genérico (cero imports de
   plugins, resolución por strings del manifest), queues en manifests,
   `agents_admin` escanea genéricamente sin nombrar plugins. Todo verificado.
2. **Cero imports cross-plugin reales en ambos stacks.** Confirmado por AST
   backend (P-3 verde) y dep-cruiser frontend. Su distinción
   docstring-vs-import (el falso positivo de `dispatcher.py`) era correcta.
3. **El retiro de P-5 fue una buena decisión de diseño** (transitions soft +
   P-SKIP en vez de `depends_on` duro) y está bien implementado y testeado
   (`TestEnabledPluginsSkip`). Mi N-1 no contradice el diseño — contradice su
   *alcance declarado*.
4. **El pre-mortem (PM-1..PM-13) es de alta calidad.** Honesto, específico,
   verificado contra hechos reales. Varios de mis hallazgos son
   generalizaciones de sus propios PM (N-8 = PM-11 en backend; N-10 = PM-12
   vivo). La meta-lección que enunció es la correcta — mi auditoría es en gran
   parte esa lección aplicada a las capas que él no alcanzó a mirar.
5. **Las extracciones de `ads` y `eta` quedaron limpias** a nivel código:
   self-contained, manifests correctos, workflow declarado y resuelto por AST,
   k8s parity al día (7/7 invariantes premortem pasan).
6. **F2/F4/F5/F7/F9 siguen siendo correctos y abiertos** — los re-confirmé con
   evidencia fresca (11 entities centrales, `resolveIcon` fallback,
   `pluginProps` 12 props, `service.py:176` sin filtro).
7. **La dirección del contrato (INV-1/INV-2 + 4 canales + casts) es la
   correcta.** Mi plan ([PLUGIN_REFACTOR_PLAN_fable.md](PLUGIN_REFACTOR_PLAN_fable.md))
   no lo reemplaza: lo extiende a las capas que faltaron (deploy, edge de
   ingest, detección real de consumo cross-API, proceso).

---

## Resumen en una línea

**Él auditó el código y diseñó la ley; faltó auditar la OPERACIÓN (deploy,
flujos de entrada, y si los candados miden lo que dicen medir). Ahí están los
4 hallazgos que cambian decisiones: N-1 (toggle inexistente en deploy), N-2
(eta atado a chats por el ingest + rutas en spinal PROTECTED), N-3 (P-9 mide
comentarios), N-5 (FE gating build-time vs multitenant).**
