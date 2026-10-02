# 06 · Paquetes de decisión (el motor de decisiones como datos)

> Diseño: `PAQUETES_DE_DECISION.md` · ADR: `ADR-2026-10-01-decision-bundles.md`
> · Kit: `docs/_sdk/17-decisionkit.md` · Código: `src/platform/decisions/`
> (motor genérico), `src/plugins/chats/agent/sales/decisions/bundled.py` (los
> builtins de ventas) y `src/plugins/chats/shared/decisions/bundles/` (los
> paquetes: uno por tienda).

## Cuándo aplica

Vas a crear o cambiar una **capacidad del motor de decisiones** (lo que le
pregunta a Jev y cómo convierte la respuesta en decisión: cortesía, baja,
zona de envío…), o a llevar el motor a otra tienda. Desde F1, las capacidades
migradas viven en YAML:

```
src/plugins/chats/shared/decisions/bundles/
  builtins.yaml                catálogo: lo que un paquete puede pedir y el dominio que trae
  ventas/bundle.yaml           id, versión, engine_contract, capacidades
  ventas/domain.yaml           el dominio de la tienda (nombre, despedida, vocabulario del agente)
  ventas/turn.yaml             el turno: la ráfaga ①, ③ y las tablas de la política (F7)
  ventas/capabilities/*.yaml   una capacidad por archivo
```

Desde F4 las 29 capacidades salen del paquete (una prueba lo exige:
`test_the_default_bundle_has_every_capability`). Las clases de
`decisions/capabilities/` y `egress.py` quedan SOLO como oráculo de la
paridad: no se les agrega comportamiento; el comportamiento nuevo va al
YAML (y, si hace falta código, a un builtin).

**Nunca nombres una clase de capacidad ni su instancia global** (`Baja()`,
`PERSONA`): pedila por nombre al resolutor, `capability("baja")` (desde una
tool, vía `guards`). Él elige el paquete activo de la tienda
(`SALES_DECISIONS_BUNDLE`, nace en Terraform `tenants.<t>.lab.decisions_bundle`)
o la clase si todavía no migró. Dos pruebas
(`test_decisions_registry.py`) frenan cualquier instancia o import de clase
fuera del resolutor.

## El bucle (TDD también aplica a los datos)

1. **Rojo:** agregá en el YAML el `examples:` que exige el comportamiento
   nuevo (o el caso del bug). `decisions check` falla con `DB010`.
2. **Verde:** cambiá preguntas / umbrales / filas hasta que pase.
3. **Certificá:** `cd hubara_agency && uv run python -m src.sdk.cli decisions check`.
   El hook `affected-tests` ya lo corre solo cada vez que editás un YAML de
   `decisions/bundles/` y te devuelve 🟢/🔴 con cada error.
4. **Si migrás una clase:** la prueba de paridad
   (`tests/plugins/chats/sales/decisions/test_decisions_bundle_parity.py`)
   compara regla, estado y preguntas byte a byte (también el orden de las
   opciones), la decisión en toda la grilla de respuestas, el piso y el
   veredicto completo. Agregá `nombre: (Clase(), entradas, grilla)` a `CASES`
   ANTES de escribir el YAML (rojo: `bundle '' == 'ventas@1'`).

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
| `opt['q']` de una pregunta sin `options:`; una pregunta condicional que lee `p`/`choice` | DB005 |
| `options:` con un builtin que no es de clase `options`; una `view:` de otra entrada | DB004 |
| `control:` de una capacidad que no está en el paquete | DB003 |
| `turn.yaml`: asunto, tool, etapa, dato, hecho o política que el catálogo (`turn:`) no declara; umbral que la política no lee; el cuestionario no hace una pregunta que la política lee | DB015 |
| `turn.yaml`: `{campo}` de una plantilla del cuestionario que el motor no llena | DB005 |
| `turn.yaml`: fila del contrato que nunca se lee (una anterior del mismo asunto no tiene condición) | DB008 |

## Cómo escribir una condición (CEL)

- Variables: `p` (sí/no → probabilidad), `choice` y `conf` (opciones),
  `th` (umbrales), `rule` (lo que dijo la regla), `inp.campo` (entrada
  declarada en el catálogo), `vars.nombre` (cálculos intermedios en orden),
  `consts.NOMBRE` (constantes del catálogo). `let`/`const` están reservadas.
- El valor tiene tipo (`value: "{cantidad: int?}"`, `tuple<string>`…); un
  `then` calculado va como `then: {expr: "{'cantidad': int(choice['cantidad.dio'])}"}`.
- **Antes de leer una respuesta, preguntá si llegó:** `!('baja.pide' in p)` →
  `doubt`. Leer una que no llegó falla al evaluarse y la tabla da duda.
- Llaves siempre literales entre comillas simples: `p['baja.pide']`.
- `then: doubt` = decide la regla. La primera fila que se cumple gana.
- **Opciones que salen de la entrada** (categorías, colores, ítems, títulos):
  `options: {builtin: catalog_categories}` en la pregunta + `criteria` solo
  con las fijas (`ambiguo`, `ninguno`). El builtin devuelve
  `{opción: (etiqueta, valor)}`; la tabla lee el valor:
  `when: "choice['q'] in opt['q']"` → `then: {expr: "opt['q'][choice['q']]"}`.
