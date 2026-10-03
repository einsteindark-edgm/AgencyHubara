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
  en `profiles.yaml`. `confidence: 0.60` del perfil no lo lee nadie
  (resuelto el 2026-10-02: se quitó de todos los perfiles y de `turno-v1`, y
  una prueba frena un umbral que su política no lee).
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
src/plugins/chats/shared/decisions/bundles/   # de chats: lo leen ventas y remarketing (F5)
  builtins.yaml                      # catálogo del motor: lo que un paquete puede pedir y el dominio que trae
  ventas/                            # una carpeta por tienda (Terraform la nombra)
    bundle.yaml                      # id, versión, engine_contract, oráculo, capacidades
    domain.yaml                      # el dominio de la tienda (F5): nombre, despedida, vocabulario del agente
    turn.yaml                        # el turno (F7): la ráfaga ①, ③ y las tablas de la política
    capabilities/
      baja.yaml
      cortesia.yaml
      ...
```

`bundle.yaml`:

```yaml
# yaml-language-server: $schema=../../../../../../../schemas/decision-bundle.schema.json
id: ventas
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
    kind: noul
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
same: {builtin: bool_eq}
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
| `inp` | `map<string,dyn>` | los campos de la entrada declarados en el catálogo y los de la vista |
| `vars` | `map<string,dyn>` | los cálculos intermedios de la capacidad, en orden |
| `consts` | `map<string,dyn>` | las constantes del catálogo |
| `opt` | `map<string,map<string,dyn>>` | el valor de cada opción que armó la entrada (F3) |
| `items` | `list<map<string,dyn>>` | los ítems con su posición y su respuesta (F4, solo con `items:`) |

**Lo que la entrada decide (F3):**

- `options:` en una pregunta choice: un builtin arma las opciones desde la
  entrada (`{opción: (etiqueta, valor)}`); Jev lee la etiqueta, la tabla lee
  el valor como `opt['<pregunta>'][<opción>]`. Las opciones fijas de
  `criteria` (`ambiguo`, `ninguno`) van al final.
- `when:` en una pregunta: se pregunta solo si la condición se cumple. Lee
  `inp` y `consts`, nunca respuestas (todavía no hay).
- `view:` en la capacidad: un builtin deriva campos de la entrada (la
  ventana de lo que vio el cliente, lo que el texto enumera) que se leen como
  `inp.campo`.
- `control:` una variante de otra capacidad (la misma decisión preguntada de
  otra forma) comparte su interruptor, su fila del panel y su nombre en la
  traza.
- Tipos posicionales y uniones: `() | (string, tuple<string>)`.

**Builtins** (código del motor, catalogados en `builtins.yaml` con su firma):
reglas (`opt_out_text`, …), constructores de estado, opciones de la entrada
(`catalog_categories`, …), vistas (`purchase_window`, …), ítems
(`items_of`, `paragraphs`, …: la lista de una capacidad que decide ítem por
ítem, F4), pisos (`jev`, `rule_or_jev`, …) y comparadores (`bool_eq`,
`set_eq`, …).
El catálogo es el "header" entre motor y paquetes: el certificador lo lee
sin importar código de plugins (R-DIP), y una prueba del plugin verifica
que el catálogo coincide con lo registrado en código.

## 6. El certificador ("¿compila?")

`uv run python -m src.sdk.cli decisions check <carpeta>` — falla con código
y ruta exacta del error. Lo corren CI, el hook de los agentes y el
laboratorio antes de promover una versión.

