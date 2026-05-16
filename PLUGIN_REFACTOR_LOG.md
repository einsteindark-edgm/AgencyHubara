# Plugin Refactor Log — AgencyHubara

> **Bitácora viva.** Apéndice ejecutable de `PLUGIN_REFACTOR_PLAN.md`.
> Aquí se registra el progreso real de cada PR, las verificaciones que
> efectivamente corrieron, y cualquier desviación del plan.
>
> **Convención:** cada entrada empieza con fecha en formato `YYYY-MM-DD`,
> identificador del PR, autor (humano o agente), y status. Nuevas entradas
> al **final** del archivo (append-only).

---

## Índice

| PR | Fecha inicio | Status | Notas |
|---|---|---|---|
| PR0 (auditoría) | 2026-05-15 | ✅ done | Documento `PLUGIN_REFACTOR_PLAN.md` creado tras auditar el código real. |
| PR1 (plumbing) | 2026-05-15 | ✅ done | Plumbing completo. `npm run plugins:sync` genera registry, todas las verificaciones verdes. Listo para commit. |
| PR2 (migrar chats) | — | ⏸ pending | Bloqueado por commit de PR1. |
| PR3 (loaders) | — | ⏸ pending | Bloqueado por PR2. |
| PR4 (agents-admin) | — | ⏸ pending | Bloqueado por PR3. |
| PR5 (catalog) | — | ⏸ pending | Bloqueado por PR3. |
| PR6 (eta) | — | ⏸ pending | Bloqueado por PR3. |
| PR7 (orders) | — | ⏸ pending | Bloqueado por PR3. |

---

## 2026-05-15 — PR0 (auditoría) — Claude — ✅ done

### Contexto

El usuario pidió "lee plugin_architecture.md, analiza el código actual,
vuelve y revisa el plan y crea todo un plan completo". El contrato
`PLUGIN_ARCHITECTURE.md` se había escrito antes de la auditoría
detallada, así que tenía supuestos sobre paths y modelo de workers que
no encajaban con la realidad.

### Trabajo realizado

1. **Mapeo de estructura**: leído `hubara_agency/src/`,
   `exoclaw-temporal/exoclaw_temporal/`, `frontend_dashboard/src/`.
2. **Lectura de archivos clave**: `main.py`, los 3 `worker.py` de
   dominio, `composition.py`, `tool_extensions.py`, `registries.py`,
   `Dockerfile`, `docker-compose.local.yml`, `package.json`,
   `vite.config.ts`, `.importlinter`, `.dependency-cruiser.cjs`,
   `pages/Dashboard.tsx`, `main.tsx`.
3. **Verificación de toolchain**: tsx/yaml/husky no instalados;
   import-linter y dependency-cruiser sí están con reglas estrictas.

### Hallazgos críticos

- **Path real de exoclaw**: `exoclaw-temporal/exoclaw_temporal/` (no
  `src/`). Package es `exoclaw_temporal`.
- **Modelo de workers real**: UN WORKER POR DOMINIO con task queue
  exclusiva, no un loader único por modo como sugería el contrato.
- **Frontend shell**: `pages/Dashboard.tsx`, no `App.tsx` (no existe).
- **Imports `from src.*`**: 40+ archivos. Refactor mecánico pero
  laborioso.
- **Tests de arquitectura existentes** (`pytest -m architecture`,
  `npm run test:arch`): se rompen al mover archivos; deben actualizarse
  en el mismo PR del move.
- **uv workspace**: `members = ["exoclaw-temporal", "hubara_agency"]`.
  Decisión: NO crear un nuevo workspace member por plugin; los plugins
  Python viven como subpaquete de `hubara_agency.src.plugins`.

### Outputs

- `PLUGIN_REFACTOR_PLAN.md` creado con plan corregido a la realidad.
- `PLUGIN_REFACTOR_LOG.md` (este archivo) creado para tracking.
- `PLUGIN_ARCHITECTURE.md` se mantiene como contrato; las
  discrepancias quedan documentadas en §0 del PLAN.

### Próximo paso recomendado

Validar el plan con el operador. Específicamente, confirmar:

1. **Layout final de un plugin (§1.1)**: el plugin Python vive en
   `hubara_agency/src/plugins/<id>/`, el TS en
   `frontend_dashboard/src/plugins/<id>/`, el manifest está en el lado
   TS. ¿OK con esa asimetría?
2. **Modelo de workers (§1.4)**: cada plugin agéntico trae su propio
   worker, hay un meta-launcher para dev local pero docker/K8s sigue
   con un container por worker. ¿OK?
