---
description: Archive command del pipeline hubara — Fase 12 OpenSpec integration. Corre DESPUÉS de trigger-pr (PR ya abierto). Mueve los artefactos de la HU (hu-refinada.md + spec-deltas/ + plan + premortem + evaluation + code-review-findings) a hubara_agency/.hubara/archive/<YYYY-MM-DD>-<HU_ID>/ tracked en git, y mergea spec-deltas/<capability>/ a hubara_agency/.hubara/specs/<capability>/spec.md aplicando ADDED/MODIFIED/REMOVED. Emite $ARTIFACTS_DIR/archive-result.yaml con resumen. NO toca código de producción. NO opera si el PR no fue creado exitosamente. Triggers — invocación via Archon workflow skills field (nodo archive-hu); NO usar como user-facing slash command.
argument-hint: (none — reads from $ARTIFACTS_DIR + git state)
---

# hubara-archive-hu — Archive de HU + merge de spec deltas

Sos un archivero determinístico. La HU acaba de mergear su PR. Tu
trabajo es **preservar la memoria institucional** (snapshot de todos los
artefactos en un directorio tracked) Y **aplicar los deltas de specs**
a las capability specs persistentes (`hubara_agency/.hubara/specs/`).

NO escribís código de producción. NO modificás artefactos del pipeline
(refinements, plans, etc.) — solo los movés. NO hacés commits (el
orquestador maneja git).

---

## §0. Invocation contract

Operás dentro de un workflow Archon con estas garantías:

- El nodo `trigger-pr` corrió exitosamente. PR está abierto (o ya
  mergeado, depende del modo del workflow).
- Tenés acceso a:
  - `$ARTIFACTS_DIR/hu-refinada.md` — refinement final + §16 con índice de deltas
  - `$ARTIFACTS_DIR/spec-deltas/<capability>/spec.md` — los deltas a aplicar (puede no existir si HU fue refactor puro)
  - `$ARTIFACTS_DIR/feature-plan-manifest.yaml` o `plugin-manifest.yaml`
  - `$ARTIFACTS_DIR/task-result.yaml`
  - `$ARTIFACTS_DIR/premortem.yaml`
  - `$ARTIFACTS_DIR/evaluation.yaml`
  - `$ARTIFACTS_DIR/code-review-findings.yaml`
  - `$ARTIFACTS_DIR/handoff.yaml` (opcional, si hubo)
- Worktree: el del orquestador, branch `hu/<HU_ID>` o `main` post-merge.
- HU_ID detectable desde:
  - `$ARTIFACTS_DIR/hu-refinada.md` header (`HU id: <id>`)
  - O branch name (`hu/<HU_ID>`)
  - O env `$HU_ID`
- Output:
  - `hubara_agency/.hubara/archive/<YYYY-MM-DD>-<HU_ID>/<archivos copiados>`
  - `hubara_agency/.hubara/specs/<capability>/spec.md` (modificados in-place con deltas aplicados)
  - `$ARTIFACTS_DIR/archive-result.yaml`

---

## §1. Step 0 — Cargar contexto (OBLIGATORIO)

```bash
# Detectar HU_ID
HU_ID="${HU_ID:-}"
if [[ -z "$HU_ID" ]]; then
  HU_ID=$(grep -m1 "^- HU id:" "$ARTIFACTS_DIR/hu-refinada.md" 2>/dev/null \
    | sed 's/.*HU id:[[:space:]]*//' | tr -d '"`' | head -c 80)
fi
if [[ -z "$HU_ID" ]] || [[ "$HU_ID" == "(provisional"* ]]; then
  HU_ID=$(git rev-parse --abbrev-ref HEAD 2>/dev/null | sed 's|^hu/||' | head -c 80)
fi

# F-OS-2 fix: sanear HU_ID (solo alfanuméricos + guiones + underscores)
HU_ID=$(echo "$HU_ID" | tr -cd '[:alnum:]_-' | head -c 80)
echo "HU_ID resolved: $HU_ID"

# Si quedó vacío después de sanear, abortar
if [[ -z "$HU_ID" ]]; then
  cat > "$ARTIFACTS_DIR/archive-result.yaml" <<EOF
hu_id: ""
status: skipped
reason: hu_id_unresolvable
archive_run_at: "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
EOF
  echo "Archive — HU_ID unresolvable, skipped"
  exit 0
fi

# Fecha YYYY-MM-DD UTC
TODAY=$(date -u +"%Y-%m-%d")
ARCHIVE_DIR="hubara_agency/.hubara/archive/${TODAY}-${HU_ID}"
echo "Archive dir: $ARCHIVE_DIR"

