# Plugin Architecture Tests — diseño (que no vuelva a pasar)

> **Qué es.** El diseño de los **unit tests de arquitectura fuertes** que hacen
> imposible violar el [PLUGIN_CONTRACT.md](PLUGIN_CONTRACT.md) sin que CI lo cace.
> Cada test mapea a una regla **P-#** del contrato y a un anti-pattern **AP-#** /
> hallazgo **F#** de la [auditoría](PLUGIN_ISOLATION_AUDIT.md).
>
> **Estado.** PARCIALMENTE IMPLEMENTADO (PR #49). Verdes y puestos como candado:
> P-1/2/3/4/12/14(forma) + P-7 (dispatcher-skip). Siguen 🔴 (definition-of-done
> pendiente): P-6, P-9 (solo evals), P-11, P-14(uso). **Nuevos del pre-mortem
> (§3.5, P-15..P-19):** los modos de fallo que las extracciones `ads`/`eta`
> destaparon y que ningún gate de import/manifest atrapa. El conjunto rojo == lo
> que falta; los verdes congelan lo bueno.
>
> **Relación con los gates existentes.** Hoy hay 3 capas: `.importlinter`
> (R-DIP, pero solo lista 3 paquetes), `.dependency-cruiser.cjs` (frontend, con
> gaps §2), `tests/plugins/test_premortem_invariants.py` (mecanismo: queues,
> k8s, compose drift — NO isolation). Estos tests **generalizan** lo que esos
> gates chequean parcialmente y **cubren los huecos** que dejaron pasar AP-1..AP-8.

---

## §0. Helpers compartidos (backend)

```python
# tests/architecture/_plugin_introspection.py
import ast, re
from pathlib import Path
import yaml

REPO = Path(__file__).resolve().parents[3]
BE = REPO / "hubara_agency" / "src" / "plugins"
FE = REPO / "frontend_dashboard" / "src" / "plugins"
PLATFORM = REPO / "hubara_agency" / "src" / "platform"

def all_manifests() -> list[tuple[str, dict]]:
    out = []
    for d in sorted(FE.iterdir()):
        if not d.is_dir() or d.name.startswith(("_", ".")): continue
        m = d / "plugin.yaml"
        if m.exists():
            out.append((d.name, yaml.safe_load(m.read_text()) or {}))
    return out

def real_imports(pyfile: Path) -> set[str]:
    """AST imports — NO docstrings/comments (evita el falso positivo de
    dispatcher.py:14 que es un ejemplo en el docstring)."""
    tree = ast.parse(pyfile.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
        elif isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
    return mods

def declared_modules(manifest: dict) -> list[str]:
    api = manifest.get("api") or {}
    agent = manifest.get("agent") or {}
    mods = []
    if api.get("python_module"): mods.append(api["python_module"])
    mods += [r["module"] for r in (api.get("legacy_routers") or [])]
    if agent.get("python_module"): mods.append(agent["python_module"])
    mods += [w["module"] for w in (agent.get("workers") or []) if w.get("module")]
    return mods
```

---

## §1. Backend (pytest, `tests/architecture/test_plugin_isolation.py`)

### P-1 · `test_manifest_modules_are_self_contained` — 🟢 (candado)
Regla P-SELF · AP-1. Todo módulo declarado por el manifest de X empieza con `src.plugins.<id>.`.
```python
def test_manifest_modules_are_self_contained():
    bad = []
    for pid, manifest in all_manifests():
        for mod in declared_modules(manifest):
            if not mod.startswith(f"src.plugins.{pid}."):
                bad.append(f"{pid}: declara {mod} fuera de su paquete")
    assert not bad, "\n".join(bad)
```
> Hoy pasa (chats declara `chats.api.eta` que ES `chats.*`). Post-refactor, cuando
> `eta` tenga su dir, sigue verde y previene la regresión de volver a meter
> módulos ajenos en un manifest.

### P-3 · `test_no_cross_plugin_imports` — 🟢 (generaliza importlinter)
Regla P-NOXIMPORT · AP-1. Ningún `src/plugins/X/**.py` importa `src.plugins.Y` (Y≠X).
```python
def test_no_cross_plugin_imports():
    bad = []
    for pdir in BE.iterdir():
        if not pdir.is_dir() or pdir.name.startswith(("_",".")): continue
        for py in pdir.rglob("*.py"):
            for imp in real_imports(py):
                m = re.match(r"src\.plugins\.([a-z0-9_]+)", imp)
                if m and m.group(1) != pdir.name:
                    bad.append(f"{py.relative_to(BE)} → {imp}")
    assert not bad, "Imports cross-plugin:\n" + "\n".join(bad)
```
> Más fuerte que `.importlinter` (que solo lista chats.agent.sales/remarketing +
> catalog.agent). Esto cubre TODOS los plugins, presentes y futuros.

### P-4 · `test_platform_never_imports_plugins` — 🟢 (cierra el gap del docstring)
Regla P-PLATFORM · F (R-DIP #9). Ningún `src/platform/**.py` importa `src.plugins.*` (AST, no docstrings).
```python
def test_platform_never_imports_plugins():
    bad = []
    for py in PLATFORM.rglob("*.py"):
        for imp in real_imports(py):
            if imp.startswith("src.plugins."):
                bad.append(f"{py.relative_to(PLATFORM)} → {imp}")
    assert not bad, "platform importa plugins:\n" + "\n".join(bad)
```
> El `.importlinter` solo prohíbe 3 paquetes-agente; esto prohíbe CUALQUIER
> plugin, y por AST evita el falso positivo del ejemplo en `dispatcher.py:14`.

### P-5 · ~~`test_transition_targets_declared_in_depends_on`~~ — ❌ RETIRADO (refinamiento 2026-06-05)
Regla retirada al implementar P-7. Quedó claro que las transitions cross-plugin son
**SOFT**, no deps duras: forzar `target ∈ depends_on` acoplaría `orders` a `chats`
(mal). La seguridad de toggle la da **P-7** (dispatcher-skip, ya implementado); las
deps DURAS van por **P-14** (`consumes`/cast ∈ depends_on). Por eso
`orders.depends_on: []` con transitions → chats/eta es **correcto** (no es violación).

### P-6 · `test_enabled_plugins_satisfies_depends_on` — 🔴 (requiere el loader nuevo)
Regla P-ENABLED · AP-3/AP-8. La validación de boot exige que las deps estén habilitadas.
```python
def test_validate_enabled_raises_on_missing_dep():
    from src.platform.plugin_loader import validate_enabled, PluginDependencyError  # NUEVO
    manifests = dict(all_manifests())
    with pytest.raises(PluginDependencyError):
        validate_enabled({"orders"}, manifests)        # orders depende de chats (post-fix)
    validate_enabled({"orders", "chats"}, manifests)   # ok, no levanta
```
> 🔴 Requiere implementar `validate_enabled()` en los loaders (`main.py` /
> `run_workers.py` / `plugins-sync.ts`). Sin él, habilitar un plugin sin su dep
> rompe en silencio.

### P-7 · `TestEnabledPluginsSkip` — 🟢 IMPLEMENTADO (2026-06-05)
Regla P-SKIP · AP-3 / F3. El dispatcher skipea transitions a plugins no habilitados.
Implementado en `dispatch_envelope_with_client`: lee `enabled_plugins()` de
`plugin_manifest` (`ENABLED_PLUGINS` ausente → `None` → no skipea = **prod-safe**) +
el campo nuevo `DispatchResult.skipped_disabled`. Tests en
`tests/platform/orchestration/test_dispatcher.py::TestEnabledPluginsSkip` (3): skip
cuando el target está apagado · no-skip con `ENABLED_PLUGINS` ausente · fire cuando
habilitado. **Verificado: 21 passed.** Cierra REQ-2 sin `depends_on` duro: `orders`
corre standalone, la notificación ETA simplemente no ocurre (degradación limpia).

### P-9 · `test_frontend_plugin_calls_only_own_api` — 🔴 (el que hubiera cazado AP-1)
Regla P-OWN/P-PARITY · AP-1/AP-6 / F1. El frontend de X no llama a `/api/<otro>/*`.
```python
def test_frontend_plugin_calls_only_own_api():
    ids = {d.name for d in FE.iterdir() if d.is_dir() and not d.name.startswith(("_","."))}
    bad = []
    for pid in ids:
        fdir = FE / pid / "frontend"
        if not fdir.exists(): continue
        for f in [*fdir.rglob("*.ts"), *fdir.rglob("*.tsx")]:
            txt = f.read_text(encoding="utf-8")
            for other in ids - {pid}:
                if f"/api/{other}/" in txt:
                    bad.append(f"{pid} llama /api/{other}/ en {f.name}")
    assert not bad, "Frontend llama API de otro plugin:\n" + "\n".join(bad)
```
> 🔴 (xfail, strict=False) Tras `ads` ✅ + `eta` ✅ extraídos, el ÚNICO ofensor que
> queda es `agents_admin` (Calidad LLM) → `/api/chats/evals`: el plano de gestión
> consume el eval del agente `sales` (`evals` queda PER-AGENTE — decisión
> 2026-06-05, NO se extrae; a formalizar server-side: agents_admin agrega los evals
> y sirve `/api/agents_admin/...`). `eta` ya NO es split (backend en `plugins/eta`,
> entity `tracked-order` → `/api/eta`). Verde cuando agents_admin no llame
> `/api/chats`. **OJO (PM-12):** el `reason` del xfail en el código todavía dice
> "eta sigue split" — STALE; el archivo es PROTECTED, actualizar pide `ARCH_CHANGE_APPROVED`.

### P-2 · `test_frontend_backend_parity` — 🟢 (post-extracciones ads/eta)
Regla P-PARITY · AP-1/AP-6 / F1/F13. Todo manifest que declara `api`/`agent` tiene backend propio; el set de ids es coherente.
```python
def test_every_backend_surface_has_own_dir():
    bad = []
    for pid, manifest in all_manifests():
        if manifest.get("api") or manifest.get("agent"):
            d = BE / pid
            has_code = d.is_dir() and any(p.suffix == ".py" and p.name != "__init__.py"
                                          for p in d.rglob("*.py"))
            if not has_code:
                bad.append(f"{pid}: declara backend pero src/plugins/{pid}/ no tiene código")
    assert not bad, "\n".join(bad)
```
> 🟢 Post-refactor: `eta` ya declara `api:` + `agent:` propios y tiene código bajo
> `src/plugins/eta/` → P-2 lo verifica de verdad (ya no es el caso "frontend-only que
> engaña"). Sigue complementado por P-9 (api-call) para cazar futuros splits donde el
> manifest sea frontend-only pero el frontend llame al backend de otro plugin.

### P-14 · `test_cross_plugin_data_goes_through_declared_cast` — 🔴 (mecanismo nuevo)
Regla P-CAST · AP-2 / F2/F8. Todo consumo cross-plugin de datos se declara en `consumes:` (provider+contract+into+cast) y el cast resuelve; ningún plugin adopta la entity de otro.
```python
def test_consumes_blocks_are_well_formed():
    bad = []
    for pid, manifest in all_manifests():
        deps = set(manifest.get("depends_on") or [])
        for c in manifest.get("consumes") or []:
            if c.get("provider") not in deps:
                bad.append(f"{pid}: consumes {c.get('provider')} pero no está en depends_on")
            cast = (c.get("cast") or "").lstrip("./")
            fe = FE / pid / "frontend" / cast
            be = BE.joinpath(pid, *cast.split("/"))
            if cast and not fe.exists() and not (be.with_suffix(".py").exists() or be.exists()):
                bad.append(f"{pid}: cast {c.get('cast')} no existe bajo su dir")
    assert not bad, "\n".join(bad)
```
> 🔴 El bloque `consumes:` es NUEVO (hay que agregarlo al schema del manifest). El
> test entra en verde cuando el caso `order` (chats→orders) se exprese como cast
> declarado server-side. La prohibición de importar la entity ajena en frontend la
> cubre la regla cruiser `plugins-own-entities-only` (P-10).

---

## §2. Frontend — dependency-cruiser (`.dependency-cruiser.cjs`)

### P-10 · reglas nuevas/reforzadas — 🟢 existentes + 🔴 gaps
Regla P-FECROSS · AP-7 / F10.
```js
// YA EXISTEN (mantener):
{ name: "plugins-no-cross-plugin", severity: "error",
  from: { path: "^src/plugins/([^/]+)/" },
  to:   { path: "^src/plugins/(?!$1)([^/]+)/" } },            // 🟢
{ name: "plugins-no-pages-app", severity: "error",
  from: { path: "^src/plugins/" }, to: { path: "^src/(pages|app)/" } },  // 🟢

// AGREGAR (gaps de la auditoría):
{ name: "plugins-no-features", severity: "error",            // F10 latente
  comment: "Un plugin no se acopla a la capa features compartida.",
  from: { path: "^src/plugins/" }, to: { path: "^src/features/" } },
{ name: "plugins-own-entities-only", severity: "error",      // AP-2 / F2/F8
  comment: "Un plugin solo importa SUS propias entities; datos de otro plugin entran por cast declarado (P-14), nunca importando la entity ajena.",
  from: { path: "^src/plugins/([^/]+)/" },
  to:   { path: "^src/plugins/(?!$1)[^/]+/frontend/entities/" } },
```

### P-11 · `test_central_entities_dir_is_empty` — 🔴 (vitest)
Regla P-ENTITY · AP-2 / F2. `src/entities/` queda VACÍO — toda entity es plugin-local (sin shared).
```ts
// src/test/architecture/entities_are_plugin_local.arch.test.ts
test("src/entities/ central está vacío (toda entity es plugin-local)", () => {
  const dirs = existsSync("src/entities")
    ? readdirSync("src/entities", { withFileTypes: true }).filter(d => d.isDirectory()).map(d => d.name)
    : [];
  expect(dirs).toEqual([]);   // cada entity vive en plugins/<id>/frontend/entities/
});
```
> 🔴 Hoy `src/entities/` tiene 10+ entities (incl. la recién agregada `eval-trend`).
> Verde cuando TODAS se muevan a sus plugins. **No hay allowlist compartida**: el
> caso `order` (lo usa chats) se resuelve por **cast declarado** (P-14), no por
> entity compartida.

---

## §3. Frontend — vitest (`src/test/architecture/`)

### P-12 · `test_manifest_icons_exist` — 🟢 (candado nuevo)
Regla P-ICON · AP-4 / F4/F11. Todo ícono de manifest resuelve en el registry (sin fallback `bot`).
```ts
import { Icon } from "@/shared/ui/Icon";
test("todo ícono declarado en un manifest existe en el registry", () => {
  const missing: string[] = [];
  for (const { id, icon } of allManifestIcons()) {          // sidebar + sections + dashboard
    if (!(icon in Icon)) missing.push(`${id}: ${icon}`);
  }
  expect(missing).toEqual([]);
});
```
> Hoy pasa (todos reusan íconos existentes) pero NADA lo garantizaba — un plugin
> con ícono nuevo renderizaba `bot` en silencio. Este candado lo vuelve un fallo
> de CI. Post-fix de íconos-contribuidos, valida contra base + contribuciones.

### P-13 · `test_plugin_ids_consistent_across_stacks` — 🟢/🔴
Regla P-PARITY · F13. El conjunto de ids es coherente entre frontend y backend.
```ts
test("cada plugin con backend declarado existe en ambos stacks", () => {
  // ids del frontend (dirs con plugin.yaml) vs dirs backend con código
  // + ningún manifest 'frontend-only' que en realidad consuma backend ajeno
  // (cross-check con el resultado de P-9)
});
```

---

## §3.5 Tests derivados del pre-mortem (P-15..P-19) — 🔴 PROPUESTOS

> Estos NO existían antes del refactor; salieron del
> [§9 pre-mortem del contrato](PLUGIN_CONTRACT.md#9-pre-mortem--modos-de-fallo-de-una-extracción).
> Cada uno atrapa un modo de fallo (PM-#) que NINGÚN gate actual ve, porque viven en
> strings, paths, o convenciones — no en imports/manifests. Son la red de seguridad
> que hubiera hecho la extracción de `eta` aburrida en vez de un campo minado.

### P-15 · `test_dashboard_workspace_paths_exist` — 🔴 (PM-6)
Regla nueva (P-WORKSPACE) · AP-1 adyacente. Todo `agent.workers[].dashboard.workspace` resuelve a un dir existente.
```python
def test_dashboard_workspace_paths_exist():
    bad = []
    for pid, manifest in all_manifests():
        for w in (manifest.get("agent") or {}).get("workers") or []:
            ws = ((w.get("dashboard") or {}).get("workspace") or "").strip()
            if ws and not (REPO / ws).is_dir():        # ws es path desde el repo root
                bad.append(f"{pid}/{w.get('name')}: workspace {ws!r} no existe en disco")
    assert not bad, "dashboard.workspace stale (PM-6):\n" + "\n".join(bad)
```
> Hubiera cazado en segundos si hubiera olvidado repointar `eta/agent/workspace` →
> `eta/agent/eta/workspace`. Hoy ese path se actualiza a mano sin red.

### P-16 · `test_worker_task_queue_self_reference_matches_dir` — 🔴 (PM-5 / AP-9)
Regla nueva (P-SELFQUEUE). Ningún `src/plugins/X/workers/*.py` llama `get_task_queue("Y", ...)` con Y≠X.
```python
def test_worker_task_queue_self_reference():
    bad = []
    for pdir in BE.iterdir():
        if not pdir.is_dir() or pdir.name.startswith(("_",".")): continue
        for py in (pdir / "workers").glob("*.py"):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "get_task_queue"
                        and node.args and isinstance(node.args[0], ast.Constant)):
                    if node.args[0].value != pdir.name:
                        bad.append(f"{py.relative_to(BE)} → get_task_queue({node.args[0].value!r}, ...) ≠ plugin {pdir.name!r}")
    assert not bad, "Worker referencia (plugin,worker) ajeno (PM-5):\n" + "\n".join(bad)
```
> El worker de eta tenía `get_task_queue("chats","eta")` y había que cambiarlo a
> `("eta","eta")`. Un grep de imports cross-plugin NO lo ve (es un string, no un import).

### P-17 · `test_agentic_flag_matches_dashboard_workers` — 🔴 (PM-3 / AP-11)
Regla nueva (P-AGENTIC). `agentic: true` ⟺ el plugin tiene ≥1 worker con bloque `dashboard:`. Cierra el mismatch schema↔código.
```python
def test_agentic_flag_consistent_with_dashboard():
    bad = []
    for pid, manifest in all_manifests():
        has_dash = any((w.get("dashboard")) for w in (manifest.get("agent") or {}).get("workers") or [])
        agentic = bool(manifest.get("agentic", False))
        if has_dash != agentic:
            bad.append(f"{pid}: agentic={agentic} pero has_dashboard_worker={has_dash}")
    assert not bad, "agentic ⊥ dashboard (PM-3):\n" + "\n".join(bad)
```
> El schema dice que `agentic` gatea `GET /api/agents_admin`, pero `service.py` lo
> IGNORA (escanea `dashboard:` blocks). Este test fuerza que no driften. **Fix
> complementario (código):** que `discover_agents()` filtre por `agentic` Y por
> `enabled_plugins()` (cierra PM-4/AP-8 también).

### P-18 · `test_routing_workflow_id_templates_are_consistent` — 🔴 (PM-2 / AP-10)
Regla nueva (P-ROUTE). El template de `workflow_id` hardcodeado en el ruteo de inbounds (`chats`) coincide con el `workflow_id_template` que el manifest de `orders` declara para el mismo agente. Mientras el route-registry declarativo (§5.1) siga diferido, este guard ata las dos copias.
```python
def test_routing_template_matches_manifest():
    # 1. extraé los prefijos de workflow_id del ruteo de inbounds (AST: f-strings `eta-{...}`)
    use_case = BE / "chats/agent/sales/use_cases/load_or_start_sales_session.py"
    hardcoded = _f_string_prefixes(use_case)            # {"eta-"} (de f"eta-{session_id}")
    # 2. prefijos declarados en TODAS las transitions de los manifests
    declared = set()
    for _pid, m in all_manifests():
        for w in (m.get("agent") or {}).get("workers") or []:
            for t in (w.get("transitions") or []):
                tpl = (t.get("action") or {}).get("workflow_id_template", "")
                declared.add(tpl.split("{")[0])         # "eta-" de "eta-{event.session_id}"
    assert hardcoded <= declared, (
        f"Ruteo usa prefijos {hardcoded - declared} que ningún manifest declara (PM-2) — "
        "el template de workflow_id drifteó entre chats y orders")
```
> 🔴 Es un parche mientras exista el residuo de Opción A. El fix REAL es el route
> registry (`agent.owns_route`) que elimina el hardcode — cuando eso entre, este test
> se reemplaza por "el ruteo NO hardcodea ningún `<plugin>-{...}`".

### P-19 · `test_transition_resolves_to_live_worker_config` — 🟡 (PM-13)

> **CORRECCIÓN (auditoría fable 2026-06-09):** la mitad ESTÁTICA de este test
> **ya existe** y este doc no lo registraba:
> `tests/architecture/test_manifest_orchestration_consistency.py` valida (a)
> `workflow_classes` ⇔ `@workflow.defn(name=)` por AST, (b) `on_event ∈ emits`,
> (c) target `(plugin, worker)` + `target_workflow` resuelven contra el índice
> de manifests, (d) eventos importables. Lo que falta es SOLO el smoke
> funcional (emitir el evento y assert que el workflow arranca).
Regla reforzada (P-DISPATCH). Más fuerte que "el target existe": para cada transition, el `(task_queue, workflow_name)` que el dispatcher RESOLVERÍA coincide con lo que el worker target realmente registra. Acerca el gate al comportamiento sin bootear Temporal.
```python
def test_transition_targets_resolve_to_worker_runtime():
    bad = []
    for _pid, m in all_manifests():
        for w in (m.get("agent") or {}).get("workers") or []:
            for t in (w.get("transitions") or []):
                tp, tw = t["action"]["target_plugin"], t["action"]["target_worker"]
                # el target worker debe declarar el workflow_name que la transition invoca
                tgt = get_worker_spec(tp, tw)                       # raise si no existe
                wf = t["action"].get("target_workflow")
                if wf and wf not in (tgt.get("workflow_classes") or []):
                    bad.append(f"{tp}/{tw}: transition invoca {wf} que el worker no declara")
                # y la task_queue debe resolver (no raise)
                get_task_queue(tp, tw)
    assert not bad, "Transition no resuelve al runtime del worker (PM-13):\n" + "\n".join(bad)
```
> 🔴 Complemento AST de un smoke de comportamiento real (emitir el evento y ver el
> workflow arrancar). Lo ideal es AMBOS: este (rápido, en CI) + un functional test
> que dispare un `OrderStageChangedEvent` y assert que el workflow eta arranca en
> `queue-eta-agent`.

---

## §4. Resumen — test → regla → anti-pattern → estado hoy → archivo destino

| Test | Regla | AP/F | Hoy | Destino |
|---|---|---|---|---|
| Test | Regla | AP/F/PM | Hoy | Destino |
|---|---|---|---|---|
| P-1 self-contained modules | P-SELF | AP-1 | 🟢 hecho | `tests/architecture/test_plugin_contract.py` |
| P-2 backend parity | P-PARITY | AP-1/F13 | 🟢 hecho | idem (eta/ads ya declaran backend propio) |
| P-3 no cross-plugin import | P-NOXIMPORT | AP-1 | 🟢 hecho | idem (+ generaliza `.importlinter`) |
| P-4 platform↛plugins | P-PLATFORM | F(R-DIP#9) | 🟢 hecho | idem |
| ~~P-5 transition targets ∈ depends_on~~ | — | — | ❌ retirado | reemplazado por P-7 + P-14 |
| P-6 enabled satisfies depends_on | P-ENABLED | AP-3/AP-8 | 🔴 | `tests/architecture/` + `platform/plugin_loader.py` (nuevo) |
| P-7 dispatcher skips disabled | P-SKIP | AP-3/F3 | 🟢 hecho | `dispatcher.py` + `test_dispatcher.py::TestEnabledPluginsSkip` |
| P-9 frontend calls own API only | P-OWN | AP-1/F1 | 🟢 ESTRICTO (F5 fable: casts order-ref + evals server-side) | `test_plugin_contract.py` |
| P-10 cruiser `plugins-no-features` | P-FECROSS | AP-7/F10 | 🟡 `plugins-no-features` hecho; `plugins-own-entities-only` PENDIENTE (entra con P-11, plan F4) | `.dependency-cruiser.cjs` |
| P-11 central entities dir empty | P-ENTITY | AP-2/F2/PM-9 | 🟢 (F4 fable: 11/11 entities migradas; src/entities/ vacío) | `src/test/architecture/test_plugin_entity_ownership.arch.test.ts` |
| P-14 cross-plugin via declared cast | P-CAST | AP-2/F2/F8 | 🟢 forma + USO (2 consumes reales: chats→orders order-ref · agents_admin→chats evals) | `test_plugin_contract.py` |
| P-12 manifest icons exist | P-ICON | AP-4/F4 | 🟢 hecho | `src/test/architecture/test_plugin_icons.arch.test.ts` |
| P-13 ids consistent cross-stack | P-PARITY | F13 | 🟡 | `src/test/architecture/` |
| **P-15 workspace paths exist** | P-WORKSPACE | PM-6 | 🔴 | `tests/architecture/` |
| **P-16 worker queue self-ref** | P-SELFQUEUE | PM-5/AP-9 | 🔴 | `tests/architecture/` |
| **P-17 agentic ⟺ dashboard** | P-AGENTIC | PM-3/AP-11 | 🔴 | `tests/architecture/` (+ fix código agents_admin) |
| **P-18 routing template consistency** | P-ROUTE | PM-2/AP-10 | 🔴 | `tests/architecture/` (parche hasta route-registry) |
| **P-19 transition→worker runtime** | P-DISPATCH | PM-13 | 🔴 | `tests/architecture/` + functional smoke |

**ACTUALIZACIÓN FINAL (refactor fable F1–F8, 2026-06-09/10): TODO el set está
VERDE.** P-1/2/3/4 + P-6 (validate_enabled ×3 loaders) + P-7 + P-9 ESTRICTO
(sin xfail) + P-10 + P-11 (src/entities/ vacío) + P-12 (base ∪ contribuciones)
+ P-13/P-26 (coherencia cross-stack + dirs huérfanos) + P-14 (forma + USO: 2
casts reales) + P-15/P-16/P-17 + P-18 ×3 (route registry) + P-19-estático.
**Nuevos del refactor:** P-20 (deploy parity compose+k8s), P-21 (worker
self-gate), P-22/P-23 (ownership de entities + literales /api en código),
P-25 (wiring↔compose env). Dónde vive cada uno + la regla de oro ("ningún
campo del manifest sin su check"): [PLUGIN_PROTOCOL_fable.md](PLUGIN_PROTOCOL_fable.md).
Pendiente deliberado: el smoke FUNCIONAL del dispatch (P-19b — emitir un
OrderStageChangedEvent real contra Temporal y assert que el workflow eta
arranca) vive mejor en `tests/functional/` con el stack Docker arriba.

---

## §5. Integración: CI + el agente enforcement

- **CI backend:** `tests/architecture/test_plugin_isolation.py` corre con
  `uv run pytest -m architecture` (junto a los gates DEHA existentes). Los nuevos
  tests son AST/filesystem puros → sin DB, rápidos, deterministas.
- **CI frontend:** las reglas dep-cruiser corren con `npm run test:arch`; los
  vitest de íconos/entities/parity en `npm test`.
- **El agente enforcement** (futuro): lee [PLUGIN_CONTRACT.md](PLUGIN_CONTRACT.md)
  (las reglas) + este doc (los tests) y, ante cualquier PR que agregue/modifique
  un plugin, corre el checklist §6 del contrato + estos tests. Un test rojo (fuera
  de los conocidos) = bloqueo de merge, igual que `review-pr-hubara` bloquea hoy
  por R-DIP.
- **Orden de adopción sugerido:** (1) agregar los 🟢 YA como candados (no rompen
  nada, congelan lo bueno); (2) agregar los 🔴 como `xfail`/skip documentado
  apuntando a su PR de fix; (3) cada PR del refactor (§5 audit) vuelve verde su
  test y le saca el `xfail`.

---

**Fin.** Estos tests convierten el contrato en algo que **CI hace cumplir**, no en
buenas intenciones. El set rojo es el mapa del refactor; el verde, el candado.
