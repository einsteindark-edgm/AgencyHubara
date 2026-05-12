# Pipeline FSD + Archon — Frontend (guía operacional)

Este pipeline transforma una HU de frontend (FSD: Vite + React 19 + TanStack
Query + Zod + Tailwind v4) en código de producción para `frontend_dashboard/`,
vía workflows de Archon coordinados por file-system, convenciones git, y
opcionalmente GitHub Projects. Soporta paralelismo total entre agentes
programadores (uno por tarea atómica del DAG).

Es el **espejo del pipeline exoclaw** (`README.md`) pero adaptado a FSD y con
una variante automatizada extra (`hu-frontend-pipeline`).

---

## 1. Componentes

### Skills (`.claude/skills/`)

| Skill | Rol | Escribe código? |
|-------|-----|-----------------|
| `frontend-tech-refiner-archon` | refinamiento técnico de la HU | no |
| `frontend-task-planner-archon` | descomposición en DAG de tareas + parallel_batches | no |
| `frontend-implementer-archon` | implementa UNA tarea (1 worktree por tarea) | **sí** |
| `frontend-merger-archon` | consolida wiring_intents de N tareas paralelas en spinal files | **sí** (sólo spinal files) |

### Workflows (`.archon/workflows/`)

| Workflow | Comando | Rol | Auto |
|----------|---------|-----|------|
| `refinar-hu-frontend` | `archon workflow run refinar-hu-frontend "<input>"` | HU → refinamiento técnico | no (loop interactivo) |
| `planificar-hu-frontend` | `archon workflow run planificar-hu-frontend "<HU-id>"` | refinamiento → DAG de tareas | no |
| `implementar-tarea-frontend` | `archon workflow run implementar-tarea-frontend "<HU-id> F<NN>"` | una tarea → código | no |
| `implementar-hu-frontend` | `archon workflow run implementar-hu-frontend "<HU-id>"` | orquestador: terminales paralelas + merger | no (manual fan-out) |
| `implementar-tarea-frontend-auto` | (no se invoca a mano) | una tarea → código, sin gates | **sí** |
| `hu-frontend-pipeline` | `archon workflow run hu-frontend-pipeline "<issue-url-or-input>"` | super-pipeline E2E + GitHub PR | **sí** |

### Convenciones (`frontend_dashboard/.frontend/`)

| Archivo | Quién lo escribe | Quién lo lee |
|---------|------------------|--------------|
| `spinal-files.yaml` | el operador (1 vez por frontend) | planner, implementer, merger |
| `project-context.md` | el operador (1 vez) | TODAS las skills (lo leen primero) |
| `github-project-config.yaml` | el operador (1 vez, opcional) | hu-frontend-pipeline |
| `refinements/<id>-tech.md` | refinar-hu-frontend / hu-frontend-pipeline | planificar-hu-frontend |
| `refinements/<id>-original.md` | refinar-hu-frontend / hu-frontend-pipeline | planificar-hu-frontend (fallback) |
| `plans/<id>/plan-manifest.yaml` | planificar-hu-frontend / hu-frontend-pipeline | implementar-tarea-frontend, implementar-hu-frontend |
| `plans/<id>/tareas/F<NN>-<slug>.md` | planificar-hu-frontend / hu-frontend-pipeline | implementar-tarea-frontend |
| `results/<id>/F<NN>-result.yaml` | implementar-tarea-frontend / hu-frontend-pipeline | implementar-hu-frontend, hu-frontend-pipeline |

---

## 2. Dos modos de uso

### Modo A — INTERACTIVO (igual que exoclaw)

Mismo flujo manual de 3 fases, con gates humanos entre cada una. Útil cuando
estás iterando arquitectura y querés revisar refinamiento + plan antes de
implementar.

```bash
# FASE 1 — refinar (loop interactivo)
archon workflow run refinar-hu-frontend "specs/HU-XYZ.md"
# Iterás hasta "aprobada". Al final commiteás:
git add frontend_dashboard/.frontend/refinements/<id>-*.md
git commit -m "<id>: refinamiento (frontend) aprobado"
git push

# FASE 2 — planificar (loop interactivo)
archon workflow run planificar-hu-frontend "<id>"
# Iterás hasta "aprobado". Al final commiteás:
git add frontend_dashboard/.frontend/plans/<id>/
git commit -m "<id>: plan (frontend) aprobado"
git push

# FASE 3 — implementar (orquestador con fan-out manual de terminales)
archon workflow run implementar-hu-frontend "<id>"
# El workflow te guía: te dice los N comandos a lanzar en N terminales,
# vos los corrés, esperás, commiteás (sin spinal files), volvés y
# respondés "ready" para invocar al merger.
```

### Modo B — AUTOMATIZADO (nuevo, exclusivo de frontend)