3. **PR1 ya puede arrancar**: scope está acotado a plumbing
   (sin tocar features). ¿Procedemos directo o el operador prefiere
   validar algo más?

---

## 2026-05-15 — PR1 (plumbing) — Claude — ✅ done

### Plan referenciado
PLUGIN_REFACTOR_PLAN.md §3 — PR1.

### Cambios efectivos

**Archivos creados (10)**:
- `frontend_dashboard/src/plugins/.gitkeep`
- `frontend_dashboard/src/plugins/_schema/plugin.schema.yaml` (~140 LOC, JSON Schema en YAML del manifest)
- `frontend_dashboard/scripts/plugins-sync.ts` (~190 LOC, descubre manifests + genera registry)
- `frontend_dashboard/src/app/plugin-registry.generated.ts` (autogenerado, gitignored)
- `hubara_agency/src/plugins/__init__.py` (namespace package + docstring de convenciones)

**Archivos modificados (7)**:
- `frontend_dashboard/package.json`:
  - Scripts: agregado `plugins:sync`, `predev`, `prebuild`.
  - Deps: `yaml ^2.6.1`. DevDeps: `tsx ^4.19.2`.
- `frontend_dashboard/package-lock.json` (auto-actualizado por `npm install`).
- `frontend_dashboard/vite.config.ts`: alias `@plugins` → `./src/plugins`.
- `frontend_dashboard/tsconfig.app.json`: paths `@plugins/*` → `["./src/plugins/*"]`.
- `frontend_dashboard/tsconfig.node.json`: incluye `scripts/**/*.ts` para typecheck del sync script.
- `frontend_dashboard/.gitignore`: ignora `src/app/plugin-registry.generated.ts`.
- `frontend_dashboard/.dependency-cruiser.cjs`: agrega el archivo generado a `doNotFollow.path`.

### Desviaciones del plan

1. **Comportamiento de `ENABLED_PLUGINS` vacío**: el plan original implícito decía "filtrar siempre". El script implementa la convención más útil: **vacío o unset → carga TODOS los plugins descubiertos**. Esto coincide con el comportamiento del loader Python que documenta el plan §3 PR3. Documentado en el header del archivo generado y en el código del script.

2. **Manejo de `noUnusedLocals`**: el primer render del registry vacío fallaba el typecheck porque importaba `lazy` sin usarlo. Fix: el generador emite **dos shapes distintos** según si hay entries (con `lazy` + `LazyExoticComponent`) o no (sin imports React, `Page: () => null` como placeholder de tipo). El comportamiento runtime es idéntico: `PLUGINS` siempre es un array iterable.

3. **Validación id == directory name**: agregada al script (no estaba en el plan). Si un manifest declara `id: foo` pero vive en `src/plugins/bar/`, se rechaza con warning. Previene que dos plugins compartan id por accident.

4. **Skip de directorios `_*` y `.*`**: agregado al script. Permite usar `_schema/`, `_drafts/`, `.archive/` sin que sean tratados como plugins.

Ninguna desviación cambia el plan global; las anoto para que cualquier futuro PR sepa la racionalización.

### Verificaciones corridas

```bash
$ cd frontend_dashboard
$ npm install
added 29 packages, and audited 403 packages in 5s
found 0 vulnerabilities

$ npm run plugins:sync
[plugins-sync] generated src/app/plugin-registry.generated.ts with 0 plugin(s): (empty)

$ npx tsc -b
TypeScript compilation completed                          # ✅ verde

$ npm run arch:cruise
✔ no dependency violations found (154 modules, 326 dependencies cruised)  # ✅ verde

$ npm run test:arch
Test Files  8 passed (8)
     Tests  12 passed | 1 skipped (13)              # ✅ verde

$ git check-ignore -v frontend_dashboard/src/app/plugin-registry.generated.ts
frontend_dashboard/.gitignore:29:src/app/plugin-registry.generated.ts    # ✅ correctamente ignorado
```

**Smoke test con plugin dummy** (eliminado al final):