| Código | Qué rechaza |
|---|---|
| DB001 | YAML ilegible o estructura inválida (llave desconocida, tipo equivocado, campo faltante) — Pydantic estricto; una llave repetida; `yes:`/`no:` sin comillas como opción de una pregunta de opciones |
| DB002 | `engine_contract` que el motor no corre (`SUPPORTED_CONTRACTS`: al subir el contrato, el viejo se queda) |
| DB003 | capacidad listada sin archivo, archivo sin listar, `capability` distinto del nombre del archivo, o una capacidad que el catálogo exige (`capabilities:`) y el paquete no trae; una capacidad del catálogo sin su `about` (nombre, dónde actúa y qué resuelve), un `about` de una capacidad que el código no pide, o un `where` que `places:` no declara |
| DB004 | builtin que no existe en el catálogo, o de otro tipo (un piso usado como regla); el estado de una capacidad con ítems que no los recibe (`takes_items`) |
| DB005 | id de pregunta repetido; llave `p['…']` / `choice['…']` / `th['…']` que no es literal o no está declarada; una lectura sin llave a la vista (`(choice)['x']`, `size(p)`); `dom.seccion.campo` o `i.campo` (dentro de `items.filter(i, …)`) que no existen; una opción mal escrita (`choice['x'] == 'otra'`); el id de cada ítem sin su posición o su llave |
| DB006 | condición que no compila en CEL (tipos, nombres) o que no devuelve bool |
| DB007 | `then` que no es del tipo `value` de la capacidad (ni `doubt`) |
| DB008 | `decide` sin `otherwise` final, o filas después del `otherwise` |
| DB009 | umbral fuera de [0,1] |
| DB010 | ejemplo que no da lo esperado, que solo pasa porque la tabla falló, o una fila `when` que ningún ejemplo decide |
| DB011 | `p['x']` sobre una pregunta de opciones (o `choice` sobre una sí/no) |
| DB012 | piso o regla obligatorios cambiados (la baja legal, el relevo, los invariantes del cierre; `required_rules` del catálogo) |
| DB013 | el id del paquete no es su carpeta, o el paquete configurado no existe |
| DB014 | el dominio de la tienda (`domain.yaml`) no es el que declara el catálogo |
| DB015 | el turno (`turn.yaml`) nombra una política, un hecho, un asunto, una tool, una etapa (también dentro de una condición) o un dato que el catálogo no declara; un umbral que la política no lee; o el cuestionario no hace una pregunta que la política lee |
| DB016 | el paquete está calibrado para un oráculo (`oracle:`) que ningún perfil usa |

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

**Las del egreso** (`preambulo`, `destinatario` y sus variantes `destinatario_plantilla`
y `destinatario_oracion`, `rescate`, `portavelas` —100 % velas—, `saludo`):
las que hacen UNA pregunta (`destinatario`, `destinatario_plantilla`,
`saludo`) migran en F3; las que preguntan parte por parte (`preambulo` por
oración, `destinatario_oracion`, `rescate` por párrafo, `portavelas` por
oración) tienen la forma C y migran en F4 con `per_item_select`. Las
políticas del turno `turno_v1..v3` (≈70 % ya son tablas) van en F7.

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

**Dónde quedó cada cosa (F5, 2026-10-01):**

- **Ya es dato de la tienda:** las preguntas y criterios de las 29
  capacidades (zonas de envío, «elige un producto, color, aroma, diseño»,
  «un material», las dimensiones del selector) viven en sus YAML (F2–F4); el
  nombre de la tienda, la despedida aprobada y los 16 ejemplos que el agente
  ve en sus herramientas y en el gancho de remarketing viven en
  `domain.yaml` (F5). Una tienda nueva escribe su carpeta; forge le entrega
  un dominio neutral (`forge/templates/sales_domain.yaml.tpl`) en vez de
  reemplazar texto en el código.
- **Sigue en código (pendiente):** los campos del pedido `aroma`/`color`/
  `diseno` (el modelo de datos de `set_order_slot`, el borrador y el
  registro), las reglas de hoy con vocabulario de Colombia (la cédula y el
  barrio de `datos`, `shipping_zone`), las frases del piso de persona (en
  español) y la redacción genérica de la guía de etapas («pide solo lo que
  falta: …», el turno de complemento), que es del embudo y no de la tienda.
- **Desde F7 también es dato:** la ráfaga ① (los 18 asuntos, sus pistas con
  «velas religiosas, de Halloween», «aromas»…), qué atiende cada asunto
  (②), el contrato asunto → tools con sus excepciones, las notas de la
  lectura del hilo («color, aroma, diseño o cantidad»), la banda de ③ y la
  guía de etapas: `turn.yaml`.

## 9. Versionado y despliegue

- Un paquete es inmutable: `ventas@3` no cambia; otro texto u otro
  umbral = `@4`.
- El `Verdict` y la traza llevan `bundle` (`id@version`), igual que hoy
  llevan el modelo de Jev.
