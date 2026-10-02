# 06 · Paquetes de decisión (el motor de decisiones como datos)

> Diseño: `PAQUETES_DE_DECISION.md` · ADR: `ADR-2026-10-01-decision-bundles.md`
> · Kit: `docs/_sdk/17-decisionkit.md` · Código: `src/platform/decisions/`
> (motor genérico) y `src/plugins/chats/agent/sales/decisions/bundled.py` +
> `decisions/bundles/` (ventas).

## Cuándo aplica

Vas a crear o cambiar una **capacidad del motor de decisiones** (lo que le
pregunta a Jev y cómo convierte la respuesta en decisión: cortesía, baja,
zona de envío…), o a llevar el motor a otra tienda. Desde F1, las capacidades
migradas viven en YAML:

```
src/plugins/chats/agent/sales/decisions/bundles/
  builtins.yaml                     catálogo: lo que un paquete puede pedir por nombre
  hubara-ventas/bundle.yaml         id, versión, engine_contract, capacidades
  hubara-ventas/capabilities/*.yaml una capacidad por archivo
```

Migradas hoy: `baja`, `cortesia`. Las demás siguen siendo clases en
`decisions/capabilities/` hasta su fase (F2–F4, ver la clasificación A/B/C en
el diseño §7).

## El bucle (TDD también aplica a los datos)

1. **Rojo:** agregá en el YAML el `examples:` que exige el comportamiento
   nuevo (o el caso del bug). `decisions check` falla con `DB010`.
2. **Verde:** cambiá preguntas / umbrales / filas hasta que pase.
3. **Certificá:** `cd hubara_agency && uv run python -m src.sdk.cli decisions check`.
   El hook `affected-tests` ya lo corre solo cada vez que editás un YAML de
   `decisions/bundles/` y te devuelve 🟢/🔴 con cada error.
4. **Si migrás una clase:** la prueba de paridad
   (`tests/plugins/chats/sales/decisions/test_decisions_bundle_parity.py`)
   compara regla, estado y preguntas byte a byte, la decisión en los bordes de
   cada umbral, el piso y el veredicto completo. Agregá el par
   `(nombre, Clase(), pregunta)` a `PAIRS` ANTES de cambiar el consumidor.

## Reglas duras (lo que el certificador te frena)

| Error típico de un agente | Código |
|---|---|
| Llave que no existe en el esquema (`threshold:` en vez de `thresholds:`) | DB001 |
| Builtin inventado o de otra clase (`rule: {builtin: jev}`) | DB004 |
| `p['baja.pid']` (pregunta mal escrita), `th['si']` (umbral no declarado), `p[qid]` (llave calculada) | DB005 |
| Condición que no compila o no da bool (`p['x'] >= 'alto'`, `p['x']` solo) | DB006 |
| `then:` que no es del tipo de la capacidad | DB007 |
| Falta `otherwise` al final | DB008 |
| Umbral fuera de [0, 1] (`85` en vez de `0.85`) | DB009 |
| Ejemplo que no da lo esperado | DB010 |
| `p[...]` sobre una pregunta choice (o `choice[...]` sobre una noul) | DB011 |
| Cambiar un piso obligatorio (la baja legal) | DB012 |

## Cómo escribir una condición (CEL)

- Variables: `p` (sí/no → probabilidad), `choice` y `conf` (opciones),
  `th` (umbrales), `rule` (lo que dijo la regla).
- **Antes de leer una respuesta, preguntá si llegó:** `!('baja.pide' in p)` →
  `doubt`. Leer una que no llegó falla al evaluarse y la tabla da duda.
- Llaves siempre literales entre comillas simples: `p['baja.pide']`.
- `then: doubt` = decide la regla. La primera fila que se cumple gana.
- Si necesitás iterar, sumar o leer el catálogo: eso es un **builtin** nuevo
  (función en `bundled.py: BUILTINS` + entrada en `builtins.yaml` + prueba),
  no una condición larga. `test_the_catalog_and_the_code_declare_the_same_builtins`
  exige que catálogo y código coincidan.

## Lo que NO se hace

- Editar una versión publicada: otra pregunta u otro umbral = `version: N+1`.
- Escribir un piso en el YAML: los pisos son builtins (código) y los
  obligatorios (`required_floors`) no se pueden cambiar.
- Importar `src.platform.decisions` desde un plugin: se usa `src.sdk.decisionkit` (P-28).
- Cargar paquetes en código de workflow (R-DET): solo en ingest, tools y activities.
