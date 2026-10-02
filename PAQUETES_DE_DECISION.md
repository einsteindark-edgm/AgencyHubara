# Paquetes de decisión — diseño

> 2026-10-01. Sigue a `MOTOR_DECISIONES_PLAN.md` (el motor con Jev, F0–F8 y §6).
> Decisión de lenguaje: `ADR-2026-10-01-decision-bundles.md`.

## 0. En una página

El motor de decisiones funciona, pero su "inteligencia" vive en código: 22
capacidades son clases de Python con la pregunta a Jev, los umbrales y la
regla de decisión adentro. Cambiar una pregunta o un umbral exige desplegar
código, y llevar el motor a otra tienda (Vincenzo, zapatos) exige editar
código lleno de velas, aromas y zonas de Colombia.

La propuesta: separar **el motor** (código, igual para toda tienda) del
**paquete de decisión** (datos tipados y versionados, uno por tienda), como
se separa un runtime de inferencia del modelo que corre:

| Mundo de los modelos | Aquí |
|---|---|
| El modelo es un archivo inmutable y versionado | `bundles/<tienda>/<version>/` (YAML) |
| Formato con versión de operadores (ONNX opset) | `engine_contract: 1` en cada paquete |
| Se compila / valida antes de servir | `uv run python -m src.sdk.cli decisions check` |
| Se entrena y evalúa en otro lado | El laboratorio (banco, bots, scorecard) |
| Despliegue gradual por alias, reversa moviendo el alias | El despliegue por capacidad + techo de Terraform, ahora por versión de paquete |

El paquete es YAML validado por un esquema estricto (Pydantic, sin llaves
desconocidas), con condiciones en **CEL** (Common Expression Language, de
Google) compiladas y verificadas por tipos, y un **certificador** que
rechaza el paquete antes de desplegar si algo "no compila": una llave mal
escrita, una pregunta o un umbral que no existen, una condición que compara
un número con un texto, una regla que nunca se alcanza o un ejemplo que no
da lo esperado.

## 1. El problema, con evidencia

- **Las capacidades son clases.** 22 en `chats/agent/sales/decisions/capabilities/`
  (+5 del egreso en `decisions/egress.py`). Cada una trae `rule`, `ask`
  (texto de la pregunta), `decide` (umbrales y mapeo), `floor` y `same`.
- **Los umbrales no se pueden cambiar sin código.** El paso de umbrales del
  perfil a la capacidad es un no-op: `_thresholds(capability)` lee los de la
  propia clase y `decide` los mezcla consigo mismos
  (`capabilities/__init__.py:117`, `:212`). Nada de `profiles.yaml` llega a
  una capacidad.
- **Las preguntas no están versionadas.** El `Verdict` y la traza no dicen
  con qué texto ni qué umbral se decidió; el laboratorio no puede comparar
  dos redacciones de la misma capacidad sin desplegar código.
- **Valores duplicados que pueden divergir:** `compra` tiene `confirm .85` y
  `retract .20` en la clase y otra vez `purchase_confirm` / `purchase_retract`
  en `profiles.yaml`. `confidence: 0.60` del perfil no lo lee nadie.
- **El dominio está en el código.** Aroma y color son las dos dimensiones de
  variante cableadas (`texto.py`, `mapeos.py`, `context.py`, `turno_v3.py`,
  `rafaga-v5.yaml`); las zonas de envío son Bogotá/nacional
  (`mapeos.py:287`, `config/shipping.py:118`); `fuera_de_catalogo` dice que
  "material" NO es un atributo de producto (falso para zapatos de cuero).
- **Forge ya lo sufrió.** Para clonar el motor a Vincenzo hicieron falta 22
  reglas de reemplazo de texto sobre el código del motor (memoria
  `multi_tenant_commerce_architecture`). Es un parche, no un diseño.

Lo que **ya** es dato y funciona: los cuestionarios de la ráfaga
(`questionnaires/rafaga-v1..v5.yaml`, con paridad congelada en
`tests/fixtures/decisions/rafaga_v1_frozen.json`), los perfiles
(`profiles.yaml`) y el perfil del oráculo (`platform/perception/profiles.yaml`).
Este diseño extiende ese camino a las capacidades.

## 2. Arte previo (resumen de la investigación del 2026-10-01)