- El laboratorio corre un paquete nuevo contra el banco (un brazo
  `B@<paquete>`, F6); si pasa, se promueve.
- Promover = nombrar el paquete en Terraform (`tenants.<t>.lab.decisions_bundle`)
  y desplegar: es la misma palanca que hoy, con historial y revisión. El
  despliegue gradual de cada capacidad (reglas → sombra → jev, por
  porcentaje y números de prueba) sigue siendo el que ya existe, ahora sobre
  las capacidades del paquete activo. Un despliegue gradual de una VERSIÓN de
  paquete por conversación (dos paquetes vivos a la vez) no hace falta para
  operar y queda fuera (exigiría pasar el paquete por cada lugar que decide).
- Recargar: hoy los YAML se cachean; un paquete nuevo entra con el despliegue
  (como los cuestionarios). Recarga en caliente queda para después.

**Promover un paquete (procedimiento, premortem del 2026-10-02):**

1. El paquete nuevo ya está en `main` y DESPLEGADO (la imagen lo trae). CI lo
   certifica (`decisions check` en `architecture-gates.yml`) y su huella queda
   congelada en `tests/plugins/test_decision_bundles_published.py`.
2. PR con `tenants.<t>.lab.decisions_bundle = "<paquete>"` en
   `infra/terraform/platform/tenants.auto.tfvars` (una prueba exige que el
   paquete exista) y `terraform apply` de platform (el operador).
3. `workflow_dispatch` de **Backend deploy**: renderiza el `.env` desde SSM y
   recrea los containers. `backend-deploy.yml` no se dispara con cambios de
   Terraform; sin este paso el paquete entra con el siguiente merge ajeno,
   mezclado con otro cambio. Antes del `up -d`, el deploy certifica DENTRO
   de la imagen nueva el paquete que nombra SSM: si no está o no compila,
   aborta y los containers viejos siguen sirviendo.
4. Verificar: la API y los workers registran `decisions.bundle_ready` con el
   `id@versión` al arrancar (`decisions.bundle_broken` si no compila).
5. Volver atrás = el id anterior en Terraform, apply y dispatch.

Nunca se borra del repo un paquete que SSM nombra (o que un rollback de
imagen puede pedir): el deploy aborta, pero un paquete borrado deja sin
salida al rollback. `ventas-2@2` hoy no cambia nada en vivo: su única
diferencia (la regla ② de `promocion`) solo actúa con la percepción del V1
en canary/on, y el techo de producción es `off`.

## 10. Fases

| Fase | Qué | Cambio de comportamiento |
|---|---|---|
| **F1** ✅ | Motor de paquetes: esquema, puerto de expresiones (CEL), catálogo de builtins, certificador + CLI + esquema JSON. Primeras capacidades desde YAML con paridad contra la clase: `baja` (A, piso legal) y `cortesia` (B) | ninguno (paridad) |
| F2 ✅ | **Resolutor único por nombre** (`capability("baja")`: los lugares dejan de nombrar clases), **paquete activo desde Terraform** (`tenants.<t>.lab.decisions_bundle` → `SALES_DECISIONS_BUNDLE`), tipos de valor del paquete (más allá de bool/string) y las 8 A restantes | ninguno |
| F3 ✅ | Las 9 B con sus builtins de estado, opciones de la entrada, vistas y preguntas condicionales + las del egreso de una pregunta (destinatario, su variante de plantilla y saludo) | ninguno |
| F4 ✅ | Las 4 C + las del egreso parte por parte (preámbulo, destinatario por oración, rescate, portavelas), con `items:` + `each:` (el `per_item_select` del diseño) | ninguno |
| F5 ✅ | Dominio de la tienda en el paquete (`domain.yaml`, certificado; `dom.x` en CEL): nombre, despedida y el vocabulario del agente. Los paquetes pasan a `chats/shared/decisions/bundles/` (los leen los dos agentes). Quita las 21 reglas de reemplazo de forge | ninguno en Hubara |
| F6 ✅ | Un brazo del laboratorio fija paquete (`B@ventas-2`): se compara contra B (el de la tienda) y contra A1. Producción promueve por Terraform (`decisions_bundle`) | — |
| F7 ✅ | El turno dentro del paquete: la ráfaga ① (`rafaga-v5` → `turn.yaml`) y la verificación ③ (`coverage_decision`), con las tablas de la política como filas certificadas | ninguno |
| F8 ✅ | Paquete propio del Order Sentinel («¿qué cambió?» y la evidencia) sobre el mismo motor genérico | ninguno |