Un solo comando hace todo de punta a punta. Sin gates humanos.

```bash
# Setup (1 sola vez por frontend):
#   - Asegurate de que .frontend/spinal-files.yaml y .frontend/project-context.md
#     existan en main.
#   - (Opcional) Configurá .frontend/github-project-config.yaml — ver
#     `.frontend/github-project-config.yaml.example`.

# Por cada HU:
archon workflow run hu-frontend-pipeline "https://github.com/<owner>/<repo>/issues/42"

# El pipeline:
#   1. Lee el body del Issue como HU.
#   2. Crea branch hu/<HU_ID> desde main.
#   3. Refina automáticamente → commit a hu/<HU_ID> (sin push).
#   4. Planifica automáticamente → commit a hu/<HU_ID>.
#   5. Para cada batch B<k>:
#      a. Lanza M `archon workflow run implementar-tarea-frontend-auto ...` en background.
#      b. Espera con `wait`.
#      c. Trae results + new files al worktree, commit.
#      d. Invoca al merger, commit consolidado.
#   6. Push hu/<HU_ID> a origin.
#   7. `gh pr create --base main --head hu/<HU_ID>` con body autogenerado.
#   8. (Si GitHub Project config existe) actualiza card al estado correspondiente
#      en cada fase.

# Vos:
#   - Revisás el PR cuando termina.
#   - Squash-merge si todo OK.
#   - El issue se cierra automáticamente (Closes <url> en el body del PR).

# Si algo falla:
#   - El pipeline para, deja el branch hu/<HU_ID> con el progreso hasta ese punto.
#   - Movés el card a "Blocked: ..." en el Project (lo hace automáticamente).
#   - Retomás manualmente con el workflow interactivo:
#       archon workflow run refinar-hu-frontend "<HU_ID>"   # si falló FASE 1
#       archon workflow run planificar-hu-frontend "<HU_ID>" # si falló FASE 2
#       archon workflow run implementar-tarea-frontend "<HU_ID> F<NN>"  # si falló una tarea
#   - Una vez resuelto, podés re-lanzar el pipeline desde donde quedó:
#       archon workflow run hu-frontend-pipeline "<HU_ID>"
#     (detecta el branch hu/<HU_ID> existente y continúa).
```

---

## 3. Diferencias clave vs el pipeline exoclaw

| Aspecto | Pipeline exoclaw (Python) | Pipeline frontend (FSD) |
|---|---|---|
| Lenguaje target | Python (uv workspace) | TypeScript + React (npm + Vite) |
| Hard rules | R-DET, R-JSON, R-STATELESS, R-HEARTBEAT, R-DIP | 4 import rules FSD + 14 anti-patterns |
| Comando de test | `cd hubara_agency && uv run pytest ...` | `cd frontend_dashboard && npm test ...` |
| Spinal files típicos | `worker.py`, `composition.py`, `contracts.py`, `workspace/*.md` | `pages/<X>.tsx`, `app/providers/index.tsx`, `index.css`, barrels |
| Wiring intent kinds | `register_tool_extension`, `factory_function`, `dataclass_def`, `markdown_section`, … | `page_feature_mount`, `provider_wrap`, `tailwind_token`, `barrel_export`, `zod_schema_def`, … |
| Modo auto-pipeline | ❌ no existe (solo interactivo) | ✅ `hu-frontend-pipeline` |
| GitHub Projects sync | ❌ | ✅ opcional (vía `.frontend/github-project-config.yaml`) |
| Branch strategy | trabajo en main directo (commits manuales) | branch `hu/<HU_ID>` aislado, PR al final |

---

## 4. Modelo de paths

Idéntico a exoclaw pero con `frontend_dashboard/.frontend/` en vez de
`hubara_agency/.exoclaw/`:

```
$ARTIFACTS_DIR (efímero, ~/.archon/workspaces/.../artifacts/runs/<id>/)
├── hu-original.md
├── hu-refinada.md
├── plan-manifest.yaml
├── tareas/F<NN>-<slug>.md
├── task.md
├── task-result.yaml
├── batch-results/F<NN>-result.yaml
├── merge-report.yaml
├── project-context.md           ← copiado del repo en cada cargar-*
├── spinal-files.yaml            ← idem
├── github-project-config.yaml   ← solo en hu-frontend-pipeline si existe
└── pipeline-state.yaml          ← solo en hu-frontend-pipeline (telemetría)

<repo>/frontend_dashboard/.frontend/ (durable, en el repo)
├── spinal-files.yaml
├── project-context.md
├── github-project-config.yaml (opcional)
├── refinements/<id>-tech.md
├── refinements/<id>-original.md
├── plans/<id>/plan-manifest.yaml
├── plans/<id>/tareas/F<NN>-*.md
└── results/<id>/F<NN>-result.yaml
```