- **MLflow Prompt Registry:** versiones inmutables, alias `production` que se
  mueve, evaluación atada a la versión.
- **DSPy:** el programa optimizado se guarda como JSON de estado (instrucciones
  y ejemplos) y se carga en código: "entrenar en otro lado, desplegar un
  artefacto congelado".
- **OpenAI retira los "prompt objects" alojados (2026-11-30)** y recomienda
  versionar en el repo con commits, revisión y evals. Es exactamente este
  diseño: el paquete vive en git, se certifica en CI y se despliega moviendo
  un puntero.

## 3. Las tres capas

1. **Motor (código, igual para cualquier tienda).** Temporal, el puerto de
   Jev, los constructores de estado (la ventana de lo que vio el cliente, los
   hechos del pedido), el evaluador de condiciones, el certificador y **los
   pisos de seguridad**. Un paquete puede pedir un piso por nombre, pero no
   puede quitar los obligatorios (la baja legal, "nunca inventar precios").
2. **Paquete de decisión (dato, uno por tienda, versionado).** Preguntas,
   criterios, umbrales, tablas de decisión, qué capacidades existen, ejemplos
   que deben cumplirse y la calibración del laboratorio.
3. **Dominio de la tienda (dato).** Dimensiones de variante (velas: aroma,
   color, diseño; zapatos: talla, color, material), zonas de envío del país,
   vocabulario, ejemplos y los asuntos del cuestionario de la ráfaga.

## 4. Lenguaje: qué se evaluó y qué se eligió

Investigación completa: comparó CEL (tres implementaciones en Python, cel-go,
cel-rust), Rego (regorus), Cedar, GoRules ZEN, DMN/FEEL, JSON Logic,
Starlark, Jsonnet, CUE, Pkl, KCL, Dhall y Nickel.

**Elegido:** YAML + Pydantic v2 estricto (estructura) + **CEL vía
`cel-expr-python`** (condiciones) + **certificador propio** (referencias
cruzadas y semántica). Todo en el mismo proceso de Python.

- `cel-expr-python` es la implementación **oficial de Google** (envuelve
  cel-cpp, Apache-2.0, ruedas para 3.11–3.14 en Linux/macOS x86 y ARM).
  **Verifica tipos al compilar.** Probado el 2026-10-01 (spike):
  - declara `map<string,double>` y `list<double>` sin protobuf;
  - rechaza `pp['x']` ("undeclared reference"), `p['x'] >= 'a'`,
    `bool && int` y un `?:` con ramas de tipos distintos;
  - las macros (`filter`, `size`) funcionan;
  - evaluar una condición: ~11 µs.
- Lo que CEL no puede atrapar (una llave mal escrita dentro de un mapa,
  `p['cortesia.sol']`) lo atrapa el certificador: todas las llaves de `p`,
  `choice` y `th` deben ser literales y existir.
- **Descartados:** Rego (sin verificador de tipos en Rust/Python, semántica
  de "undefined" que los LLM confunden), Cedar (solo autorización), ZEN (en
  Python solo valida sintaxis; el análisis de tipos es del editor
  comercial), CUE/Pkl (API de Python inmadura o inexistente), DMN, JSON
  Logic, Jsonnet y Starlark (sin tipos o demasiado "programa").
- **Go o Rust como servicio aparte: descartado por ahora.** Las librerías de
  Python ya envuelven los mismos núcleos en C++/Rust, así que no ganan
  verificación. Una llamada local por gRPC cuesta 100–300 µs (10–100× la
  evaluación), agrega un proceso que la caja de 4 GB puede matar, y abre la
  puerta a que el servicio y el paquete queden en versiones distintas.
- **Igual queda desacoplado:** las condiciones se evalúan a través de un
  puerto (`ExpressionPort`). Si un día otro consumidor (la app móvil, otro
  lenguaje) necesita evaluar el mismo paquete, se agrega un adaptador
  (cel-go como servicio, ZEN) sin tocar los paquetes.
- **Riesgo aceptado:** `cel-expr-python` es 0.1.x. Mitigación: el puerto, la
  versión fijada en `uv.lock`, y que el formato del paquete (YAML + CEL
  estándar) no depende de la implementación.

## 5. Forma del paquete