**F1 hecho (2026-10-01).**
- Motor genérico en `src/platform/decisions/` (modelos, `ExpressionPort` +
  `CelExpressions`, certificador DB001–DB012, tablas compiladas), fachada
  `src.sdk.decisionkit`, CLI `decisions check|schema`, esquemas del editor en
  `hubara_agency/schemas/` (con prueba de que no se desactualizan).
- Paquete `ventas@1` con `baja` y `cortesia`; el ingest las lee del
  paquete (`decisions/bundled.py`) y el `Verdict` lleva `bundle` en la traza.
  Paridad exacta con las clases: 27 pruebas (estado y preguntas byte a byte,
  bordes de umbral, piso, veredicto completo con reglas/sombra/jev).
- Hook del plugin `hubara-dev`: editar un YAML de `decisions/bundles/` corre
  el certificador en el momento. Skill: `references/06-decision-bundles.md`.
- Las clases `Baja` y `Cortesia` quedan solo como oráculo de la paridad; se
  borran cuando su fase cierre.

**F2 hecho (2026-10-01).**
- Resolutor único (`decisions/registry.py: capability(nombre)`): ingest,
  guardas, tools, activities, egreso, remarketing, abandono y contacto piden
  la decisión por nombre. Dos pruebas prohíben instanciar o importar clases
  de capacidad (o sus instancias globales) fuera del resolutor.
- Paquete activo desde Terraform: `tenants.<t>.lab.decisions_bundle`
  (default `ventas`, validado) → SSM `SALES_DECISIONS_BUNDLE`. Un
  paquete que no existe falla con `DB013`; el id del paquete debe ser su
  carpeta (DB013 también).
- Motor: tipos de valor, `then: {expr}`, `vars`, `inp.campo`, `consts.X`,
  y un arreglo: leer una llave ausente en CEL devuelve un valor de error que
  antes se trataba como false (la fila siguiente decidía); ahora es duda.
- Migradas con paridad en grilla completa: contactar, cierre (invariantes
  del pedido como piso obligatorio), retoma, cantidad, zona_de_envio,
  selector, afirmacion y relevo (piso obligatorio). Paquete: 10 capacidades.

**F3 hecho (2026-10-01).**
- Motor: `options:` (opciones de un choice armadas desde la entrada, leídas
  como `opt[…]`), `when:` (preguntas condicionales sobre `inp`), `view:`
  (campos derivados), `control:` (variantes que comparten interruptor),
  tuplas posicionales y uniones de tipos. El certificador las cubre (DB003,
  DB004, DB005, DB006, DB010).
- Migradas con paridad en grilla completa (y el orden de las opciones byte a
  byte): compra (pregunta condicional y la ventana como vista), acuse (piso:
  sin signo de pregunta), cupón, categoría, familia de color (piso: el
  detalle de la regla si coincide), ítem del pedido, producto nombrado
  (piso: la regla más Jev), enumeración (piso: sin combinaciones),
  destinatario, destinatario de plantilla (`control: destinatario`) y saludo.
  Paquete: 21 capacidades.
- Los builtins que arrastran el ingest, `use_cases/` o el catálogo viven en
  `bundled_ingest.py`, `bundled_catalog.py` y `bundled_egress.py` y se
  cargan por nombre: las tools no importan Temporal.
- Quedan como clases: las 4 C (datos, fuera_de_catalogo, persona, monto) y
  las 4 del egreso parte por parte → F4.

**F4 hecho (2026-10-01).**
- Motor: `items:` (un builtin arma la lista: oraciones, párrafos, términos,
  datos) y `each:` (la pregunta de cada ítem, con plantillas `{n}`,
  `{index}` y `{campo}`, condición `when` y variantes del texto). La tabla
  lee `items` (cada ítem con sus campos, su posición y, si Jev contestó,
  `p` o `choice`/`conf`) y lo recorre con `filter`/`map`/`exists`/`all`/
  `join` (extensión `strings` de CEL). Así queda el `per_item_select` del
  diseño, sin un reductor especial: cada capacidad escribe su selección en
  CEL.