---

## 5. Cuándo usar cada modo

| Caso | Modo recomendado |
|------|------------------|
| HU nueva, no estoy seguro de la arquitectura | Modo A (interactivo) — revisás refinamiento + plan antes de implementar |
| HU clara, equipo conoce la app | Modo B (pipeline auto) — un comando y café |
| HU con riesgo alto (cambia muchos features) | Modo A — humano en cada gate |
| HU rutinaria (nuevo CRUD, nuevo modal) | Modo B |
| Operador no está cerca del teclado | Modo B con GitHub Projects + notif al terminar el PR |
| HU bloqueada en producción urgente | Modo A — control granular, sin sorpresas |

El modo B no reemplaza al modo A. Coexisten: el modo B internamente usa
las mismas skills, y si falla cae a las versiones interactivas para que el
operador retome.

---

## 6. Setup inicial (1 vez por repo)

```bash
# 1. Convenciones del frontend (estos archivos van committed a main):
ls frontend_dashboard/.frontend/
#   → spinal-files.yaml      (ya está commiteado)
#   → project-context.md     (ya está commiteado)

# 2. (Opcional) GitHub Project config para el modo auto:
cp frontend_dashboard/.frontend/github-project-config.yaml.example \
   frontend_dashboard/.frontend/github-project-config.yaml
# Edita los IDs (ver instrucciones inline).
git add frontend_dashboard/.frontend/github-project-config.yaml
git commit -m "frontend: github project config"
git push

# 3. Asegurate de tener `gh` autenticado:
gh auth status

# 4. Verificá que los 4 skills están en .claude/skills/:
ls .claude/skills/ | grep frontend-.*-archon
#   → frontend-tech-refiner-archon
#   → frontend-task-planner-archon
#   → frontend-implementer-archon
#   → frontend-merger-archon
```

---

## 7. Troubleshooting (frontend-específico)

| Síntoma | Causa probable | Acción |
|---------|----------------|--------|
| `planificar-hu-frontend` no encuentra `.frontend/refinements/<id>-tech.md` | refinement no mergeado a main | `git push` o merge a main + re-lanzá |
| `hu-frontend-pipeline` falla en FASE 1 con "validation_failed" | el refinement auto quedó incompleto (sin AC, sin entities, etc.) | retomar con `refinar-hu-frontend "<id>"` (modo interactivo) y darle feedback |
| `hu-frontend-pipeline` falla en FASE 2 con "task_count > 15" | la HU se descompuso en demasiadas tareas | retomar con `planificar-hu-frontend "<id>"` y pedir agrupación |
| `npm test` falla en una tarea pero los tests de §10 ya pasaron | regresión en código NO tocado por la tarea (touched another feature) | marcado como `blocked: regression`; revisar el test fallido a mano |
| `tsc -b` falla después del merge consolidado | spinal file quedó sintácticamente inválido | revisá `merge-report.yaml`, el archivo se restauró; re-correr planificar para evitar la colisión |
| Subprocess de `implementar-tarea-frontend-auto` no se encuentra | el `head -1` del worktree fish colisionó con otro subprocess | capturar `$ARCHON_WORKTREE_PATH` por F<NN> en Paso 3.3 (ya está en el yaml) |
| `gh pr create` falla con auth | `gh auth status` muestra desconectado | `gh auth login` + re-lanzar el pipeline (continúa desde el branch existente) |
| Card del Project no se actualiza | `github-project-config.yaml` con IDs incorrectos | re-correr `gh project field-list <N> --format json` y actualizar |
| Pipeline se queda colgado en FASE 3 sin `wait` | algún `archon workflow run ... &` no terminó | matar PIDs en `$ARTIFACTS_DIR/batch-logs/B<k>-pids.txt` y retomar |

---

## 8. Quick reference de artifacts

```
$ARTIFACTS_DIR/                       (efímero, por run)
├── hu-original.md
├── hu-refinada.md
├── plan-manifest.yaml
├── tareas/F<NN>-<slug>.md
├── task.md
├── task-result.yaml
├── batch-results/F<NN>-result.yaml
├── batch-logs/B<k>-F<NN>.log         (solo hu-frontend-pipeline)
├── merge-report.yaml
├── project-context.md
├── spinal-files.yaml
├── github-project-config.yaml        (solo si existe en el repo)
├── pipeline-state.yaml               (solo hu-frontend-pipeline)
└── pipeline-error.yaml               (solo si algo falla)

<repo>/frontend_dashboard/.frontend/  (durable)
├── spinal-files.yaml
├── project-context.md
├── github-project-config.yaml        (opcional)
├── refinements/<id>-{tech,original}.md
├── plans/<id>/{plan-manifest.yaml,tareas/F<NN>-*.md}
└── results/<id>/F<NN>-result.yaml
```
