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
bundles/
  builtins.yaml            catálogo del motor: lo que un paquete puede pedir por nombre
  ventas/
    bundle.yaml            id, versión, engine_contract, oráculo, capacidades
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
   Códigos DB001–DB013 (lista en `src/platform/decisions/checker.py`).
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
9. **Builtins:** lo que sigue siendo código (reglas de texto, constructores
   de estado, opciones, vistas, ítems, pisos legales, comparadores) lo pone
   el plugin, registrado en código y declarado en `builtins.yaml`; una
   prueba exige que coincidan.

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
