# 02 · Recetas (índice — cada una se ejecuta test-first)

> Punteros a `ARCHITECTURE_FINAL_fable.md §4`. La receta dice QUÉ archivos
> tocar; el bucle de `00-tdd-law.md` dice EN QUÉ ORDEN (el primer archivo que
> tocás es el test que falla).

| Querés… | Receta | Resumen |
|---|---|---|
| Crear un plugin nuevo (full-stack) | §4.1 | manifest `plugin.yaml` (id==dir) → backend `api/` + workers + eventos → frontend `index.ts` + entities → `plugins:sync` → `render-compose.py` + k8s |
| Consumir datos de otro plugin (cast) | §4.2 | `depends_on` + `consumes` + router que reenvía al contrato HTTP del provider bajo `/api/<tu-id>/` |
| Agente conversacional con ruta propia | §4.3 | `owns_route` + `route_workflow_id_template` en tu worker; leé tu ruta de TU manifest |
| Toggle por deployment | §4.4 | editar `ENABLED_PLUGINS` en el artefacto → `render-compose.py` → `up -d --remove-orphans` → re-build frontend |
| Agregar un campo al manifest | §4.5 | las 3 patas: schema + código que lo consume + check de conformidad |
| Extraer/mover código entre plugins | §4.6 | checklist PM-1..PM-13 de `PLUGIN_CONTRACT.md` |
| Drenar un import `src.platform.*` al SDK | §4.7 | superficie por kit + migrar + regenerar ratchet P-28 + las 3 patas (ej. dashboardkit) |

## El atajo

Antes de seguir cualquier receta: nombrá el test que falla y exige el primer
incremento (00-tdd-law.md). Una receta sin test-first es copy-paste sin red.

Para un plugin NUEVO, además existe el scaffolder del SDK que **nace C2**:
`cd hubara_agency && uv run python -m src.sdk.cli create plugin <id> --archetype <a>`
(genera manifest + api delgada + dominio puro + el archivo TCK). Ver
`05-sdk-surface.md`.

---
Fuente canónica: `ARCHITECTURE_FINAL_fable.md §4`. Si difiere del código vivo,
gana el código vivo.