- El certificador revisa las plantillas (`{campo}` que el ítem no trae:
  DB005), las condiciones por ítem (solo leen `item`, `inp`, `consts`) y
  los ejemplos con `items:` (cada uno con su respuesta). Una condición que
  lee un campo (`item.ask`) se evalúa con valores de ejemplo de los tipos
  declarados para saber si da true o false.
- Migradas con paridad en grilla completa: persona (piso: las frases que
  nada deja pasar), monto, datos (texto de la pregunta con variantes: los
  datos personales nunca van en ella), fuera de catálogo (piso: Jev solo
  quita), preámbulo (el corte por prefijo en CEL; el corte mecánico del
  texto en el builtin), destinatario por oración (`control: destinatario`),
  rescate y portavelas. **Paquete: 29 capacidades; ninguna sale ya de una
  clase** (una prueba lo exige). Las clases quedan solo como oráculo de la
  paridad.

**F5 hecho (2026-10-01).**
- Motor: el catálogo declara el dominio que trae cada paquete (`domain:`
  campo → tipo, con secciones); el certificador exige el `domain.yaml` del
  paquete completo, del tipo y sin campos de más (DB014); las condiciones
  lo leen como `dom.campo`; `load_domain` lo lee sin compilar capacidades.
- Los paquetes viven en `chats/shared/decisions/bundles/` y
  `chats/shared/store_pack.py` dice cuál es el activo (`SALES_DECISIONS_BUNDLE`)
  y lee su dominio: lo usan las tools de ventas al importarse y el gancho
  de remarketing (que no puede importar ventas).
- Los ejemplos de la tienda («'lavanda', 'el morado'», «el Velón de
  Cristo», «nuestras velas religiosas»…) salen de `domain.yaml: vocabulary`.
  Lo que ve el LLM no cambió ni un carácter: una foto congelada de las 16
  tools, 3 ganchos y 3 resultados de tool lo exige
  (`tests/plugins/chats/test_store_domain.py`), y otra prueba frena un
  ejemplo de la tienda que vuelva al código.
- Portavelas lee la despedida de `dom.farewell_order_registered` (sale del
  catálogo la constante con la marca).
- Forge: las 21 reglas `ej-*` se borraron; el clon recibe un dominio
  neutral desde una plantilla.

**F6 hecho (2026-10-02).**
- Un brazo del laboratorio es un bot y, si fija paquete, `@<paquete>`
  (`B@ventas-2`). La forma la valida la plataforma (`labkit.split_arm`,
  `arm_pattern`); qué bots existen, chats (`bots.bot_for_arm`: el bot con
  `bundle`, y el paquete tiene que existir); el plugin `lab` deja pasar la
  forma y chats rechaza un paquete que no está en la imagen (422).
- El proceso del caso fija `SALES_DECISIONS_BUNDLE` ANTES de importar la app
  (`sales_lab.arms.arm_env`, entrypoint), así las capacidades y el
  vocabulario de las tools salen de ese paquete.
- La corrida lo corre como su bot (costo, perfil, arena) y el resumen lo
  compara contra A1 y contra el mismo bot con el paquete de la tienda
  (`B:B@ventas-2`: la diferencia del paquete nuevo).
- Lanzador: si la imagen trae otro paquete, «Paquete a comparar» lo suma
  junto a B; con uno solo, dice cuál corre. Toda la pantalla nombra el brazo
  «Bot nuevo con Jev · paquete ventas-2».

**F7 hecho (2026-10-02).**
- Motor: cada paquete trae su turno (`turn.yaml`), certificado contra el
  vocabulario que declara el catálogo (`turn:` — políticas con los umbrales
  y las preguntas que leen, hechos y sus valores, etapas, datos, tools;
  DB015). Trae el cuestionario de la ráfaga ①, la regla ② de cada asunto,
  las notas de la lectura del hilo, el **contrato asunto → tools como
  filas CEL** (la primera del asunto que se cumple decide; `any_of: []` =
  no pide tool), la **banda de ③ por asunto** (`covered | missing |
  doubt`; el motor las junta), la guía de etapas y ejemplos del contrato y
  de ③. Un `{campo}` de una plantilla que el motor no llena no compila
  (DB005: fallaba en pleno turno); una fila del contrato que nunca se lee,
  tampoco (DB008). Esquema del editor: `decision-turn.schema.json`.