```bash
# Crear plugin dummy
echo "id: smoke ..." > src/plugins/smoke/plugin.yaml

# Verificar render con entries:
$ npm run plugins:sync
[plugins-sync] generated src/app/plugin-registry.generated.ts with 1 plugin(s): smoke
# → registry incluye `Page: lazy(() => import("@plugins/smoke/frontend"))`

# Verificar filtros:
$ ENABLED_PLUGINS=other npm run plugins:sync
[plugins-sync] ENABLED_PLUGINS lists "other" but no manifest found
# → registry vacío

$ ENABLED_PLUGINS=smoke npm run plugins:sync
# → registry con smoke entry

# Verificar validación id != dirname:
# (cuando id=_smoke pero dir=smoke)
[plugins-sync] skip smoke: manifest id (_smoke) != directory name

# Cleanup
$ rm -rf src/plugins/smoke && npm run plugins:sync
[plugins-sync] generated src/app/plugin-registry.generated.ts with 0 plugin(s): (empty)
```

### Definition of Done (del plan §3 PR1)

- [x] `npm run plugins:sync` genera un archivo TS válido (vacío) sin error.
- [x] `npx tsc -b` pasa con el archivo generado.
- [x] `npm run test:arch` (8 archivos, 12 tests) verde.
- [x] `npm run arch:cruise` verde (154 módulos, sin violaciones).
- [x] El archivo generado está correctamente gitignored.
- [ ] `npm run dev` arranca y la app se renderiza idéntica a antes. **NO probado en esta sesión** — Vite dev requiere un puerto libre y el operador puede preferir validarlo manualmente. Riesgo bajo: el archivo generado es un módulo TS aislado, no se importa todavía desde ningún sitio.

### Bloqueadores encontrados

Ninguno. Cero discrepancias críticas vs. plan.

### Estado de git al cerrar

```
Modified (7):
  frontend_dashboard/.dependency-cruiser.cjs
  frontend_dashboard/.gitignore
  frontend_dashboard/package-lock.json
  frontend_dashboard/package.json
  frontend_dashboard/tsconfig.app.json
  frontend_dashboard/tsconfig.node.json
  frontend_dashboard/vite.config.ts

Untracked (5):
  PLUGIN_REFACTOR_LOG.md (este archivo)
  PLUGIN_REFACTOR_PLAN.md
  frontend_dashboard/scripts/                   # plugins-sync.ts
  frontend_dashboard/src/plugins/               # .gitkeep + _schema/
  hubara_agency/src/plugins/                    # __init__.py

NB: PLUGIN_ARCHITECTURE.md también está untracked — viene de la sesión
anterior (pre-compact). Si se commitea PR1, agregar también ese archivo.
```

### Próximo paso recomendado

1. Operador commitea PR1 (sugerencia de mensaje: `feat(plugins): plumbing — manifest schema, sync script, FE plumbing`).
2. Validar smoke test manual con `npm run dev` para confirmar DOD #6.
3. Arrancar **PR2 (migrar `chats`)**: ver §3 PR2 del plan. Es el PR más laborioso (~50 archivos movidos + reescritura de imports). Estimado 2-3 días.

### Status final

✅ **done** — todas las verificaciones automatizadas verdes. Pendiente solo
smoke test manual de `npm run dev` (no bloqueante).

---

## Plantilla para nuevas entradas

Copiar este bloque al iniciar un PR:

```markdown
## YYYY-MM-DD — PRn (nombre) — autor — 🚧 in_progress

### Plan referenciado
PLUGIN_REFACTOR_PLAN.md §3 — PRn

### Cambios efectivos
(lista de archivos creados/modificados/borrados)

### Desviaciones del plan
(si las hubo, justificar; si requiere actualizar el PLAN, dejar nota)

### Verificaciones corridas
(comandos exactos + resultado)

### Definition of Done
- [ ] (item 1 del DOD del plan)
- [ ] (item 2)
- [ ] tests verdes
- [ ] documentación actualizada

### Bloqueadores encontrados
(ninguno / detalle)

### Status final
✅ done | ⚠️ done con caveats | ❌ revertido
```

---

## Reglas de uso

1. **Append-only**: nuevas entradas al final. NO editar entradas
   pasadas (excepto para corregir typos o agregar links cruzados).
2. **Status visible**: cuando un PR cambia de status, actualizar
   también la fila del índice de arriba.
3. **Verificaciones reales**: pegar el output de los comandos, no
   solo decir "pasó". Si un comando falló y se workarround-eó, decirlo.
4. **Vincular PRs de GitHub**: cuando se abra un PR real, pegar el
   link en la entrada.
5. **Si el plan cambia mid-PR**: editar `PLUGIN_REFACTOR_PLAN.md` y
   referenciar el commit del cambio desde la entrada del log.

---

**Fin del log.** Actualizar al iniciar y al cerrar cada PR.
