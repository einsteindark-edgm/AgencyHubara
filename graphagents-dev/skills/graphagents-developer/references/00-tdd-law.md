# 00 · La ley TDD de GraphAgents (rojo → verde → refactor)

El método con que se escribe cada línea de producción de `GraphAgents/`. No es
opcional. El panel `/graphagents-gates` hace el "verde" determinístico; TDD
agrega lo único que el panel no impone: **el rojo va primero**.

## Las tres leyes

1. No escribís producción hasta tener un test que **falla** y lo exige.
2. No escribís más test del mínimo para fallar (un `ImportError`/error de
   colección NO es rojo válido — el test todavía no prueba nada).
3. No escribís más producción del mínimo para pasar ese rojo.

Ciclo de minutos, un comportamiento por vuelta.

## Qué es el "rojo" en cada capa

| Capa | Dónde nace el test PRIMERO | El rojo asierta |
|---|---|---|
| **Capability** (`graphs/<x>.py`, un `StateGraph`) | `tests/graphs/test_<x>_golden.py` | **golden-replay**: fixture de datos → output EXACTO del grafo |
| **Nodo puro** (transform/extract dentro del grafo) | `tests/graphs/` (unit del nodo) | función pura: input → output, sin LLM/IO |
| **Tool** (`tools/<x>.py`) | `tests/<...>/test_<x>.py` | el **decision payload** / efecto declarado (no la implementación) |
| **Connector** (`sdk/connectorkit`) | `tests/architecture/` o unit | los 4 paths del port: éxito · error del vendor · timeout · no-disponible |
| **SDK / manifest check** (`sdk/testkit/checks.py`) | `tests/architecture/` | el **caso NEGATIVO primero**: fabricá el manifest roto y probá que el check lo CAZA ("el gate que nunca falla es un gate roto") |
| **CASO de ejecución** (`fixtures/cases/<x>.case.yaml`) | el propio `.case.yaml` + `sdk.cli cases --check` | el target ENTERO **por su manifest** (seed inyectado → golden pineado). Ver §"El caso de ejecución" abajo — **obligatorio al cerrar tool/agente/flujo nuevo** |

## El caso de ejecución (`.case.yaml`) — la definición de HECHO

Ninguna tool, agente o flujo nuevo se declara terminado sin su **caso de
ejecución** en `fixtures/cases/<id>.case.yaml`. El golden unitario prueba la
función; el CASO prueba el **CABLE completo** (manifest + bindings G-BIND +
tools del catálogo + ports fixture) replayeando el target por
`build_runnable` — el mismo camino que corre el viewer y el runtime. Es lo
que L-25 exige, hecho artefacto verificable.

1. `id` + `target:` (`tool:<id>` | `agent:<id>` | `flow:<id>`).
2. `seed:` — lo que el central/hubara deposita (payload grande → `{$ref:
   fixtures/x.json}`, no duplicar). Cubrí las RAMAS del dominio, no solo el
   happy path (ej. window-strategist: las 3 fases del funnel + cadencia +
   truncado de presupuesto en UN seed).
3. `ports:` — solo si el target `consumes` ports (fixture vendors); `llm:`
   = respuesta fija.
4. `golden:` — el output COMPLETO pineado (`{$ref:
   fixtures/cases/<id>.golden.json}`); generalo con `replay_case` UNA vez y
   revisalo a mano antes de pinearlo (un golden mentiroso certifica basura).
5. Verificá: `python3 -m sdk.cli cases --check` (el TCK del catálogo caza
   golden desactualizado / $ref roto / target inexistente / port sin vendor)
   + un test nominal en `tests/architecture/test_cases.py` si el caso guarda
   una composición importante (patrón `dia-del-padre-flujo`).

El caso queda listado en el viewer/Studio (select de casos) — es también el
"botón de probar" del operador. Caso paradigmático de la regla:
`window-strategist-ciclo.case.yaml` (2026-07-07 — el desarrollo se cerró sin
caso y hubo que agregarlo post-facto; no repetir).

## El golden-replay, concreto

1. Conseguí un fixture real (snapshot de insights de Meta) en `fixtures/`.
2. Escribí `test_<x>_golden.py`: construí el grafo, corrélo sobre el fixture,
   asertá el output esperado (un dataclass/dict estable). **Velo fallar** con un
   assert con sentido, no con un import roto.
3. Implementá el mínimo del `StateGraph` para ponerlo verde.
4. Refactor con el golden de red de seguridad.

El golden-replay es lo que hace al grafo **determinista y testeable** pese a
tener un LLM: el esqueleto (ruteo/extracción/transformación) es puro y se
replayea; el nodo LLM se aísla con `temperature=0` + structured output y se
mockea en el golden (o se fija con un fixture de respuesta).

## No es TDD (rechazá esto)

- Escribir el grafo y "después el golden" (test-after) — invierte la presión de
  diseño y normaliza nodos no-testeables.
- Asertar sobre implementación (qué nodo llamó a cuál) en vez de **output
  observable**.
- Over-mock: si para testear un nodo tenés que mockear medio grafo, el nodo hace
  demasiado — arreglá el diseño, no el test.
- Un test que no puede fallar (sin assert real). Si nunca lo viste rojo, no sabés
  si prueba algo.

## Bug de producción ⇒ guard rojo antes del fix

Cuando un run real (sobre AgentSpan) revela un bug, el **primer** artefacto es un
test que lo reproduce y falla. Recién entonces el fix lo pone verde. El "Guard:"
de cada lección `L-#` se escribe ANTES que el "Fix:". Para una carrera de
ejecución durable, reprodúcela con el `execution-id` del run y un fixture del
estado.

## El atajo mental

Antes de tocar producción: *"¿cuál es el golden (o el check) que falla y exige
esto?"* Si no podés nombrarlo, todavía no entendés el incremento.