- **Pregunta que solo a veces se hace:** `when: "inp.context && inp.asked_known == null"`
  (lee `inp` y `consts`). Los campos derivados (la ventana, lo que el texto
  enumera) los da una `view:`.
- **Variante** (la misma decisión preguntada de otra forma, mismo
  interruptor): `control: <capacidad>`.
- **La misma pregunta por cada oración / párrafo / término / dato:**
  `items: {builtin: items_of, with: {field: parts}}` + `each:` con
  plantillas (`id: "persona.{n}"`, `text: "¿La oración [{n}] …?"`, `{campo}`
  del ítem) y `when: "item.ask"` si solo algunos se preguntan. La tabla lee
  `items` con `filter`/`map`/`exists`/`all`/`join`; un ítem sin respuesta no
  trae `p`: `has(i.p)`. Los ejemplos llevan `items: [{text: …, p: 0.9}, …]`.
- Si necesitás iterar, sumar o leer el catálogo: eso es un **builtin** nuevo
  (función + entrada en `builtins.yaml` + prueba), no una condición larga.
  Si arrastra `use_cases/`, el ingest o el catálogo, va en
  `bundled_ingest.py` / `bundled_catalog.py` / `bundled_egress.py` y se
  registra en `LAZY_BUILTINS` (las tools no pueden importar Temporal).
  `test_the_catalog_and_the_code_declare_the_same_builtins` exige que
  catálogo y código coincidan.

## Ejemplos de la tienda en el agente (F5)

Un ejemplo que el LLM lee y que es de ESTA tienda («'lavanda', 'el
morado'», un producto, una frase de gancho) NO va en el código de una tool
ni de un prompt: va en `domain.yaml: vocabulary` (declarado en
`builtins.yaml: domain`) y el código lo lee con
`chats/shared/store_pack.py: vocabulary()`. `test_store_domain.py` frena un
ejemplo de la tienda que vuelva al código y exige que, para la tienda
actual, el texto que ve el LLM sea idéntico a la foto congelada (si lo
cambias a propósito, regenera la foto en el mismo cambio).

## El turno (F7)

Lo que Jev contesta antes del turno (la ráfaga ①) y antes de enviar (③),
y las tablas con que la política `turno-v3` arma el turno, viven en
`ventas/turn.yaml` (el perfil `jev-v5` dice `turn: bundle`). Para cambiar
qué tool pide un asunto, una excepción del contrato, una palabra de ②, una
nota de la lectura o la banda de ③: **se edita el YAML (en otra versión del
paquete), no la política**.

- **Contrato** (`contract:`): filas `{topic, when?, any_of, nudge}`; la
  primera del asunto que se cumple decide; `any_of: []` = no pide tool. CEL
  sobre `p` (una respuesta sin probabilidad vale 0), `th`, `inp.stage`, `dom`.
  Antes de leer una respuesta: `'envio.costo' in p && …`.
- **③** (`verify_decide:`): por asunto, `item.topic`/`item.msg`/`item.p` →
  `covered | missing | doubt`; `!('p' in item)` = Jev no contestó.
- **Ejemplos** (`examples.contract` / `examples.verify`): uno por excepción
  del contrato y por banda, en los bordes.
- **Una pregunta nueva de la ráfaga** va en `questionnaire.questions` (con
  `when` sobre los hechos del catálogo); si la va a leer el CÓDIGO de la
  política, declárala en `builtins.yaml: turn.policies.<política>.reads`.
- La paridad con las constantes de la política (jev-v1…v4 siguen con ellas)
  está en `test_decisions_turn_bundle.py`: si cambias `ventas` a propósito,
  esa prueba falla (y debe: es otra versión).

## Otro plugin con su paquete (F8)

El Order Sentinel tiene el suyo: `order_sentinel/agent/decisions/`
(`bundles/builtins.yaml` = su catálogo, `bundles/centinela/` = el paquete,
`builtins.py` + `BUILTINS` = el código que el catálogo nombra, una prueba
exige que coincidan). Para un plugin nuevo, la misma forma: catálogo propio
junto a sus paquetes (`**/decisions/bundles/<id>/bundle.yaml`: el
certificador los descubre), `load_bundle` desde `src.sdk.decisionkit`, y la
paridad contra una foto del comportamiento de antes si reemplaza código vivo.

## Probar un paquete nuevo (F6)

1. Copiá la carpeta de la tienda a una nueva (`bundles/ventas-2/`), cambiá
   `id` (= la carpeta) y `version`, y hacé el cambio de comportamiento.
2. `decisions check` en verde y commit (la caja del laboratorio corre la
   imagen del commit).
3. En el laboratorio, «Nueva corrida» → «Paquete a comparar: ventas-2»: corre
   `B@ventas-2` junto a B y el resumen muestra la diferencia `B → B@ventas-2`.
4. Si gana, se promueve en Terraform (`tenants.<t>.lab.decisions_bundle`).

## Lo que NO se hace

- Editar una versión publicada: otra pregunta u otro umbral = `version: N+1`.
- Escribir un piso en el YAML: los pisos son builtins (código) y los
  obligatorios (`required_floors`) no se pueden cambiar.
- Importar `src.platform.decisions` desde un plugin: se usa `src.sdk.decisionkit` (P-28).
- Cargar paquetes en código de workflow (R-DET): solo en ingest, tools y activities.