```
decisions/bundles/
  builtins.yaml                      # catálogo del motor: lo que un paquete puede pedir por nombre
  hubara-ventas/
    bundle.yaml                      # id, versión, engine_contract, oráculo, dominio
    capabilities/
      baja.yaml
      cortesia.yaml
      ...
```

`bundle.yaml`:

```yaml
# yaml-language-server: $schema=../../../../../../../../schemas/decision-bundle.schema.json
id: hubara-ventas
version: 1
engine_contract: 1
oracle: jev-1.13
capabilities: [baja, cortesia]
```

Una capacidad (`cortesia.yaml`):

```yaml
capability: cortesia
value: bool
input: Inbound                       # tipo declarado en builtins.yaml
rule: {builtin: constant_false}      # lo que decide sin Jev (o con duda)
state: {builtin: customer_message_with_window}   # nada (null) = no se pregunta
questions:
  - id: cortesia.solo
    kind: yes_no
    text: "¿El mensaje del cliente es solo cortesía: …?"
    criteria: {"true": "sí, solo agradece o saluda", "false": "no: pregunta, pide algo o responde una pregunta"}
thresholds: {yes: 0.80}
decide:                              # en orden; la primera fila que se cumple decide
  - when: "!('cortesia.solo' in p)"
    then: doubt                      # duda: decide la regla
  - when: "p['cortesia.solo'] >= th['yes']"
    then: true
  - otherwise: false
floor: {builtin: jev}                # piso: lo que nunca se quita
same: bool_eq
examples:                            # el certificador los corre
  - answers: {cortesia.solo: 0.88}
    expect: true
  - answers: {cortesia.solo: 0.79}
    expect: false
  - answers: {}
    expect: doubt
```

**Entorno de CEL** (tipado, lo arma el motor por capacidad):

| Variable | Tipo | Qué es |
|---|---|---|
| `p` | `map<string,double>` | probabilidad de cada pregunta sí/no respondida |
| `choice` | `map<string,string>` | opción elegida de cada pregunta de opciones |
| `conf` | `map<string,double>` | confianza de esa opción |
| `th` | `map<string,double>` | umbrales de la capacidad |
| `rule` | según `value` | lo que dijo la regla |

**Builtins** (código del motor, catalogados en `builtins.yaml` con su firma):
constructores de estado, generadores de preguntas dinámicas
(`closed_choice`, `fan_out`), reglas (`is_opt_out_text`, …), pisos (`jev`,
`rule_or_jev`, …), comparadores (`bool_eq`, `set_eq`, …) y un reductor por
ítem (`per_item_select`) para las capacidades que deciden sobre una lista.
El catálogo es el "header" entre motor y paquetes: el certificador lo lee
sin importar código de plugins (R-DIP), y una prueba del plugin verifica
que el catálogo coincide con lo registrado en código.

## 6. El certificador ("¿compila?")

`uv run python -m src.sdk.cli decisions check <carpeta>` — falla con código
y ruta exacta del error. Lo corren CI, el hook de los agentes y el
laboratorio antes de promover una versión.

| Código | Qué rechaza |
|---|---|
| DB001 | YAML ilegible o estructura inválida (llave desconocida, tipo equivocado, campo faltante) — Pydantic estricto |
| DB002 | `engine_contract` que el motor no sabe correr |
| DB003 | capacidad listada sin archivo, archivo sin listar, o `capability` distinto del nombre del archivo |
| DB004 | builtin que no existe en el catálogo, o de otro tipo (un piso usado como regla) |
| DB005 | id de pregunta repetido, o llave `p['…']` / `choice['…']` / `th['…']` que no es literal o no está declarada |
| DB006 | condición que no compila en CEL (tipos, nombres) o que no devuelve bool |
| DB007 | `then` que no es del tipo `value` de la capacidad (ni `doubt`) |
| DB008 | `decide` sin `otherwise` final, o filas después del `otherwise` |
| DB009 | umbral fuera de [0,1] |
| DB010 | ejemplo que no da lo esperado |
| DB011 | `p['x']` sobre una pregunta de opciones (o `choice` sobre una sí/no) |

Además: el esquema JSON (`decisions schema`) para que el editor autocomplete
y marque errores mientras se escribe (`# yaml-language-server: $schema=…`).

## 7. Clasificación de las 22 capacidades

- **A** = cabe completa en YAML (preguntas + umbrales + tabla).
- **B** = la tabla cabe en YAML; el estado o las preguntas dinámicas los da un builtin.
- **C** = necesita un reductor del motor (`per_item_select`) porque decide sobre una lista.