- Ventas: `jev-v5` dice `turn: bundle`; `decisions/turn.py: turn_of(perfil)`
  da al motor, a la sonda y al banco de referencia el cuestionario, la
  política y las tablas del paquete activo. `turno-v1..v3` reciben las
  tablas (`policies/tables.py`); sin ellas (jev-v1…v4) usan sus constantes,
  que quedan como oráculo de la paridad. La traza lleva `bundle` en
  `versions`. `questionnaires/rafaga-v5.yaml` se movió al paquete (foto
  congelada en `tests/fixtures/decisions/rafaga_v5_frozen.json`).
- Paridad: cuestionario byte a byte (estado y preguntas en seis contextos,
  ③ y la respuesta), tablas iguales a las constantes, el turno completo
  (`decide_turn`) igual en más de 8 000 combinaciones de asuntos, etapas,
  lecturas y respuestas en los bordes, el contrato y ③ en grilla, y el motor
  corriendo el turno de otro paquete (`ventas-2`).
- Dos cosas que la certificación dejó a la vista, resueltas el mismo día:
  - `confidence` no la leía ninguna política del turno: el paquete no la
    trae (el certificador rechaza un umbral que nadie lee) y se quitó de los
    perfiles jev-v1…v4 y de `turno-v1` (sin cambio: nadie la leía). Una
    prueba exige que cada perfil y cada política traigan solo los umbrales
    que su código lee (`test_no_profile_or_policy_carries_a_threshold_nobody_reads`).
  - El asunto `promocion` no tenía regla ② desde que llegó (rafaga-v4): nada
    lo daba por atendido dentro del turno, así que en el V1 con motor, cuando
    una tool cortaba el turno, «¿tienen promociones?» pedía una ronda más
    aunque el bot ya las hubiera mostrado. `ventas@1` no se edita (queda
    `promocion: {}`, comentado): **`ventas-2@2`** es `ventas` con esa regla
    (las tools de promociones y cupones, o un texto que hable de
    promociones, descuentos o cupones) y nada más; una prueba lo exige
    (`test_decisions_ventas_2.py`). El laboratorio no lo mide con un brazo:
    el bot B es el V2, que no usa la regla ② (solo el contrato); la prueba
    es determinista sobre la misma función que aplica el workflow V1.
    **Promovido** para el tenant hubara el 2026-10-02
    (`infra/terraform/platform/tenants.auto.tfvars: lab.decisions_bundle =
    "ventas-2"`; el default de la variable sigue en `ventas`, que es lo que
    recibe un clon). Entra con el `terraform apply` de platform y el redeploy
    (render del `.env` desde SSM); volver atrás = `"ventas"`. `ventas-2` no
    viaja a un clon de forge (`deletes`).

**F8 hecho (2026-10-02).**
- El lector de Jev del Order Sentinel tiene su paquete,
  `order_sentinel/agent/decisions/bundles/centinela/` (su propio catálogo
  `builtins.yaml`), sobre el MISMO motor genérico y certificado con
  `decisions check` (lo encuentra junto a los de ventas). Dos capacidades:
  `cambio` (choice «¿qué cambió en el pedido…?» con sus seis opciones;
  decide con certeza ≥ 0,85, una opción fuera de la lista es duda) y
  `evidencia` (ítem por ítem: un sí/no por mensaje nuevo; lo que afirma cada
  cambio —«ya está listo», «la tienda ya recibió o verificó el pago»— son
  variantes del texto por cambio; la tabla junta los mensajes ≥ 0,85 con su
  texto exacto). Los últimos 16 candidatos y los 500/200 caracteres son
  parámetros del paquete (`with:`).
