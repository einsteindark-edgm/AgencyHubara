# 17 · `decisionkit` — paquetes de decisión

> Diseño: [PAQUETES_DE_DECISION.md](../../PAQUETES_DE_DECISION.md) ·
> Decisión: [ADR-2026-10-01-decision-bundles.md](../../ADR-2026-10-01-decision-bundles.md)

## Qué problema soluciona

Las capacidades del motor de decisiones (¿pide la baja?, ¿solo agradece?,
¿qué zona de envío?) eran clases de Python: la pregunta a Jev, los umbrales y
la tabla de decisión vivían en código. Cambiar una pregunta exigía desplegar
y llevar el motor a otra tienda exigía editar código lleno de velas y de
Colombia. `decisionkit` deja esa "inteligencia" como **datos tipados y
versionados** (un paquete por tienda) y la **certifica antes de desplegar**:
si el paquete no compila, no existe.

## Cómo funciona

```
src/plugins/chats/shared/decisions/bundles/
  builtins.yaml            catálogo del motor: lo que un paquete puede pedir y el dominio que trae
  ventas/
    bundle.yaml            id, versión, engine_contract, oráculo, capacidades
    domain.yaml            el dominio de la tienda: nombre, despedida, vocabulario del agente
    turn.yaml              el turno: la ráfaga ①, la verificación ③ y las tablas de la política (F7)
    capabilities/<c>.yaml  regla, estado, preguntas, umbrales, tabla, piso, ejemplos
```

1. **Estructura:** modelos Pydantic estrictos (`extra="forbid"`): una llave
   que no existe o un tipo equivocado rechazan el archivo.
2. **Condiciones:** cada fila `when:` es CEL compilado por
   `cel-expr-python` (oficial de Google) contra un entorno tipado —
   `p: map<string,double>` (sí/no), `choice: map<string,string>` y
   `conf: map<string,double>` (opciones), `th: map<string,double>`
   (umbrales), `rule` (lo que dijo la regla). Va detrás de `ExpressionPort`:
   se puede cambiar de implementación sin tocar los paquetes.
3. **Certificador:** referencias cruzadas que CEL no ve (llaves de mapa,
   builtins del catálogo, pisos obligatorios) y los ejemplos del paquete.
   Códigos DB001–DB015 (lista en `src/platform/decisions/checker.py`; los
   del turno, en `turn_check.py`).
4. **Tabla:** la primera fila cuya condición se cumple decide; `doubt` =
   decide la regla. Una condición que falla al evaluarse también es duda
   (leer una llave que no está devuelve un *valor de error* en CEL; el motor
   lo trata como duda, nunca como false: la fila siguiente no decide).
5. **Tipos de valor** (`value:`): `bool`, `string`, `int`, `double`, `any`,
   `list<T>`, `tuple<T>` (tupla de Python), tuplas posicionales
   `(string, tuple<string>)`, registros `{campo: tipo}`, `?` para null y
   uniones `A | B` — p. ej. `{cantidad: int?}`,
   `{deferral: {kind: string, until_ms: int}?, courtesy: bool}` o
   `() | (string, tuple<string>)`. Un `then`
   literal tiene que ser del tipo (DB007); `then: {expr: "<CEL>"}` lo calcula
   (`{'cantidad': int(choice['cantidad.dio'])}`) y se convierte al tipo.
6. **Variables legibles en CEL:** `p`, `choice`, `conf`, `th`, `rule`,
   `inp.campo` (los campos de la entrada declarados en el catálogo y los de
   la vista), `vars.nombre` (cálculos intermedios de la capacidad, en
   orden), `consts.NOMBRE` (constantes del catálogo, con su valor) y
   `opt['<pregunta>'][<opción>]` (el valor de una opción armada desde la
   entrada). `let` y `const` son palabras reservadas de CEL: por eso `vars`
   y `consts`.
7. **Lo que depende de la entrada:** `options:` en una pregunta choice (un
   builtin arma las opciones: etiqueta para Jev, valor para la tabla; las
   fijas de `criteria`, como `ambiguo`/`ninguno`, van al final), `when:` en
   una pregunta (se hace solo si la condición sobre `inp`/`consts` se
   cumple), `view:` en la capacidad (campos derivados de la entrada) y
   `control:` (una variante comparte el interruptor de otra capacidad).
8. **Ítem por ítem** (oraciones, párrafos, términos, datos): `items:` (un
   builtin arma la lista con los campos que declara el catálogo) y `each:`
   (la pregunta de cada ítem: `id` y `text` son plantillas con `{n}`,
   `{index}` y los campos; `when` elige qué ítems se preguntan; el texto
   puede tener variantes `- {when: …, text: …}` / `- {otherwise: …}`). La
   tabla lee `items` —cada ítem con sus campos, su posición y, si Jev
   contestó, `p` o `choice`/`conf`— con `filter`, `map`, `exists`, `all` y
   `join`:

   ```yaml
   decide:
     - when: "!items.exists(i, has(i.p))"
       then: doubt
     - otherwise: {expr: "items.filter(i, has(i.p) && i.p >= th['yes']).map(i, i.text)"}
   ```
9. **El dominio de la tienda** (`domain.yaml`): lo que es de UNA tienda y
   no de la inteligencia. El catálogo lo declara (`domain:` campo → tipo,
   con secciones); el certificador exige que el paquete lo traiga completo,
   del tipo y sin campos de más (DB014). Las condiciones lo leen como
   `dom.campo`; el código, con `load_domain(carpeta, catálogo)` (en ventas:
   `chats/shared/store_pack.py: store_domain()` / `vocabulary()`).
