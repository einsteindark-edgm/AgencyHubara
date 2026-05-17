# hubara-architecture-guide — README para humanos

> **Audiencia:** dev que mantiene este skill (no es contenido cargado por
> agentes; Claude lee `SKILL.md` y `sections/`, no este README).

## Qué es esto

Es un **skill modular de conocimiento arquitectural** consumido por los
skills del pipeline Archon Hubara (`hubara-tech-refiner-archon`,
`hubara-plugin-planner-archon`, `hubara-feature-planner-archon`,
`hubara-implementer-archon`, `hubara-merger-archon`).

A diferencia de los skills tradicionales que tienen UN `SKILL.md`
monolítico, este skill se divide en:

```
SKILL.md                        # entrypoint + nav
sections/01..10-*.md            # las 10 secciones temáticas (~8-15 KB c/u)
references/*.md                 # 4 references densos (manifest schema, R-rules, FSD rules, Temporal patterns)
examples/*.md                   # 4 ejemplos trabajados (1 por template de plugin)
README.md                       # ESTE archivo
```

**Total:** ~170 KB. Cada skill downstream carga solo 1-3 secciones por
task, no las 10.

## Por qué existe

El plan completo está en `HUBARA_PIPELINE_PLAN.md §3` (raíz del repo).
Resumen: queríamos un único repositorio de conocimiento arquitectural
que (a) no se duplicara en cada skill, (b) se cargara modular, y (c)
fuera fácil de mantener cuando el repo cambia.

## Cuándo editar este skill

| Cambio en el repo | Editar |
|---|---|
| Plugin nuevo creado / borrado | `sections/01-general.md §3 contadores` + `examples/` si vale agregar como referencia |
| Cambio al schema `plugin.yaml` | `references/manifest-schema.md` + `sections/07-shared-files.md` si cambia spinal |
| Regla DEHA nueva o relajada | `references/deha-rules.md` + `SKILL.md §4` + `sections/08-tests-and-gates.md` |
| Nueva tool / activity / workflow pattern | `sections/04-backend-agents.md` + posible entry en `sections/10-cookbook.md` |
| Nueva sección FSD (e.g. nueva entity) | `sections/05-frontend-fsd.md §2` (los listados) |
| Cambio en arquitectura global | `sections/01-general.md` + `SKILL.md §3 mapa` |

**Regla práctica:** si editás `ARCHITECTURE.md` (la fuente de verdad
humana), generalmente tenés que actualizar 1-2 secciones de este skill.
El equivalente al revés es raro (este skill NO es source of truth, es
formato-agente del ARCHITECTURE.md).

## Convención de splits

Si una sección crece >15 KB, splittearla en sub-archivos del mismo
prefijo numérico:

```
sections/04-backend-agents.md           # entrypoint, ≤8 KB
sections/04a-backend-agents-workflows.md
sections/04b-backend-agents-activities.md
sections/04c-backend-agents-tools.md
```

El skill que la lee carga `04-backend-agents.md` y desde ahí salta a los
sub-archivos si necesita más detalle (nav explícito en el archivo
principal).

## Testing del skill

```bash
# 1. Cobertura de contenido
grep -r "TODO" .claude/skills/hubara-architecture-guide/
# → debe ser 0 matches

# 2. Paths citados existen en el repo
# (lista curada de paths citados — extraerla con grep)
grep -rEho "[a-zA-Z_-]+/(src|tests|scripts|plugins|k8s|app|pages|features|entities|shared)/[a-zA-Z0-9_/-]+\.(py|ts|tsx|yaml|yml|md|css)" \
  .claude/skills/hubara-architecture-guide/ \
  | sort -u | xargs -I{} ls -1 {} 2>&1 | grep "No such file"
# → debe ser empty

# 3. Snippets Python parsean
grep -A 100 '```python' .claude/skills/hubara-architecture-guide/sections/*.md \
  | python3 -c "import sys,ast; [ast.parse(b) for b in sys.stdin.read().split('```')]"
# (script más serio: extraer cada bloque y parsearlo individualmente)

# 4. Test de uso real (manual)
# Tomar HU dummy y leer las secciones que cargaría un implementer.
# ¿Las secciones cubren todo lo necesario para implementar?
```

## Versiones

- **v1.0** (2026-05-17) — initial release, PR12 del scope hubara
  pipeline. Cubre arquitectura post-PR11.