- Sigue en código (builtins, `order_sentinel/agent/decisions/builtins.py`):
  el texto de la conversación que ve Jev, qué mensajes pueden ser evidencia
  (los nuevos; el pago, SOLO del equipo) y, en `readings.py`, la forma del
  veredicto (la del LLM, para que las guardas del grafo no cambien) y lo
  que se tapa.
- Paridad: una foto del lector de antes (`fixtures/order_sentinel/readings_frozen.json`,
  su generador al lado) con lo que pregunta en seis conversaciones, la
  evidencia de cada cambio con tres cortes de lo ya analizado y 744
  lecturas sobre respuestas en los bordes; el lector del paquete la iguala
  carácter por carácter. Otra certeza u otra afirmación en una copia del
  paquete cambian lo que lee (el paquete manda); una certeza nueva sin su
  ejemplo en el borde no certifica.
- El lector sigue apagado por Terraform (`ORDER_SENTINEL_READER=off`): el
  ciclo diario de producción no cambia.

**La paridad es la compuerta de cada fase:** para cada capacidad migrada,
(a) el estado y las preguntas son byte a byte iguales a los de la clase en
entradas representativas, y (b) la decisión coincide sobre una grilla de
respuestas en los bordes de cada umbral (0,14 / 0,15 / 0,16 …), con y sin
respuesta, y con los pisos. Mientras haya paridad, la clase se puede borrar.

## 10.1 Dónde se conecta (revisión del 2026-10-01)

El desacople funciona porque casi todos los lugares le preguntan a Jev por un
**único enchufe**: `decide(capacidad, entrada)` (o `decide_for_session` en las
guardas). A ese enchufe no le importa si la capacidad es una clase o un YAML:
migrar una capacidad la vuelve dato en todos los lugares que la usan, sin
tocarlos.

| Lugar | Cómo le pregunta a Jev | ¿Lo cubre el paquete? |
|---|---|---|
| Ingest (compra, retoma, baja, cortesía, acuse, cupón, fuera de catálogo) | `decide()` | sí (F1–F4) |
| Antes del turno: cantidad (`build_prompt_stage`) | `decide()` | sí (F2) |
| Antes del turno: la ráfaga ① (`rafaga-v5` + `turno_v3`) | directo al puerto (`engine.perceive`) | sí (F7: `turn.yaml`) |
| Dentro de una tool (categoría, color, ítem del pedido, zona de envío) | `decide()` vía `guards` | sí (F2–F3) |
| Egreso: preámbulo, destinatario, rescate, portavelas, saludo + guardas (persona, monto, enumeración, selector, datos, relevo) | `decide()` | sí (F2–F4) |
| Antes de enviar: la verificación ③ | directo al puerto (`verify_questions` + `coverage_decision`) | sí (F7: `turn.yaml`) |
| Después de enviar: afirmación (sombra) | `decide()` | sí (F2) |
| Remarketing (contactar, producto nombrado, fuera de catálogo) | `decide()` vía el enchufe compartido | sí (F2–F4) |
| Abandono (cierre) | `decide()` | sí (F2) |
| Order Sentinel | directo al puerto, con las tablas de su paquete (`centinela`) | sí (F8) |

**Dos huecos que cierra F2:**

1. **Los lugares nombraban la implementación** (`Baja()`,
   `bundled_capability("baja")`). Desde F2 nombran solo la decisión:
   `capability("baja")`. Un único resolutor (`decisions/registry.py`) la
   toma del paquete activo. Desde el premortem del 2026-10-02 ya no cae a la
   clase si el paquete no la trae (corría en silencio la inteligencia de
   velas): el catálogo lista las capacidades que el código pide y el
   certificador exige que el paquete las traiga (DB003). Una prueba prohíbe
   instanciar clases de capacidad fuera del resolutor.
2. **El paquete activo estaba fijo en el código.** Desde F2 viene de la
   configuración de la tienda, igual que el perfil de Jev:
   `tenants.<t>.lab.decisions_bundle` (Terraform, default `ventas`) →
   SSM `SALES_DECISIONS_BUNDLE` → el resolutor. Un paquete configurado que no
   existe falla fuerte (no se corre la inteligencia de otra tienda); una
   prueba exige que el default de Terraform exista en el repo.

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
   versión publicada (su huella está congelada en una prueba).