10. **Builtins:** lo que sigue siendo código (reglas de texto, constructores
   de estado, opciones, vistas, ítems, pisos legales, comparadores) lo pone
   el plugin, registrado en código y declarado en `builtins.yaml`; una
   prueba exige que coincidan.
11. **El turno** (`turn.yaml`, F7): lo que Jev contesta antes del turno (la
   ráfaga ①: qué asuntos plantea el cliente, qué le preguntó el asesor, qué
   responde) y antes de enviar (③: ¿la respuesta atiende cada asunto?), con
   las tablas de la política que arma el turno (código del plugin). El
   catálogo declara su vocabulario (`turn:` — políticas con los umbrales y
   las preguntas que leen, hechos y sus valores, etapas, datos, tools); lo
   que no está ahí no compila (DB015). Trae:

   - `policy` y `thresholds` (exactamente los que lee la política);
   - `questionnaire`: asuntos, preguntas fijas / por asunto (`each_topic`) /
     por mensaje (`each_message`) con `when` sobre hechos del turno, la
     pregunta de ③ y los textos del `state` (un `{campo}` que el motor no
     llena es DB005);
   - `coverage` (②: tools, palabras o cualquier texto por asunto; todos los
     asuntos, `{}` si nada lo atiende), `reading` (la nota según lo que
     preguntó el asesor y lo que responde el cliente) y `guide`;
   - `contract`: asunto → tools, como **filas**: la primera del asunto que
     se cumple decide, `any_of: []` = no pide tool. CEL sobre `p` (una
     respuesta sin probabilidad vale 0), `th`, `inp` y `dom`:

     ```yaml
     contract:
       - topic: envio
         when: "'envio.costo' in p && p['envio.costo'] < th['detect']"
         any_of: []                      # pregunta cuánto tarda, no el costo
       - topic: envio
         any_of: [send_shipping_rates]
         nudge: "Para el costo del envío usa send_shipping_rates. …"
     ```
   - `verify_decide`: ③ por asunto (`item` con `topic`, `msg` y `p` si Jev
     contestó) → `covered | missing | doubt`; el motor junta: cualquier
     missing → complemento, cualquier doubt → pendiente, si no, se envía;
   - `examples` del contrato y de ③ (DB010).

   En código: `bundle.turn` (`CompiledTurn`): `turn.required(asuntos, p=…,
   inp=…)`, `turn.verify([(asunto, mensaje)…], p=…)` y sus tablas. En ventas,
   el perfil `jev-v5` dice `turn: bundle` y `decisions/turn.py: turn_of(perfil)`
   le da al motor el cuestionario, la política y las tablas del paquete activo.

## Cómo se usa

Certificar (CI, hooks y antes de promover en el laboratorio):

```bash
cd hubara_agency && uv run python -m src.sdk.cli decisions check
```

Esquema para el editor (autocompleta y marca errores en VS Code con
`# yaml-language-server: $schema=…` al principio del YAML):

```bash
cd hubara_agency && uv run python -m src.sdk.cli decisions schema
```

En código (plugin):

```python
from src.sdk.decisionkit import DOUBT, answers_from_result, load_bundle

bundle = load_bundle(bundle_dir, catalog_path)        # BundleError si no compila
table = bundle.capability("baja")
questions = table.questions_for(inp_fields)          # las fijas y las condicionales que se cumplen
value = table.decide(answers=answers_from_result(table.spec.questions, result), rule=rule_value,
                     inp=inp_fields, options={"categoria.cual": {"santos": "velas-religiosas"}})
if value is DOUBT:
    ...  # decide la regla
```

Otro plugin, su propio paquete: el lector de Jev del Order Sentinel
(`order_sentinel/agent/decisions/`) trae su catálogo y su paquete
(`centinela`: «¿qué cambió?» y la evidencia), los carga con `load_bundle` y
corre sus builtins con los `with:` del paquete (`call(table.spec.state, convo)`);
`decisions check` lo encuentra junto a los de ventas.

El motor de ventas lo envuelve en `decisions/bundled.py` (`BundledCapability`,
con la forma de `Capability`) y los lugares lo piden por nombre al resolutor
(`decisions/registry.py: capability("baja")`), que toma el paquete activo
de la tienda (`SALES_DECISIONS_BUNDLE`, Terraform `tenants.<t>.lab.decisions_bundle`)
o, si la capacidad todavía no migró, su clase. El `Verdict` lleva
`bundle: "ventas@1"` en la traza.

## Reglas al escribir un paquete

1. Correr `decisions check` siempre; un paquete que no pasa no existe.
2. Un ejemplo por fila de `decide`, más los bordes de cada umbral.
3. Llaves literales: `p['baja.pide']`, nunca calculadas; antes de leer una
   respuesta, preguntar si llegó (`'baja.pide' in p`).
4. Lo que no cabe en una fila es un builtin nuevo (código + prueba + entrada
   en `builtins.yaml`), no una condición CEL de tres líneas.
5. Otra pregunta u otro umbral = otra versión del paquete.
6. Los pisos se piden por nombre; los obligatorios (`required_floors`) no se
   pueden cambiar.
7. Una opción que no está en la lista no pasa: antes de leer
   `opt['q'][choice['q']]`, preguntar `choice['q'] in opt['q']`.
8. Un ítem sin respuesta no trae `p` (ni `choice`): `has(i.p)` antes de
   leerla. En un string de YAML con comillas dobles, `\n` es un salto de
   línea real: para el separador de `join` se escribe `'\\n'`.
9. En el turno, toda fila del contrato que pide tools lleva su `nudge` (la
   nota que nombra la que falta); una fila sin condición cierra el asunto
   (las que siguen del mismo asunto nunca se leen: DB008).
