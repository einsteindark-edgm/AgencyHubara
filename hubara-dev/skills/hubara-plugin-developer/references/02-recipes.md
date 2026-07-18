# 02 · Recetas (índice — cada una se ejecuta test-first)

> Punteros a `ARCHITECTURE_FINAL_fable.md §4`. La receta dice QUÉ archivos
> tocar; el bucle de `00-tdd-law.md` dice EN QUÉ ORDEN (el primer archivo que
> tocás es el test que falla).

| Querés… | Receta | Resumen |
|---|---|---|
| Crear un plugin nuevo (full-stack) | §4.1 | manifest `plugin.yaml` (id==dir) → backend `api/` + workers + eventos → frontend `index.ts` + entities → `plugins:sync` → `render-compose.py` + k8s → **cierre L-18: declarar `depends_on:` funcionales + verificar en el mapa (abajo)** |
| Consumir datos de otro plugin (cast) | §4.2 | `depends_on` + `consumes` + router que reenvía al contrato HTTP del provider bajo `/api/<tu-id>/` |
| Agente conversacional con ruta propia | §4.3 | `owns_route` + `route_workflow_id_template` en tu worker; leé tu ruta de TU manifest |
| Toggle por deployment | §4.4 | editar `ENABLED_PLUGINS` en el artefacto → `render-compose.py` → `up -d --remove-orphans` → re-build frontend |
| Agregar un campo al manifest | §4.5 | las 3 patas: schema + código que lo consume + check de conformidad |
| Extraer/mover código entre plugins | §4.6 | checklist PM-1..PM-13 de `PLUGIN_CONTRACT.md` |
| Drenar un import `src.platform.*` al SDK | §4.7 | superficie por kit + migrar + regenerar ratchet P-28 + las 3 patas (ej. dashboardkit) |
| Integrar un plugin con GraphAgents (cross-sistema) | §abajo | adaptador HTTP por execution-id + **declarar la costura en `vscode-hubara/seams.yaml`** |

## El atajo

Antes de seguir cualquier receta: nombrá el test que falla y exige el primer
incremento (00-tdd-law.md). Una receta sin test-first es copy-paste sin red.

Para un plugin NUEVO, además existe el scaffolder del SDK que **nace C2**:
`cd hubara_agency && uv run python -m src.sdk.cli create plugin <id> --archetype <a>`
(genera manifest + api delgada + dominio puro + el archivo TCK). Ver
`05-sdk-surface.md`.

## Cierre de un plugin nuevo: depends_on + el dibujo en Acktos Studio (L-18)

El edge plugin→plugin del System Map sale EXCLUSIVAMENTE de `depends_on:` del
manifest — y P-6 solo protege en deploy lo que está declarado. Antes de cerrar
un plugin nuevo, dos pasos NO opcionales:

1. **Declarar toda dependencia funcional** en `depends_on:`. Preguntas gatillo:
   ¿escribís en conversaciones de clientes (templates/free-form → sesiones)?
   → `[chats]` (las respuestas y el opt-out los maneja SU ingest; guard
   `tests/platform/test_session_plugins_depend_on_chats.py` lo exige).
   ¿Otro plugin cumple una promesa tuya o es target de tus transitions? →
   declaralo. Comentá el porqué en el manifest (patrón: eta, reengagement,
   marketing).
2. **Verificar el dibujo**: `cd hubara_agency && uv run python -c
   "from src.plugins.system_map.domain.builder import build_system_graph;
   g=build_system_graph(); print([(e.source,e.target,e.kind) for e in g.edges
   if 'plugin:<id>' in (e.source,e.target)])"` — tu plugin debe tener edges
   HACIA AFUERA (no solo `belongs_to` internos). Una isla en el mapa =
   integración invisible para el operador (clase L-17/L-18).

## Integración cross-sistema (plugin ↔ GraphAgents)

El contacto entre las dos arquitecturas es SIEMPRE un adaptador HTTP en tu
plugin que habla con el runtime de AgentSpan por `execution-id` (caso vivo:
`src/plugins/ads/runs/conductor.py` → pod `agent:ads-analytics`). NUNCA un
import cruzado.

Al cerrar la integración, **declarás la costura en `vscode-hubara/seams.yaml`**
(raíz del monorepo). No se auto-detecta: Acktos Studio dibuja el workspace con
las costuras de ese archivo — sin la entrada, la conexión es invisible en el
mapa (y en la vista colapsada, donde cada costura aparece como sub-caja del
sistema conectada al subsistema real del otro lado). Formato — ids NAMESPACED
(`hub:` = nodos del system map: `plugin:/api:/worker:…`; `ga:` = nodos del
catálogo GraphAgents: `agent:/tool:…`):

```yaml
seams:
  - id: <nombre-de-la-integracion>
    from: hub:plugin:<tu-plugin>
    to: ga:agent:<agente>
    label: "<qué es (archivo que la implementa)>"   # citar código VIVO, no aspiracional
    kind: launches
```

Una costura cuyo from/to no resuelve contra los grafos actuales se reporta
como "rota" en el canvas — no rompe nada, pero delata drift. Verificá que los
ids existen: el lado hub sale de `GET /api/graph` del system map bridge, el
lado ga del catálogo (`agent:<id>` del manifest).

---
Fuente canónica: `ARCHITECTURE_FINAL_fable.md §4`. Si difiere del código vivo,
gana el código vivo.