6. En el `coverage` del turno, `{}` NO significa «no se juzga»: es una regla
   que nunca se cumple, así que el asunto queda siempre sin atender y pide
   otra ronda del LLM (el bug de `promocion` en `ventas@1`). Un asunto lleva
   las tools o las palabras que lo atienden.
7. Los pisos se piden por nombre; nunca se reescriben en el paquete.
8. Un bug de decisión en producción sigue el bucle de
   `docs/motor-de-decisiones/index.html` (§07 y §08): el veredicto dice dónde
   cae el arreglo (pregunta, umbral, fila, builtin o modo) y nunca es un `if`
   en el lugar que decide.

## 13. Premortem del 2026-10-02

Antes de promover `ventas-2`, cuatro revisiones en paralelo (motor y
certificador, runtime de ventas, despliegue, laboratorio y centinela)
buscaron cómo fallaría la solución en producción. La promesa del certificador
es «si compila, corre»; cada hueco quedó con su prueba
(`tests/platform/decisions/test_bundle_premortem.py`,
`tests/infra/test_decision_bundles_gates.py`,
`tests/plugins/test_decision_bundles_published.py` y las de cada capa).

| Cómo fallaba | Qué se hizo |
|---|---|
| CI no certificaba ningún paquete; uno roto llegaba a `main` | `architecture-gates.yml` corre `decisions check` y las suites de paquetes |
| SSM nombra un paquete que la imagen no trae (rollback de imagen): API y worker de ventas caídos, webhook en 502 | el deploy certifica el paquete de SSM dentro de la imagen nueva antes del `up -d` |
| Un paquete roto tumbaba cada mensaje en el ingest, y recompilaba en cada uno | el resolutor guarda el error; el ingest sigue con las reglas del código; la API y los workers precompilan al arrancar (`warm_up`, fuera del event loop) |
| La regla de la baja legal y la del relevo se podían cambiar | `required_rules` del catálogo (DB012) |
| Una fila del contrato del turno que falla apagaba el contrato del asunto | sigue con la fila siguiente; el certificador exige preguntar si la respuesta llegó |
| Llave repetida en el YAML, `criteria` sin comillas («True»), `no:` como opción `false` | DB001 / normalización del cuestionario que viaja |
| Lecturas que el certificador no veía: `(choice)['x']`, `size(p)`, `dom.seccion.campo`, `i.campo` en `items.filter`, opciones o etapas mal escritas | DB005 / DB015 |
| Un error de la tabla quedaba como «duda» de Jev, con log en debug | warning con paquete y fila; el veredicto dice `reason=bundle_error`; un ejemplo que solo pasa por error no certifica |
| Un literal mutable de `then` se compartía entre decisiones | se copia en cada decisión |
| Un builtin que lanza (argumento implícito) tumbaba la guarda | `takes_items` / `keys` en el catálogo (DB004) y el builtin que falla deja `BUNDLE_FAULT` (decide la regla) |
| Una capacidad que faltaba en el paquete corría la clase de Python | el catálogo lista las 29 (DB003); sin respaldo a la clase |
| Métricas y cola de desacuerdos sin paquete ni variante | `bundle` y `variant` en cada fila |
| Una versión publicada se podía editar | huella sha256 congelada por versión; cada versión publicada debe seguir certificando (`SUPPORTED_CONTRACTS`) |
| El oráculo del paquete no se comparaba con nada | DB016 + pruebas de que ventas y centinela usan el oráculo del código |
| El laboratorio corría `ventas` como «el de la tienda» después de promover | la orden de cada corrida lleva `store_bundle`; fijar el mismo paquete de la tienda es 422 |
| Jev sin `probabilities` → el adaptador inventaba 1,0 | la distribución queda vacía y se lee `confidence` |
| La foto del lector del centinela se podía regenerar con el lector nuevo (tautológica) | el generador se niega sin `--regenerar` |
| La evaluación golden siempre corría `ventas` | entrada `bundle` y el reporte dice cuál corrió |
| `{}` en `coverage` documentado al revés | mensaje del certificador y docstring corregidos (regla 6) |

Queda del operador: `tenants.hubara.lab.internal_numbers` (los teléfonos del
equipo, para que el banco del laboratorio no los incluya) y el `apply` +
dispatch de la promoción.