| # | Capacidad | Valor | Regla hoy | Decisión | Piso | Clase | Acoplamiento al dominio |
|---|---|---|---|---|---|---|---|
| 1 | contactar | bool | constante | sobra/terminada vs .85/.15 | jev | A | — |
| 2 | cierre | etiqueta | constante | opción ≥ .85 | **invariantes del pedido (código)** | A | etiquetas del embudo |
| 3 | datos | slots | constante | por slot, p ≤ .15 bloquea | jev | C | cédula/barrio, abreviaturas de dirección (Colombia) |
| 4 | compra | [tipo, fuente] | `classify_inbound_purchase_signal` | opción .70 + confirma .85 + pregunta .85 / .20 | jev | B | "elige color, aroma, diseño" |
| 5 | retoma | {aplazo, cortesía} | parser de aplazamientos | .85/.15, ambas respondidas | jev | A | palabras de fecha (locale) |
| 6 | baja | bool | `is_opt_out_text` | 3 bandas .85/.15 | **regla ∨ jev (legal)** | A | — |
| 7 | acuse | bool | `is_closing_ack` | ≥ .90 | **jev ∧ sin "?"** | B | — |
| 8 | cortesia | bool | constante | ≥ .80 | jev | B | — |
| 9 | cupon | bool | `coupon_in_play` | 3 bandas .70/.15 | jev | B | modelo de cupón |
| 10 | fuera_de_catalogo | términos | `unavailable_terms` | por término, p > .15 se queda | **⊆ regla** | C | "material" no es producto (falso en zapatos), "envase" |
| 11 | cantidad | {cantidad} | `read_reply_quantity` | preguntó .85 + opción .85 | jev | A | máximo 20 |
| 12 | categoria | {categoria} | `resolve_category` | opción cerrada ≥ .80 | jev | B | — |
| 13 | familia_de_color | resolución | `resolve_color_family` | opción cerrada ≥ .80 | **regla si mismo color** | B | **color cableado como dimensión** |
| 14 | item_del_pedido | {item} | `item_for_values` | opción cerrada ≥ .80 | jev | B | campos aroma/color/diseño |
| 15 | zona_de_envio | {zona} | `shipping_zone` | opción ≥ .85 | jev | A | **Bogotá/nacional cableados** |
| 16 | producto_nombrado | títulos | coincidencia exacta | opción cerrada ≥ .85 | **∪ regla** | B | — |
| 17 | persona | índices | `breaks_human_persona` | por frase .85/.15 | **∪ regex** | C | regex en español |
| 18 | enumeracion | (dim, etiquetas) | `find_enumerated_variants` | opción ≥ .85 | **() si hay combinaciones** | B | **aroma/color cableados** |
| 19 | monto | frases | constante | por frase ≥ .85 | jev | C | "pago contra entrega" |
| 20 | selector | títulos | rechazo por vocabulario | 3 bandas .85/.15 | **∪ by_id** | A | "(aroma, color, diseño)" |
| 21 | afirmacion | bool | constante | 3 bandas | jev (solo sombra) | A | — |
| 22 | relevo | bool | `_RELEVO_RE` | 3 bandas | **regla ∨ jev** | A | regex en español |

**Totales:** A = 9, B = 9, C = 4. Las 4 C tienen la misma forma (decidir
ítem por ítem) y se resuelven con un solo reductor parametrizado
(`per_item_select`): qué selecciona un ítem, qué hacer en la banda de duda,
qué devuelve y cuándo es duda.

**Fuera de alcance por ahora:** las 5 del egreso (`Preambulo`,
`Destinatario`, `Rescate`, `Portavelas` —100 % velas—, `Saludo`) y las
políticas del turno `turno_v1..v3` (≈70 % ya son tablas; un `turno-v4`
declarativo es posible después con `plan_from_topics`, `coverage_triage`).

**Pisos que siguen siendo código** (los pide el paquete por nombre, no los
escribe): baja, relevo, persona, acuse, selector, enumeracion, cierre,
fuera_de_catalogo, producto_nombrado, familia_de_color.

## 8. El dominio de la tienda

Inventario de lo específico de velas/Colombia dentro de `decisions/` (para el
paquete de dominio, fase F5):

