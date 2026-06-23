# 05 · El protocolo `Capability` y el tracing por-tool (mejores prácticas)

> Cómo una capability se implementa SIEMPRE igual (el protocolo del kit), por qué eso la hace
> trazable sin tocar su código, y cómo el viewer muestra el I/O real de cada tool **reconstruyéndolo**
> en vez de persistirlo. Es el patrón a copiar para cualquier preocupación transversal futura
> (métricas, costo, gates) — todas cuelgan del mismo seam.

## La idea en una página

- **El contrato (`sdk/capability.py`).** Una capability = un nodo determinista de UN agente, con
  dos métodos:
  - `run(input, *, ports, tools) -> output` — **PURA, obligatoria** (G-DET, G-RUN-SIG). El IO sale
    por `ports`; las tools se llaman por el **mapping `tools` INYECTADO**, nunca por import directo.
  - `build(*, checkpointer=None) -> CompiledStateGraph` — **opcional** (G1+), el mismo comportamiento
    como StateGraph para correr standalone en AgentSpan. Debe ser equivalente a `run()` en su output.
- **El seam.** Ese mapping `tools` es el ÚNICO punto por donde pasan las llamadas a tools. Si lo
  **envolvés** (`sdk/tooltrace.traced`), registrás cada llamada (orden + input + output) sin que la
  capability se entere. Por eso TODA capability que cumple el protocolo es trazable gratis.
- **Reconstruir, no persistir.** No guardamos el trace en el runtime durable (Conductor registra 1
  task por sub-agente; las tools corren adentro, opacas). Como el `run()` es PURO y DETERMINISTA
  (G-DET), **lo reconstruimos**: replayeamos el `run()` del nodo con tools trazadas sobre su input
  registrado. Es fiel porque el supervisor durable corre el MISMO `run()` (vía `build_runnable`) — y
  un standalone `build()` lo garantiza la paridad run≡build (golden). Cero cambios a la ejecución,
  al `acc` o a los goldens: observabilidad puramente aditiva.
- **La cert lo amarra.** `G-PROTO` (estructural, en `run_checks`) + el conformance behavioral
  (replay del pod → tools realmente invocadas == declaradas) hacen que la reconstrucción sea
  CONFIABLE para cualquier capability. El manifest no puede mentir: si llama una tool no declarada
  o en otro orden, rojo.

## El kit de tracing (`sdk/tooltrace.py`)

| Pieza | Qué hace |
|---|---|
| `ToolLedger` | registro ordenado `[{seq, tool, input, output}]` — `seq` ordinal, SIN timestamps (determinista, golden-able). |
| `traced(tools, ledger)` | envuelve el mapping para registrar cada llamada; devuelve el output REAL intacto. |
| `replay_with_trace(node, ga_root, input)` | reconstruye el I/O por-tool de UNA capability. |
| `replay_flow_with_trace(supervisor, ga_root, seed)` | reconstruye TODO el pod desde su seed (threadea el acc igual que `build_runnable`); lo usa el viewer (`/api/flow-trace`) y el conformance. |

## Reglas duras (qué NO hacer)

1. **Una capability NUNCA importa la impl de una tool directamente en `run()`.** Se llama por
   `tools["<id>"]` (el mapping inyectado). Importar directo rompe el tracing Y el binding G-BIND.
   - ⚠️ Hoy los `build()` SÍ importan las impls directo (`from tools.X.impl import run`). Es la
     fuente del riesgo de drift run↔build (L-24). El supervisor durable NO usa `build()` de sus
     miembros (usa `build_runnable`→`run()`), así que el trace reconstruido es fiel igual; pero al
     escribir un `build()` nuevo, mantené el MISMO orden de tools que el `run()` (lo prueba el golden
     del `build()`).
2. **No persistas el trace en el `acc`.** Reconstruir es más barato y no toca goldens/determinismo.
   Si algún día hace falta el I/O en vivo de un run NO determinista, ahí sí instrumentás el output —
   pero entonces el trace debe ser determinista o excluirse del golden.
3. **El trace por-tool es la EJECUCIÓN real, no la composición declarada.** Un nodo puede llamar una
   tool en LOOP (ej. `blended-economics` corre `blended-unit-economics` por-día): el trace lo repite,
   el manifest la declara una vez. El viewer muestra ambas verdades — el panel la composición
   declarada, el modal la ejecución reconstruida (más rica). No las confundas.
4. **Degradá honesto.** Sin seed recuperable, `/api/flow-trace` devuelve `reconstructed: false` y el
   modal cae al listado declarado — NUNCA inventa I/O.

## Cómo extender el patrón (la razón del protocolo)

¿Una preocupación transversal nueva (medir costo/tokens, contar llamadas, gate de approval por-tool)?
Cuelga del MISMO seam:
1. Escribí un wrapper del mapping `tools` (como `traced`) que haga lo tuyo por-llamada.
2. Si necesitás el resultado fuera del proceso, reconstruí (replay determinista) en vez de persistir.
3. Amarralo con un conformance (replay del pod → tu invariante) y, si emitís un código de cert,
   glosalo (`sdk/glossary.py`) — el guard `test_glossary` lo exige.

El protocolo `Capability` es lo que hace esto posible: como TODAS las capabilities exponen la misma
superficie y rutean por el mismo mapping, un solo wrapper las cubre a todas.

## Verificación

- `tests/architecture/test_capability_protocol.py` — el contrato estructural (`assert_capability`).
- `tests/architecture/test_tooltrace.py` — el kit (ledger, traced, replay).
- `tests/architecture/test_capability_conformance.py` — **el amarre**: replay del pod real → tools
  invocadas == declaradas (+ el loop revelado) + G-PROTO en `run_checks`.
- `tests/integration/test_viewer_api.py::test_api_flow_trace_*` — el endpoint de reconstrucción.
- En vivo: `durable ▸` un caso, abrí un nodo, expandí una tool → su `entró`/`salió` real.
