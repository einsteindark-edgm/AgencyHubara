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
   builtins del catálogo, pisos y reglas obligatorios) y los ejemplos del
   paquete. Códigos DB001–DB016 (lista en `src/platform/decisions/checker.py`;
   los del turno, en `turn_check.py`). Desde el premortem del 2026-10-02
   también rechaza:
   - lecturas sin llave a la vista (`(choice)['x']`, `('x') in choice`,
     `choice.exists(…)`, `size(p)`), `dom.seccion.campo` que no existe e
     `i.campo` que el ítem no trae dentro de `items.filter(i, …)` (o de una
     `vars` que es un `filter` de `items`) — DB005;
   - una comparación con una opción que la pregunta no tiene
     (`choice['q'] == 'otra'`) — DB005; una etapa que el catálogo no declara
     en una condición del turno — DB015;
   - una llave repetida en el YAML, y `yes:`/`no:` sin comillas como opción
     (YAML los vuelve `true`/`false`) — DB001;
   - una fila `when` que ningún ejemplo decide, o un ejemplo que solo pasa
     porque la tabla falló — DB010;
   - un oráculo (`oracle:`) que ningún perfil usa — DB016.

   Lo que declara el catálogo (`builtins.yaml`) para que el certificador lo
   haga cumplir:
   - `capabilities:` las que el código del plugin pide (DB003 si falta una;
     el resolutor no cae a otra implementación);
   - `places:` (id → la parte del software, en el orden de una conversación)
     y `about:` (por capacidad: `name`, `where: [lugar]`, `solves`) en
     español llano: Calidad LLM → «Motor de decisiones» los muestra al
     operador. DB003 si una capacidad de `capabilities:` no trae su `about`,
     si un `about` no es de una capacidad pedida o si nombra un lugar que
     `places:` no declara;
   - `required_rules:` / `required_floors:` la regla o el piso que una
     capacidad no puede cambiar (DB012: la baja legal, el relevo);
   - `takes_items: true` en los builtins de estado que reciben la lista de
     ítems e `items_to_state: true` si el motor se la pasa (DB004 si el
     estado de una capacidad con ítems no la recibe);
   - `keys: [campo]` en un builtin `items`: los campos que identifican al
     ítem; `each.id` lleva `{n}`, `{index}` o esas llaves, y nada más.
   - `engine_contract`: el motor corre los de `SUPPORTED_CONTRACTS` (al subir
     el contrato, el viejo se queda: un paquete publicado es inmutable).
4. **Tabla:** la primera fila cuya condición se cumple decide; `doubt` =
   decide la regla. Una condición que falla al evaluarse también es duda
   (leer una llave que no está devuelve un *valor de error* en CEL; el motor
   lo trata como duda, nunca como false: la fila siguiente no decide).
   `decide_explained(…)` devuelve `Decision(value, row, error)`: leer una
   respuesta que no llegó es duda legítima (log en debug); cualquier otro
   error es un bug del paquete (warning `decision_bundle.row_error` con
   paquete y fila, y `error`). En ventas ese caso no es «duda»: el veredicto
   dice `reason=bundle_error`. Un `then` literal (`{zona: …}`) se copia en
   cada decisión.
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
     asuntos; `{}` es una regla que nunca se cumple: el asunto queda SIEMPRE
     sin atender y pide otra ronda), `reading` (la nota según lo que
     preguntó el asesor y lo que responde el cliente) y `guide`;
   - `contract`: asunto → tools, como **filas**: la primera del asunto que
     se cumple decide, `any_of: []` = no pide tool. CEL sobre `p` (una
     respuesta sin probabilidad vale 0), `th`, `inp` y `dom`; una fila que
     lee `p['x']` pregunta antes `'x' in p` (DB005), y una fila que falla al
     evaluarse no decide (sigue la siguiente del asunto):

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
`decisions check` lo encuentra junto a los de ventas. La App Operador hace lo
mismo dentro de `chats` (`chats/shared/operator/decisions/`, paquete
`operador`): una carpeta `decisions/bundles/` con su propio `builtins.yaml`,
para no heredar el catálogo de ventas (DB003 pediría sus 29 capacidades). Y
corre por el `decide()` de ventas: el resolutor
`registry.foreign_capability(nombre, bundle=…, builtins=…)` arma un
`BundledCapability` con los builtins de ese paquete, así que una capacidad de
otro catálogo tiene el mismo panel, métricas, desacuerdos y registro.

El motor de ventas lo envuelve en `decisions/bundled.py` (`BundledCapability`,
con la forma de `Capability`) y los lugares lo piden por nombre al resolutor
(`decisions/registry.py: capability("baja")`), que toma el paquete activo
de la tienda (`SALES_DECISIONS_BUNDLE`, Terraform `tenants.<t>.lab.decisions_bundle`).
Un paquete que no compila se registra una vez (`decisions.bundle_broken`) y
el ingest sigue con las reglas del código; la API y los workers lo compilan
al arrancar (`warm_up`: `decisions.bundle_ready` con el `id@versión`). El
`Verdict` lleva `bundle: "ventas@1"` en la traza, y las métricas y la cola
de desacuerdos guardan `bundle` y `variant`. Cómo promover un paquete:
`PAQUETES_DE_DECISION.md` §9.

## Reglas al escribir un paquete

1. Correr `decisions check` siempre; un paquete que no pasa no existe.
2. Un ejemplo por fila de `decide`, más los bordes de cada umbral.
3. Llaves literales: `p['baja.pide']`, nunca calculadas; antes de leer una
   respuesta, preguntar si llegó (`'baja.pide' in p`).
4. Lo que no cabe en una fila es un builtin nuevo (código + prueba + entrada
   en `builtins.yaml`), no una condición CEL de tres líneas.
5. Otra pregunta u otro umbral = otra versión del paquete (la huella de
   cada versión publicada está congelada en
   `tests/plugins/test_decision_bundles_published.py`).
6. Los pisos y las reglas se piden por nombre; los obligatorios
   (`required_floors`, `required_rules`) no se pueden cambiar.
7. Una opción que no está en la lista no pasa: antes de leer
   `opt['q'][choice['q']]`, preguntar `choice['q'] in opt['q']`.
8. Un ítem sin respuesta no trae `p` (ni `choice`): `has(i.p)` antes de
   leerla. En un string de YAML con comillas dobles, `\n` es un salto de
   línea real: para el separador de `join` se escribe `'\\n'`.
9. En el turno, toda fila del contrato que pide tools lleva su `nudge` (la
   nota que nombra la que falta); una fila sin condición cierra el asunto
   (las que siguen del mismo asunto nunca se leen: DB008).