# F-OS-23 fix: idempotency check — si ya existe el archive dir, skipear
if [[ -d "$ARCHIVE_DIR" ]] && [[ -f "$ARCHIVE_DIR/hu-refinada.md" ]]; then
  cat > "$ARTIFACTS_DIR/archive-result.yaml" <<EOF
hu_id: "$HU_ID"
status: skipped
reason: already_archived
archive_dir: "$ARCHIVE_DIR"
archive_run_at: "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
note: "This HU was already archived. Subsequent runs are no-ops to avoid duplicate merge of spec-deltas."
EOF
  echo "Archive — HU $HU_ID already archived at $ARCHIVE_DIR, skipped"
  exit 0
fi
```

Si HU_ID no se puede resolver (ej. trabajamos en `main` directo sin
branch HU) → abortar con `archive-result.yaml` con `status: skipped`
y razón `hu_id_unresolvable`.

Si el archive dir ya existe Y contiene `hu-refinada.md` → skipear con
`reason: already_archived`. Idempotency garantiza que pipeline restart
NO duplique spec merges.

---

## §2. Step 1 — Cargar índice de spec-deltas

```bash
DELTAS_DIR="$ARTIFACTS_DIR/spec-deltas"
if [[ -d "$DELTAS_DIR" ]]; then
  CAPABILITIES_AFFECTED=$(find "$DELTAS_DIR" -name "spec.md" -type f | sed "s|^$DELTAS_DIR/||" | sed 's|/spec.md$||')
  echo "Capabilities con deltas:"
  echo "$CAPABILITIES_AFFECTED"
else
  echo "No spec-deltas/ — HU sin cambios de comportamiento observable."
  CAPABILITIES_AFFECTED=""
fi
```

---

## §3. Step 2 — Crear archive dir + copiar artefactos

```bash
mkdir -p "$ARCHIVE_DIR"

# Copiar artefactos canónicos (todos opcionales — copiar lo que exista)
for artifact in \
  hu-refinada.md \
  hu-original.md \
  plugin-manifest.yaml \
  feature-plan-manifest.yaml \
  task-result.yaml \
  premortem.yaml \
  evaluation.yaml \
  code-review-findings.yaml \
  review-findings-deha.yaml \
  review-findings-fsd.yaml \
  review-findings-plugin-system.yaml \
  review-findings-test-coverage.yaml \
  review-findings-security.yaml \
  handoff.yaml \
  merge-report.yaml \
; do
  if [[ -f "$ARTIFACTS_DIR/$artifact" ]]; then
    cp "$ARTIFACTS_DIR/$artifact" "$ARCHIVE_DIR/$artifact"
  fi
done

# Copiar spec-deltas completos (preservando estructura)
if [[ -n "$CAPABILITIES_AFFECTED" ]]; then
  cp -R "$ARTIFACTS_DIR/spec-deltas" "$ARCHIVE_DIR/spec-deltas"
fi

# Escribir un README en el archive
cat > "$ARCHIVE_DIR/README.md" <<EOF
# Archive: $HU_ID — $TODAY

> Snapshot de la HU $HU_ID al cierre del PR.

## Artefactos preservados

$(ls "$ARCHIVE_DIR" | grep -v "^README" | sed 's|^|- |')

## Capabilities con behavior changes

$(if [[ -n "$CAPABILITIES_AFFECTED" ]]; then echo "$CAPABILITIES_AFFECTED" | sed 's|^|- |'; else echo "(none — refactor interno)"; fi)

## Cómo leer este archive

- \`hu-refinada.md\` — qué pidió el operador / qué decidimos hacer (14 secciones canónicas + §0 Plugin Classification + §16 Spec deltas)
- \`spec-deltas/\` — qué Requirements cambiaron por capability (ADDED/MODIFIED/REMOVED). Ya aplicados a \`hubara_agency/.hubara/specs/\` por el archive command.
- \`premortem.yaml\` — los modos de fallo que imaginamos antes del PR (útil para calibrar premortem)
- \`evaluation.yaml\` — score de la rúbrica + verdict (útil para calibrar evaluator)
- \`code-review-findings.yaml\` — findings consolidados de los 5 specialists (útil para calibrar reviewers)
- \`task-result.yaml\` — qué hizo realmente el implementer (LOC, tests, files)

## Para volver a esta HU

Buscar en git log por mensajes que mencionen \`$HU_ID\`. El PR original
queda link-eado en el cuerpo del commit final.
EOF
```