- dimensiones de variante: `texto.py` 140–211 y 326, `lecturas.py:79`,
  `mapeos.py` 108–115 y 194, `context.py:181,211`, `turno_v2.py:46`,
  `turno_v3.py:72,117`, `rafaga-v5.yaml` 18–19, 46–47, 80;
- ejemplos de velas: `rafaga-v4/v5.yaml`, `probe.py:109–175`, `egress.py:386`;
- Colombia: `mapeos.py:287`, `config/shipping.py:118`, `datos.py:38–69`,
  `texto.py:258`, `turno_v1.py:42`;
- palabras de cobertura: `turno_v1.py:37–55`.

Para zapatos cambiaría: dimensiones `[talla, color, material]`, asunto
`aroma` → `talla`, `medidas` → horma/tabla de tallas, un asunto nuevo de
cambios de talla, `material` como atributo de producto, y las zonas del país.

## 9. Versionado y despliegue

- Un paquete es inmutable: `hubara-ventas@3` no cambia; otro texto u otro
  umbral = `@4`.
- El `Verdict` y la traza llevan `bundle` (`id@version`), igual que hoy
  llevan el modelo de Jev.
- El laboratorio corre un paquete nuevo contra el banco; si pasa, se promueve.
- Producción elige la versión por capacidad (el despliegue gradual que ya
  existe), siempre dentro del techo de Terraform.
- Recargar: hoy los YAML se cachean; un paquete nuevo entra con el despliegue
  (como los cuestionarios). Recarga en caliente queda para después.

## 10. Fases

| Fase | Qué | Cambio de comportamiento |
|---|---|---|
| **F1** | Motor de paquetes: esquema, puerto de expresiones (CEL), catálogo de builtins, certificador + CLI + esquema JSON. Primeras capacidades desde YAML con paridad contra la clase: `baja` (A, piso legal) y `cortesia` (B) | ninguno (paridad) |
| F2 | Las otras 7 A | ninguno |
| F3 | Las 9 B con sus builtins (`customer_window`, `closed_choice`, …) | ninguno |
| F4 | Las 4 C con `per_item_select` | ninguno |
| F5 | Paquete de dominio: dimensiones, zonas, vocabulario. Quita las 22 reglas de reemplazo de forge | ninguno en Hubara |
| F6 | Versión de paquete en el Verdict, el laboratorio y el despliegue gradual | — |
| F7 | `turno-v4` declarativo (opcional) | — |

**La paridad es la compuerta de cada fase:** para cada capacidad migrada,
(a) el estado y las preguntas son byte a byte iguales a los de la clase en
entradas representativas, y (b) la decisión coincide sobre una grilla de
respuestas en los bordes de cada umbral (0,14 / 0,15 / 0,16 …), con y sin
respuesta, y con los pisos. Mientras haya paridad, la clase se puede borrar.

## 11. Riesgos

| Riesgo | Mitigación |
|---|---|
| El YAML se vuelve un lenguaje de programación peor | CEL no es Turing-completo; lo complejo va a builtins con nombre; el certificador rechaza lo que no entiende |
| Un agente (o una persona) inventa llaves | Pydantic `extra=forbid` + DB005 + esquema JSON en el editor + ejemplos obligatorios |
| `cel-expr-python` 0.1.x cambia o se abandona | Puerto `ExpressionPort`, versión fijada, CEL es un estándar con 3 implementaciones |
| Un paquete quita un piso legal | Los pisos obligatorios los aplica el motor aunque el paquete no los pida |
| Paquete y código desalineados | `engine_contract` + catálogo de builtins verificado por prueba |

## 12. Reglas para quien escriba paquetes (humano o agente)

1. Nunca escribir un paquete sin correr `decisions check`; un paquete que no
   pasa no existe.
2. Toda capacidad trae ejemplos: al menos uno por fila de `decide`, más los
   bordes de cada umbral.
3. Las llaves de `p`, `choice` y `th` van literales (`p['baja.pide']`), nunca
   calculadas.
4. Lo que no cabe en una fila es un builtin nuevo en el motor (con su prueba
   y su entrada en el catálogo), no una condición CEL de 3 líneas.
5. Otro texto u otro umbral = otra versión del paquete; nunca se edita una
   versión publicada.
6. Los pisos se piden por nombre; nunca se reescriben en el paquete.