---

## §4. Step 3 — Aplicar spec-deltas a parent specs

Por cada capability en `CAPABILITIES_AFFECTED`, leer el delta y mergear a
`hubara_agency/.hubara/specs/<capability>/spec.md` aplicando las
secciones ADDED / MODIFIED / REMOVED.

### §4.1 Algoritmo de merge (determinístico)

```python
# Pseudocódigo del merge — implementarlo con Read + Edit/Write
for capability in CAPABILITIES_AFFECTED:
    delta_path = f"{ARTIFACTS_DIR}/spec-deltas/{capability}/spec.md"
    parent_path = f"hubara_agency/.hubara/specs/{capability}/spec.md"

    delta = read(delta_path)

    if not exists(parent_path):
        # seed_inline case — el delta es spec inicial completa
        if "## ADDED Requirements" in delta and "## Purpose" in delta:
            # Promote inline seed a parent spec
            content = delta.replace("# Delta for ", "# ")
            content = content.replace("## ADDED Requirements", "## Requirements")
            content = strip_section(content, "## MODIFIED Requirements")
            content = strip_section(content, "## REMOVED Requirements")
            mkdir_p(dirname(parent_path))
            write(parent_path, content)
            status[capability] = "created (seed_inline → parent)"
        else:
            status[capability] = "error: seed_inline malformed (no Purpose section)"
        continue

    parent = read(parent_path)

    # Apply ADDED — append each Requirement to parent's ## Requirements section
    added_reqs = extract_requirements(delta, section="## ADDED Requirements")
    parent = append_requirements(parent, added_reqs)

    # Apply MODIFIED — find Requirement by title, replace whole block
    modified_reqs = extract_requirements(delta, section="## MODIFIED Requirements")
    for req in modified_reqs:
        parent = replace_requirement_by_title(parent, req)

    # Apply REMOVED — delete Requirement by title
    removed_titles = extract_requirement_titles(delta, section="## REMOVED Requirements")
    for title in removed_titles:
        parent = remove_requirement_by_title(parent, title)

    # Footer audit trail
    parent += f"\n\n<!-- Merged from HU {HU_ID} on {TODAY} -->\n"

    write(parent_path, parent)
    status[capability] = f"merged ({len(added_reqs)} added, {len(modified_reqs)} modified, {len(removed_titles)} removed)"
```

### §4.2 Implementación práctica — APPEND-SAFE strategy (V1)

> **F-OS-29 fix (premortem self-review):** el merge "in-place"
> de MODIFIED/REMOVED Requirements es complejo y propenso a corromper
> el parent spec si las heurísticas de matching fallan. V1 usa
> estrategia **append-safe**: TODOS los deltas (ADDED, MODIFIED, REMOVED)
> se appendan a una sección dedicada al final del parent spec con
> marker visible. El humano review del PR resuelve los MODIFIED/REMOVED
> a mano si quiere (cosechando los Requirements duplicados o
> deprecados). Esto evita corromper specs por bugs del merger.

Como Claude, vas a hacer esto con Read + Edit:

1. Para cada capability en `CAPABILITIES_AFFECTED`:
   1. Read `$ARTIFACTS_DIR/spec-deltas/<cap>/spec.md` completo
   2. Si NO existe `hubara_agency/.hubara/specs/<cap>/spec.md`:
      - Si el delta tiene `## Purpose` (es `seed_inline`) → Write a parent con transformaciones:
        - Title: `# <CapabilityName>` (no `# Delta for ...`)
        - Mantener `## Purpose` tal cual
        - `## ADDED Requirements` → renombrar a `## Requirements`
        - Dropear `## MODIFIED Requirements` y `## REMOVED Requirements` (no aplica para seed)
        - Action: `seed_promoted`
      - Si no tiene `## Purpose` → error en archive-result.yaml `errors[]`, skip esa capability
   3. Si SÍ existe parent:
      - Read parent completo
      - Append al final del parent (después de cualquier `<!-- Merged from -->` previo):
        ```markdown

        ---

        ## Updates from HU <HU_ID> (<YYYY-MM-DD>)

        <contenido completo del delta excepto el header `# Delta for ...`>

        <!-- Merged from HU <HU_ID> on <YYYY-MM-DD>. ADDED Requirements
        marcadas como activas. MODIFIED/REMOVED Requirements requieren
        review manual: el humano debe cosechar el bloque correspondiente
        de la sección `## Requirements` original. -->
        ```
      - Write parent
      - Action: `appended`
2. Actualizar `hubara_agency/.hubara/specs/_index.md`:
   - Si una capability nueva fue creada (seed_promoted) → cambiar status `⏳ todo` → `✅ active`
   - Si una capability fue solo appended → no tocar la tabla
   - Append al footer: `<!-- Last update: HU <HU_ID> on <YYYY-MM-DD> -->`

### §4.3 Edge cases

- **Parent spec corrupto / archivo binario**: log error, skip merge para esa capability, marcar en archive-result.yaml `errors[]` con severity HIGH.
- **MODIFIED sin Requirement matching en parent**: NO falla (append-safe strategy). El humano review lo resuelve.
- **REMOVED de Requirement inexistente**: NO falla (idem).
- **Spec-deltas/ vacío o ausente**: archive-result.yaml `spec_merges: []`, todo OK, solo se copiaron artefactos.
- **Merge ya aplicado (idempotency)**: el check del §1 lo evita. Si por alguna razón llegamos acá con archive ya existente, el append duplicaría — verificación adicional: buscar el marker `<!-- Merged from HU <HU_ID>` en el parent spec ANTES de appendear. Si está → skip ese append.

### §4.4 V2 (futuro, post-stress-test)

Si en el corpus de archives acumulados (ver `stress-test-protocol.md`)
el operador ve patterns claros (e.g., "siempre que ADDED y MODIFIED el
mismo título → MODIFIED gana"), podemos automatizar el merge real
(in-place reemplazo) con regex/diff. V1 es manual + append-safe
porque NO tenemos corpus suficiente para confiar en heurísticas.

---

## §5. Step 4 — Limpiar $ARTIFACTS_DIR (opcional)

NO borrar artefactos de `$ARTIFACTS_DIR` — el workflow Archon los limpia
naturalmente al terminar. El archive es una **copia**, no un move.

Si el operador quiere borrarlos manualmente post-archive, está el path
ya copiado en `$ARCHIVE_DIR`.

---

## §6. Step 5 — Emitir archive-result.yaml

```yaml
# $ARTIFACTS_DIR/archive-result.yaml
hu_id: "<HU_ID>"
archive_run_at: "<ISO 8601>"
status: success | partial | skipped | error
archive_dir: "hubara_agency/.hubara/archive/<YYYY-MM-DD>-<HU_ID>"

artifacts_copied:
  - hu-refinada.md
  - task-result.yaml
  - premortem.yaml
  # ... lista exacta

spec_merges:
  - capability: plugins/orders
    parent_path: hubara_agency/.hubara/specs/plugins/orders/spec.md
    action: appended   # appended | seed_promoted | already_merged | error
    requirements_added: 1
    requirements_modified: 0
    requirements_removed: 0
    note: "ADDED Requirement(s) marcadas activas en sección 'Updates from HU X'. Review manual del PR puede cosechar duplicados."

  - capability: agents/sales-worker
    parent_path: hubara_agency/.hubara/specs/agents/sales-worker/spec.md
    action: appended
    requirements_added: 2
    requirements_modified: 1
    requirements_removed: 0
    note: "1 MODIFIED Requirement requiere cosecha manual del bloque previo en sección Requirements original."

warnings: []          # WARN-level (e.g., MODIFIED sin match, REMOVED inexistente)
errors: []            # ERROR-level (e.g., parent corrupto)

next_step: |
  Commitear los cambios al repo:
    git add hubara_agency/.hubara/specs/ hubara_agency/.hubara/archive/
    git commit -m "archive: <HU_ID> — merge spec deltas + snapshot artifacts"
```

---

## §7. Hard rules

- NO modificás código de producción (`hubara_agency/src/`, `frontend_dashboard/src/`).
- NO modificás `.archon/`, `.claude/`, ni convenciones del pipeline.
- NO commits ni push.
- NO borrás `$ARTIFACTS_DIR/` — el orquestador lo maneja.
- Si encontrás conflicto de merge en specs (ADD que sobrescribe Requirement existente con mismo título), marcalo en `warnings[]` y elegí KEEP (parent gana) — el operador resuelve manualmente en próximo PR si es legítimo.

---

## §8. Salida final

Imprimir summary 6 líneas:

```
Archive — HU=<HU_ID> date=<YYYY-MM-DD>
Artifacts copied: <count>
Specs merged: <count> capabilities
Created (seed_inline): <count>
Warnings: <count>
Errors: <count>
Output: $ARTIFACTS_DIR/archive-result.yaml
```

Si `errors > 0`, exit con error (el workflow lo captura como gate fail).
